"""Extract structured profile fields from resume text.

Regex handles the reliable contact basics (email, phone, links); an optional LLM
fills the rest (name, location, current title/company, years of experience). The
`intake` command uses this to pre-fill the questionnaire and only ask for fields
it could not recover from the resume.
"""

from __future__ import annotations

import re

from agent.llm.prompts import RESUME_PROFILE_SYSTEM, RESUME_PROFILE_USER

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
LINK_RE = re.compile(
    r"(?:https?://|www\.)[^\s)>\],]+|(?:linkedin\.com|github\.com)/[^\s)>\],]+",
    re.IGNORECASE,
)

# Keys the LLM is asked to return (flat). Regex overrides the contact ones.
_LLM_KEYS = (
    "full_name", "email", "phone", "city", "state", "country",
    "linkedin", "github", "portfolio",
    "current_title", "current_company", "total_years",
)


def extract_contact(text: str) -> dict:
    """Deterministic extraction of email, phone, and known links."""
    out: dict[str, str] = {}

    email = EMAIL_RE.search(text)
    if email:
        out["email"] = email.group(0)

    phone = _first_phone(text)
    if phone:
        out["phone"] = phone

    out.update(_classify_links(text))
    return out


def extract_profile_fields(text: str, llm=None) -> dict:
    """Best-effort structured fields from resume text.

    Returns a flat dict using the keys in ``_LLM_KEYS``. Missing fields are simply
    absent. Regex-derived contact details take precedence over the LLM.
    """
    fields: dict[str, object] = {}

    if llm is not None and text.strip():
        try:
            data = llm.chat_json(
                RESUME_PROFILE_SYSTEM,
                RESUME_PROFILE_USER.format(resume=text[:6000]),
            )
            for key in _LLM_KEYS:
                val = data.get(key)
                if val not in (None, "", []):
                    fields[key] = val
        except Exception:
            pass

    # Regex wins for the things it can find reliably.
    fields.update(extract_contact(text))
    return fields


def _first_phone(text: str) -> str | None:
    for m in re.finditer(r"[\+(]?\d[\d\s().\-]{7,}\d", text):
        cand = m.group(0).strip()
        digits = re.sub(r"\D", "", cand)
        if 10 <= len(digits) <= 15:
            return cand
    return None


def _classify_links(text: str) -> dict:
    out: dict[str, str] = {}
    for raw in LINK_RE.findall(text):
        url = raw.rstrip(".,;)")
        low = url.lower()
        if "linkedin.com" in low:
            out.setdefault("linkedin", url)
        elif "github.com" in low:
            out.setdefault("github", url)
        elif low.startswith(("http", "www.")):
            out.setdefault("portfolio", url)
    return out
