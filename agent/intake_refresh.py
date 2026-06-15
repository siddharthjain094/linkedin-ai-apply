"""Refresh intake.yaml from the master resume (non-interactive intake merge)."""

from __future__ import annotations

import yaml

from agent.config import Settings
from agent.llm.provider import LLMClient, llm_is_configured
from agent.resume.parser import extract_text
from agent.resume.profile import extract_profile_fields


def merge_intake_from_resume(settings: Settings) -> dict:
    """Re-parse the master resume and merge recovered fields into intake.yaml.

    Mimics the resume-parsing step of ``linkedin-apply intake`` without re-prompting
    the user. Existing screening answers, EEO, and eligibility are preserved; only
    personal/links/experience fields are updated when the resume provides values.
    """
    resume = settings.resolve_master_resume()
    if not resume.exists():
        return {"ok": False, "error": "master resume not found"}

    try:
        text = extract_text(resume)
    except Exception as exc:
        return {"ok": False, "error": f"could not read resume: {exc}"}

    llm = None
    if llm_is_configured(settings):
        try:
            llm = LLMClient(settings)
        except Exception:
            llm = None

    fields = extract_profile_fields(text, llm)
    if not fields:
        return {"ok": False, "error": "no fields extracted from resume"}

    intake_path = settings.intake_file
    existing: dict = {}
    if intake_path.exists():
        with intake_path.open(encoding="utf-8") as fh:
            existing = yaml.safe_load(fh) or {}

    personal = existing.setdefault("personal", {})
    links = existing.setdefault("links", {})
    experience = existing.setdefault("experience", {})

    for key in ("full_name", "email", "phone", "city", "state", "country"):
        if fields.get(key) not in (None, ""):
            personal[key] = fields[key]
    for key in ("linkedin", "github", "portfolio"):
        if fields.get(key) not in (None, ""):
            links[key] = fields[key]
    for key in ("current_title", "current_company"):
        if fields.get(key) not in (None, ""):
            experience[key] = fields[key]
    if fields.get("total_years") not in (None, ""):
        try:
            experience["total_years"] = int(fields["total_years"])
        except (TypeError, ValueError):
            pass

    if experience.get("total_years") is not None:
        sa = existing.setdefault("screening_answers", {})
        sa["years of experience"] = str(experience["total_years"])

    intake_path.parent.mkdir(parents=True, exist_ok=True)
    with intake_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(existing, fh, sort_keys=False, allow_unicode=True)

    return {"ok": True, "fields_merged": len(fields), "intake_path": str(intake_path)}
