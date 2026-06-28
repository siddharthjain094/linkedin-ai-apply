"""Scoring: LLM-score unscored jobs vs the resume (informational + auto-approve)."""

from __future__ import annotations

from typing import Callable, Optional

from rich.console import Console

from agent.config import Settings
from agent.db import Database
from agent.llm.prompts import SCORING_SYSTEM, SCORING_USER
from agent.llm.provider import LLMClient, LLMUnavailableError
from agent.models import Status
from agent.resume.parser import MIN_RESUME_CHARS, cached_resume_text, resume_text_looks_valid

# If this many LLM calls fail back-to-back we treat it as systemic (bad key,
# wrong model, provider down) and abort loudly instead of zeroing every job.
_MAX_CONSECUTIVE_LLM_FAILURES = 3

console = Console()


def _blacklisted(job, settings: Settings) -> str | None:
    title = (job.title or "").lower()
    company = (job.company or "").lower()
    for c in settings.blacklist_companies:
        if c and c.lower() in company:
            return f"blacklisted company: {c}"
    for kw in settings.blacklist_title_keywords:
        if kw and kw.lower() in title:
            return f"blacklisted title keyword: {kw}"
    return None


def _parse_score(value) -> int:
    """Coerce an LLM-returned score into a 0-100 int, robust to junk."""
    if value is None:
        return 0
    try:
        return max(0, min(100, int(float(str(value).strip().rstrip("%")))))
    except (TypeError, ValueError):
        return 0


def score_jobs(
    settings: Settings,
    db: Database,
    llm: LLMClient,
    should_stop: Optional[Callable[[], bool]] = None,
    job_ids: list[str] | None = None,
) -> dict:
    resume_path = settings.resolve_master_resume()
    try:
        resume = cached_resume_text(str(resume_path))
    except Exception as exc:
        console.log(f"[red]Could not read master resume ({exc}); cannot score jobs. "
                    f"Set MASTER_RESUME_PATH / drop your resume and retry.[/]")
        db.log_run("score", errors=1, notes=f"resume unreadable: {exc}")
        return {"scored": 0, "skipped": 0, "error": str(exc)}

    if not resume_text_looks_valid(resume):
        msg = (
            f"Resume at {resume_path} yielded only {len(resume.strip())} characters of text "
            f"(need at least {MIN_RESUME_CHARS}). PDF text extraction often fails on "
            "image-only or scanned resumes — try uploading a .docx or text-based PDF, "
            "or run `linkedin-apply resume-check` for details."
        )
        console.log(f"[red]{msg}[/]")
        db.log_run("score", errors=1, notes=msg)
        return {"scored": 0, "skipped": 0, "error": msg, "resume_chars": len(resume.strip())}

    pending = db.jobs_by_ids(job_ids) if job_ids is not None else db.needs_scoring()
    scored = skipped = errors = auto_approved = 0
    consecutive_failures = 0
    stopped = False

    try:
        for job in pending:
            if should_stop and should_stop():
                stopped = True
                break
            reason = _blacklisted(job, settings)
            if reason:
                db.update(job.job_id, status=Status.skipped.value, match_score=0,
                          match_reasons=reason)
                skipped += 1
                continue

            try:
                result = llm.chat_json(
                    SCORING_SYSTEM,
                    SCORING_USER.format(
                        resume=resume[:8000],
                        title=job.title,
                        company=job.company,
                        location=job.location,
                        description=(job.description or "")[:6000],
                    ),
                )
                consecutive_failures = 0
            except Exception as exc:
                errors += 1
                consecutive_failures += 1
                db.update(
                    job.job_id,
                    match_reasons=f"scoring error: {exc}",
                )
                if consecutive_failures >= _MAX_CONSECUTIVE_LLM_FAILURES:
                    raise LLMUnavailableError(
                        f"LLM failed {consecutive_failures} times in a row "
                        f"({exc}). Check LLM_API_KEY / LLM_MODEL, then retry.") from exc
                continue

            score = _parse_score(result.get("score"))
            reasons = str(result.get("reasons") or "")
            fields: dict = {"match_score": score, "match_reasons": reasons}
            if settings.auto_approve_on_score and score >= settings.match_threshold:
                fields["approved"] = True
                auto_approved += 1
            db.update(job.job_id, **fields)
            scored += 1
    finally:
        db.log_run(
            "score",
            skipped=skipped,
            errors=errors,
            notes=(
                f"{scored} scored, {auto_approved} auto-approved (≥{settings.match_threshold}), "
                f"resume_chars={len(resume.strip())}"
                + (", stopped early" if stopped else "")
            ),
        )

    note = " (stopped early)" if stopped else ""
    console.log(
        f"Scored {scored + skipped}/{len(pending)} jobs: "
        f"{scored} scored, {auto_approved} auto-approved (≥{settings.match_threshold}), "
        f"{skipped} blacklisted.{note}")
    return {
        "scored": scored,
        "skipped": skipped,
        "errors": errors,
        "auto_approved": auto_approved,
        "stopped": stopped,
        "resume_chars": len(resume.strip()),
    }
