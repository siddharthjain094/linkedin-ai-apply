"""AI-driven applier for external portals / ATS pages and hiring posts.

A bounded agentic loop: snapshot the page's interactive elements, ask the LLM for
the single next action, execute it, repeat. Stops and returns "human_review" the
moment it hits a captcha, login wall, OTP, or a required field it cannot fill.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent.browser.finders import human_delay
from agent.browser.forms import flatten_profile
from agent.config import Settings
from agent.llm.prompts import EXTERNAL_PLAN_SYSTEM, EXTERNAL_PLAN_USER
from agent.llm.provider import LLMClient

MAX_STEPS = 12
BLOCK_SIGNS = ["captcha", "recaptcha", "hcaptcha", "verify you are human",
               "enter the code", "one-time", "two-factor", "log in to continue",
               "sign in to continue"]


def external_apply(
    session,
    settings: Settings,
    job: dict,
    intake: dict,
    llm: LLMClient | None,
    resume_path: Path | None,
) -> tuple[str, str, list[dict]]:
    if llm is None:
        return "human_review", "External apply needs an LLM; none configured.", []

    page = session.page
    target = job.get("url") or ""
    if not target or "linkedin.com" in target and job.get("source") == "hiring_post" \
            and not _looks_like_apply_link(target):
        return "human_review", "No actionable external apply link found in post.", []

    try:
        page.goto(target, wait_until="domcontentloaded")
        human_delay(settings.min_delay_ms, settings.max_delay_ms)
    except Exception as exc:
        return "human_review", f"Could not open external page: {exc}", []

    profile = json.dumps(flatten_profile(intake), default=str)

    for step in range(MAX_STEPS):
        if _is_blocked(page):
            _screenshot(settings, job, page)
            return "human_review", "Blocked by captcha/login/OTP on external site.", []

        elements = _snapshot(page)
        if not elements:
            return "human_review", "No interactive elements detected on external page.", []

        try:
            plan = llm.chat_json(
                EXTERNAL_PLAN_SYSTEM,
                EXTERNAL_PLAN_USER.format(
                    profile=profile,
                    url=page.url,
                    elements=_render_elements(elements),
                ),
            )
        except Exception as exc:
            return "human_review", f"Planner failed: {exc}", []

        action = (plan.get("action") or "human_review").lower()
        idx = plan.get("target_index")
        value = plan.get("value")

        if action == "human_review":
            _screenshot(settings, job, page)
            reason = plan.get("reason", "AI requested human review.")
            question = plan.get("question") or plan.get("missing")
            needs = [{"question": str(question), "options": [], "type": "text"}] \
                if question else []
            return "human_review", reason, needs
        if action == "finish":
            if settings.dry_run:
                return "human_review", "DRY_RUN: stopped before external submit.", []
            return "applied", "Submitted via external site (AI).", []

        ok = _execute(page, action, idx, value, elements, resume_path, settings)
        if not ok:
            _screenshot(settings, job, page)
            return "human_review", f"Action '{action}' could not be completed.", []
        human_delay(settings.min_delay_ms, settings.max_delay_ms)

    _screenshot(settings, job, page)
    return "human_review", "External apply exceeded step budget.", []


# ---- page understanding ----------------------------------------------------

def _snapshot(page) -> list[dict]:
    elements: list[dict] = []
    selectors = "input, textarea, select, button, a[href]"
    nodes = page.locator(selectors)
    count = min(nodes.count(), 60)
    for i in range(count):
        try:
            el = nodes.nth(i)
            if not el.is_visible():
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            etype = el.get_attribute("type") or ""
            label = (
                el.get_attribute("aria-label")
                or el.get_attribute("placeholder")
                or el.get_attribute("name")
                or (el.inner_text(timeout=500) if tag in {"button", "a"} else "")
                or ""
            ).strip()[:80]
            elements.append({"index": len(elements), "tag": tag, "type": etype,
                             "label": label, "_loc": el})
        except Exception:
            continue
    return elements


def _render_elements(elements: list[dict]) -> str:
    lines = []
    for e in elements:
        lines.append(f"[{e['index']}] <{e['tag']} type={e['type']}> {e['label']}")
    return "\n".join(lines)


def _is_blocked(page) -> bool:
    try:
        body = (page.inner_text("body", timeout=2000) or "").lower()
    except Exception:
        return False
    return any(sign in body for sign in BLOCK_SIGNS)


def _looks_like_apply_link(url: str) -> bool:
    return any(k in url.lower() for k in ("apply", "job", "career", "greenhouse",
                                          "lever", "workday", "ashby"))


# ---- action execution ------------------------------------------------------

def _execute(page, action, idx, value, elements, resume_path, settings) -> bool:
    if action == "upload_resume":
        if not resume_path:
            return False
        try:
            page.locator("input[type='file']").first.set_input_files(str(resume_path))
            return True
        except Exception:
            return False

    if idx is None or idx >= len(elements):
        return False
    loc = elements[idx]["_loc"]
    try:
        if action == "fill":
            loc.fill(str(value or ""))
            return True
        if action == "select":
            loc.select_option(label=str(value))
            return True
        if action == "click":
            loc.click()
            return True
    except Exception:
        return False
    return False


def _screenshot(settings: Settings, job: dict, page) -> None:
    try:
        out = settings.output_path / "screenshots" / f"{job['job_id']}_external.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out), full_page=True)
    except Exception:
        pass
