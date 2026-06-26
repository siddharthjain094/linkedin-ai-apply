"""Generate tailored resume + cover letter documents.

Documents are produced as editable **.docx** (the source of truth the user
reviews and may hand-edit). Conversion to the final upload format (PDF) is
deferred to apply time via ``finalize_resume_for_upload`` so any manual edits the
user makes between review and apply are honored."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from rich.console import Console

from agent.config import Settings
from agent.db import Database
from agent.llm.prompts import (
    COVER_LETTER_SYSTEM,
    COVER_LETTER_USER,
    RESUME_TAILOR_SYSTEM,
    RESUME_TAILOR_USER,
)
from agent.llm.provider import LLMClient
from agent.models import Status
from agent.resume import builder
from agent.resume.parser import cached_resume_text

console = Console()


def _contact_line(intake: dict) -> tuple[str, str]:
    personal = intake.get("personal") or {}
    name = personal.get("full_name", "")
    bits = [personal.get("email"), personal.get("phone"),
            ", ".join(x for x in [personal.get("city"), personal.get("state")] if x)]
    links = intake.get("links") or {}
    bits += [links.get("linkedin"), links.get("github")]
    contact = " | ".join(b for b in bits if b)
    return name, contact


def generate_documents(settings: Settings, llm: LLMClient, job) -> tuple[str, str]:
    """Returns (resume_path, cover_letter_path) as strings ('' if a step failed)."""
    try:
        resume_text = cached_resume_text(str(settings.resolve_master_resume()))
    except Exception:
        # No readable resume -> we can't tailor documents; the apply phase will
        # fall back to uploading the master resume (if any) without tailoring.
        return "", ""
    intake = settings.intake
    name, contact = _contact_line(intake)
    base = builder.job_basename(job.company, job.title)

    # --- tailored resume ---
    resume_out = ""
    try:
        tailored = llm.chat_json(
            RESUME_TAILOR_SYSTEM,
            RESUME_TAILOR_USER.format(
                resume=resume_text[:9000],
                title=job.title,
                company=job.company,
                description=(job.description or "")[:6000],
            ),
        )
        docx_path = settings.output_path / "resumes" / f"{base}.docx"
        builder.build_resume_docx(
            name=name or "Candidate",
            contact_line=contact,
            summary=str(tailored.get("summary", "")),
            bullets=[str(b) for b in (tailored.get("bullets") or [])],
            skills=[str(s) for s in (tailored.get("highlighted_skills") or [])],
            base_resume_text=resume_text,
            out_path=docx_path,
        )
        # Keep the editable .docx as the stored artifact; PDF is rendered at apply.
        resume_out = str(docx_path)
    except Exception:
        resume_out = ""

    # --- cover letter ---
    cover_out = ""
    try:
        body = llm.chat(
            COVER_LETTER_SYSTEM,
            COVER_LETTER_USER.format(
                resume=resume_text[:9000],
                name=name or "Candidate",
                title=job.title,
                company=job.company,
                location=job.location,
                description=(job.description or "")[:6000],
            ),
        )
        cover_docx = settings.output_path / "cover_letters" / f"{base}.docx"
        builder.build_cover_letter_docx(name or "Candidate", body, cover_docx)
        cover_out = str(cover_docx)
    except Exception:
        cover_out = ""

    return resume_out, cover_out


def finalize_resume_for_upload(settings: Settings, resume_path: str) -> Path | None:
    """Produce the file to actually upload from the (possibly hand-edited) source.

    The stored artifact is an editable .docx. If the output format is PDF we
    convert the current docx now, so edits made during review are picked up. Falls
    back to the docx itself if conversion isn't available, then to None."""
    if not resume_path:
        return None
    p = Path(resume_path)
    if not p.exists():
        return None
    if settings.resume_output_format == "pdf" and p.suffix.lower() == ".docx":
        pdf = builder.to_pdf(p)
        if pdf:
            return pdf
    return p


def master_resume_for_upload(settings: Settings) -> Path | None:
    """Return the user's master resume in the format needed for upload."""
    master = settings.resolve_master_resume()
    if not master.exists():
        return None
    if settings.resume_output_format == "pdf" and master.suffix.lower() == ".docx":
        pdf = builder.to_pdf(master)
        if pdf:
            return pdf
    return master


def generate_all(
    settings: Settings,
    db: Database,
    llm: LLMClient,
    regenerate: bool = False,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """Generate tailored documents for every queued (above-threshold) job.

    This is the standalone 'draft everything for review' step. It does NOT touch
    the browser or submit anything. Already-generated jobs are skipped (so a
    user's hand-edits are never clobbered) unless ``regenerate`` is set.
    """
    candidates = list(db.pending_for_apply())
    made = skipped = failed = 0
    stopped = False
    for job in candidates:
        if should_stop and should_stop():
            stopped = True
            break
        has_docs = bool(job.resume_path) and Path(job.resume_path).exists()
        if has_docs and not regenerate:
            skipped += 1
            continue
        resume_path, cover_path = generate_documents(settings, llm, job)
        if resume_path:
            # Regenerating resets approval: the content the user signed off on changed.
            db.update(
                job.job_id,
                resume_path=resume_path,
                cover_letter_path=cover_path,
                status=Status.generated.value,
                approved=False,
            )
            made += 1
            console.log(f"[green]Drafted[/]: {job.title} @ {job.company}")
        else:
            failed += 1
            console.log(f"[yellow]Could not draft[/]: {job.title} @ {job.company}")
    db.log_run("generate", errors=failed,
               notes=f"{made} drafted, {skipped} skipped"
                     + (", stopped early" if stopped else ""))
    return {"generated": made, "skipped_existing": skipped, "failed": failed,
            "stopped": stopped}
