"""Screening-question answering + generic form-field fillers for Easy Apply."""

from __future__ import annotations

import json
from typing import Any

from agent.llm.prompts import SCREENING_SYSTEM, SCREENING_USER
from agent.llm.provider import LLMClient


def flatten_profile(intake: dict) -> dict:
    """Flatten intake.yaml into a compact profile for the LLM + lookups."""
    flat: dict[str, Any] = {}
    for section in ("personal", "links", "eligibility", "compensation", "experience", "eeo"):
        for k, v in (intake.get(section) or {}).items():
            flat[k] = v
    return flat


def answer_from_intake(question: str, intake: dict) -> str | None:
    """Match the question against intake.screening_answers by substring."""
    q = question.lower().strip()
    answers = intake.get("screening_answers") or {}
    for key, value in answers.items():
        if key.lower() in q:
            return str(value)
    # EEO fall-throughs.
    eeo = intake.get("eeo") or {}
    for key, value in eeo.items():
        if key.replace("_", " ") in q:
            return str(value)
    return None


def resolve_answer(
    question: str,
    options: list[str] | None,
    intake: dict,
    llm: LLMClient | None,
) -> str | None:
    """intake first, then LLM. Returns None if unanswerable (-> human review)."""
    direct = answer_from_intake(question, intake)
    if direct is not None:
        coerced = _coerce_to_option(direct, options)
        if coerced is not None:
            return coerced
        # We had an intake answer but couldn't map it onto the offered options;
        # fall through to the LLM rather than guessing wrong.

    if llm is None:
        return None

    options_hint = f"OPTIONS: {options}" if options else ""
    try:
        result = llm.chat_json(
            SCREENING_SYSTEM,
            SCREENING_USER.format(
                profile=json.dumps(flatten_profile(intake), default=str),
                question=question,
                options_hint=options_hint,
            ),
        )
    except Exception:
        return None

    answer = result.get("answer")
    if answer is None or str(answer).strip() == "":
        return None
    return _coerce_to_option(str(answer), options)


def _coerce_to_option(answer: str, options: list[str] | None) -> str | None:
    """Map a free-text answer onto one of a select/radio's options.

    Returns None when the answer can't be confidently mapped, so the caller parks
    the job for human review instead of silently submitting an arbitrary (often
    wrong) option."""
    if not options:
        return answer
    a = answer.lower().strip()
    for opt in options:
        if opt.lower().strip() == a:
            return opt
    for opt in options:
        if a and (a in opt.lower() or opt.lower() in a):
            return opt
    # Yes/No normalisation.
    if a in {"yes", "true"}:
        for opt in options:
            if opt.lower().startswith("yes"):
                return opt
    if a in {"no", "false"}:
        for opt in options:
            if opt.lower().startswith("no"):
                return opt
    return None  # unmappable -> let the caller route to human review
