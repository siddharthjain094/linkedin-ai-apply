"""APPLY phase: generate docs and submit, honoring SUBMIT_MODE + DRY_RUN."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console
from sqlalchemy import select

from agent.browser.job_page import is_linkedin_job_url
from agent.browser.easy_apply import easy_apply
from agent.browser.external_apply import external_apply
from agent.browser.session import LoggedOutError
from agent.config import Settings, SubmitMode
from agent.db import Database
from agent.llm.provider import LLMClient
from agent.models import Job, Status, TERMINAL_STATUSES
from agent.pipeline.generate import (
    finalize_resume_for_upload,
    generate_documents,
    master_resume_for_upload,
)

console = Console()


def run_apply(
    session,
    settings: Settings,
    db: Database,
    llm: LLMClient | None,
    only_approved: bool = False,
    job_ids: list[str] | None = None,
    regenerate: bool = False,
    skip_generate: bool = False,
    should_stop: Optional[Callable[[], bool]] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> dict:
    selected = list(dict.fromkeys(job_ids or []))
    if selected:
        retried = db.resolve_human_review_for(selected)
        if retried and progress:
            progress(f"Retrying {retried} selected job(s) from human review")
    elif only_approved:
        retried = db.resolve_approved_human_review()
        if retried and progress:
            progress(f"Retrying {retried} approved job(s) from human review")

    candidates = list(db.pending_for_apply())
    if selected:
        wanted = set(selected)
        candidates = [j for j in candidates if j.job_id in wanted]
    elif only_approved:
        candidates = [j for j in candidates if j.approved]

    stats = {"applied": 0, "human_review": 0, "errors": 0, "generated": 0,
             "skipped": 0, "closed": 0, "stopped": False, "targeted": len(selected)}
    submitted = 0

    if not candidates:
        msg = _explain_no_candidates(db, settings, only_approved, selected)
        stats["message"] = msg
        console.log(f"[yellow]{msg}[/]")
        db.log_run("apply", notes=msg)
        return stats

    if progress:
        progress(f"Applying to {len(candidates)} job(s) — watch the Chrome window the tool opened")
    if skip_generate and progress:
        progress("Skipping draft generation — applying with master resume (or existing tailored files)")
    if settings.dry_run and progress:
        progress("DRY_RUN is on: forms fill but final submit is skipped")

    try:
        for job in candidates:
            # Stop BEFORE touching the next job so we never submit after a stop.
            if should_stop and should_stop():
                stats["stopped"] = True
                console.log("[yellow]Stop requested - halting before next application.[/]")
                break
            # Abort cleanly if the LinkedIn session ended (don't fail each job).
            if session is not None and session.logged_out():
                raise LoggedOutError(
                    "LinkedIn session ended mid-run (logged out / auth wall). "
                    "Re-run `linkedin-apply login`, then try again.")
            if submitted >= settings.max_applies_per_run:
                console.log(f"Reached MAX_APPLIES_PER_RUN ({settings.max_applies_per_run}).")
                break

            label = f"{job.title} @ {job.company}".strip(" @")
            if progress:
                progress(f"Opening job: {label}")
            _process_one(session, settings, db, llm, job, regenerate, stats, skip_generate=skip_generate)
            if stats.pop("_just_submitted", False):
                submitted += 1
    finally:
        notes = [f"{stats['closed']} closed"] if stats["closed"] else []
        if stats["stopped"]:
            notes.append("stopped early")
        db.log_run(
            "apply", applied=stats["applied"], review=stats["human_review"],
            errors=stats["errors"], notes=", ".join(notes))

    return stats


def _process_one(session, settings, db, llm, job, regenerate, stats, *, skip_generate=False) -> None:
    """Generate (if needed), route, submit, and record outcome for one job."""
    # 1) Reuse already-generated docs (preserving manual edits); else draft now.
    resume_path = job.resume_path or ""
    has_docs = bool(resume_path) and Path(resume_path).exists()
    if skip_generate:
        regenerate = False
    elif (not has_docs or regenerate) and llm is not None:
        resume_path, cover_path = generate_documents(settings, llm, job)
        db.update(job.job_id, resume_path=resume_path, cover_letter_path=cover_path,
                  status=Status.generated.value)
        stats["generated"] += 1

    use_master = job.use_master_resume or (skip_generate and not has_docs)
    upload = (
        master_resume_for_upload(settings)
        if use_master
        else _resume_for_upload(settings, resume_path)
    )
    if use_master and upload is None:
        reason = (
            "master resume file not found (scheduled apply skips draft generation)."
            if skip_generate and not job.use_master_resume
            else "use_master_resume set but master resume file not found."
        )
        _park_for_review(db, job, reason, [])
        stats["human_review"] += 1
        console.log(f"[yellow]Human review[/]: {job.title} @ {job.company} - "
                    "master resume missing")
        return

    apply_type = job.apply_type or "unknown"
    job_url = job.url or ""
    linkedin_job = is_linkedin_job_url(job_url)

    if settings.submit_mode == SubmitMode.review:
        _park_for_review(db, job, "review mode: documents drafted, submit manually.", [])
        stats["human_review"] += 1
        return

    # apply_type is detected at discovery time but can be stale/wrong. LinkedIn jobs
    # always try Easy Apply first — many tagged "external" are still Easy Apply.
    def _go_external() -> tuple[str, str, list]:
        if settings.submit_mode == SubmitMode.easy_only:
            _park_for_review(
                db, job, "easy_only mode: external application queued for you.", [])
            return "_parked", "", []
        return external_apply(
            session, settings, job.as_row(), settings.intake, llm, upload)

    status, notes, needs_input = "", "", []

    if apply_type == "post":
        status, notes, needs_input = _go_external()
    elif linkedin_job or apply_type in ("easy", "unknown", "external"):
        status, notes, needs_input = easy_apply(
            session, settings, job.as_row(), settings.intake, llm, upload)
        if status == "not_easy":
            status, notes, needs_input = _go_external()
    else:
        status, notes, needs_input = easy_apply(
            session, settings, job.as_row(), settings.intake, llm, upload)
        if status == "not_easy":
            status, notes, needs_input = _go_external()

    if status == "_parked":
        stats["human_review"] += 1
        return

    # Job is closed (no longer accepting applications) -> terminal, never retry.
    if status == Status.closed.value:
        db.update(job.job_id, status=Status.closed.value,
                  notes=notes or "No longer accepting applications.")
        stats["closed"] += 1
        console.log(f"[dim]Closed[/]: {job.title} @ {job.company} - "
                    "no longer accepting applications")
        return

    # Record outcome.
    if status == Status.human_review.value:
        _park_for_review(db, job, notes, needs_input)
        stats["human_review"] += 1
        console.log(f"[yellow]Human review[/]: {job.title} @ {job.company} - {notes}")
        return

    db.update(job.job_id, status=status, notes=notes)
    if status == Status.applied.value:
        stats["applied"] += 1
        stats["_just_submitted"] = True
        # Clear any stale review flags now that it's submitted.
        db.update(job.job_id, needs_input="", review_resolved=False)
        console.log(f"[green]Applied[/]: {job.title} @ {job.company}")
    else:
        stats["errors"] += 1
        console.log(f"[red]Error[/]: {job.title} @ {job.company} - {notes}")


def _park_for_review(db: Database, job, notes: str, needs_input: list[dict]) -> None:
    """Leave a job in human_review with structured questions, awaiting the user.

    review_resolved is reset to False so the job is skipped on future runs until
    the user resolves it (via `linkedin-apply review` or the sheet)."""
    db.update(
        job.job_id,
        status=Status.human_review.value,
        notes=notes,
        needs_input=json.dumps(needs_input) if needs_input else "",
        review_resolved=False,
    )


def _resume_for_upload(settings: Settings, generated: str) -> Path | None:
    # Render the final upload file from the (possibly edited) generated docx.
    upload = finalize_resume_for_upload(settings, generated)
    if upload is not None:
        return upload
    master = settings.resolve_master_resume()
    return master if master.exists() else None


def _explain_no_candidates(
    db: Database,
    settings: Settings,
    only_approved: bool,
    job_ids: list[str] | None = None,
) -> str:
    if job_ids:
        return (
            f"None of the {len(job_ids)} selected job(s) are eligible "
            "(already applied/skipped/closed, or still blocked in human review)."
        )
    if not only_approved:
        return "No jobs matched apply filters (status)."
    with db.session() as s:
        approved = list(
            s.execute(select(Job).where(Job.approved.is_(True))).scalars().all()
        )
    terminal = {st.value for st in TERMINAL_STATUSES}
    active = [j for j in approved if j.status not in terminal]
    if not active:
        return "No approved jobs to apply to — approve jobs in the grid first."
    return "No eligible approved jobs right now (check status filters)."
