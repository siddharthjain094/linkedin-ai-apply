"""APPLY phase: generate docs and submit, honoring SUBMIT_MODE + DRY_RUN."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console

from agent.browser.easy_apply import easy_apply
from agent.browser.external_apply import external_apply
from agent.browser.session import LoggedOutError
from agent.config import Settings, SubmitMode
from agent.db import Database
from agent.llm.provider import LLMClient
from agent.models import Status
from agent.pipeline.generate import finalize_resume_for_upload, generate_documents

console = Console()


def run_apply(
    session,
    settings: Settings,
    db: Database,
    llm: LLMClient | None,
    only_approved: bool = False,
    regenerate: bool = False,
    should_stop: Optional[Callable[[], bool]] = None,
) -> dict:
    candidates = [
        j for j in db.pending_for_apply()
        if (j.match_score or 0) >= settings.match_threshold
    ]
    if only_approved:
        # Review-gated flow: act only on jobs the user explicitly signed off on.
        candidates = [j for j in candidates if j.approved]
    stats = {"applied": 0, "human_review": 0, "errors": 0, "generated": 0,
             "skipped": 0, "closed": 0, "stopped": False}
    submitted = 0

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

            _process_one(session, settings, db, llm, job, regenerate, stats)
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


def _process_one(session, settings, db, llm, job, regenerate, stats) -> None:
    """Generate (if needed), route, submit, and record outcome for one job."""
    # 1) Reuse already-generated docs (preserving manual edits); else draft now.
    resume_path = job.resume_path or ""
    has_docs = bool(resume_path) and Path(resume_path).exists()
    if (not has_docs or regenerate) and llm is not None:
        resume_path, cover_path = generate_documents(settings, llm, job)
        db.update(job.job_id, resume_path=resume_path, cover_letter_path=cover_path,
                  status=Status.generated.value)
        stats["generated"] += 1

    upload = _resume_for_upload(settings, resume_path)
    apply_type = job.apply_type or "unknown"

    if settings.submit_mode == SubmitMode.review:
        _park_for_review(db, job, "review mode: documents drafted, submit manually.", [])
        stats["human_review"] += 1
        return

    # apply_type is detected at discovery time but can be stale/unknown, so we
    # don't fully trust it. "post" is unambiguously external; everything else
    # (easy/unknown) tries Easy Apply first and falls back to the external path.
    def _go_external() -> tuple[str, str, list]:
        if settings.submit_mode == SubmitMode.easy_only:
            _park_for_review(
                db, job, "easy_only mode: external application queued for you.", [])
            return "_parked", "", []
        return external_apply(
            session, settings, job.as_row(), settings.intake, llm, upload)

    if apply_type == "post":
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
    master = settings.master_resume_file
    return master if master.exists() else None
