"""AI-driven applier for external portals / ATS pages and hiring posts.

A bounded agentic loop: read the visible page (text + interactive elements),
ask the LLM for the next action (fill / click / scroll / upload / finish), execute,
repeat. Handles LinkedIn → external-site handoff (click Apply, follow new tabs).
Stops on captcha/login walls or unmappable required fields.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from agent.browser.finders import human_delay
from agent.browser.forms import flatten_profile
from agent.browser.job_page import (
    find_apply_button_anywhere,
    follow_external_apply,
    is_linkedin_job_url,
    posting_closed,
    safe_close_page,
    wait_for_job_page,
)
from agent.config import Settings
from agent.llm.prompts import EXTERNAL_PLAN_SYSTEM, EXTERNAL_PLAN_USER
from agent.llm.provider import LLMClient

logger = logging.getLogger(__name__)

MAX_STEPS = 24
BLOCK_SIGNS = [
    "captcha", "recaptcha", "hcaptcha", "verify you are human",
    "enter the code", "one-time", "two-factor", "log in to continue",
    "sign in to continue",
]


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
    if not target:
        return "human_review", "No apply URL for this job.", []

    if "linkedin.com" in target and job.get("source") == "hiring_post" \
            and not _looks_like_apply_link(target):
        return "human_review", "No actionable external apply link found in post.", []

    on_ats = not is_linkedin_job_url(page.url or "")
    if not on_ats:
        try:
            if hasattr(session, "navigate"):
                session.navigate(target, progress=getattr(session, "_progress", None))
            else:
                page.goto(target, wait_until="domcontentloaded")
                human_delay(settings.min_delay_ms, settings.max_delay_ms)
        except Exception as exc:
            return "human_review", f"Could not open apply page: {exc}", []

    # LinkedIn job views need an extra click to reach the company ATS.
    if is_linkedin_job_url(target) or is_linkedin_job_url(page.url):
        wait_for_job_page(page)
        old_linkedin_page = page
        page, early_status, early_notes = follow_external_apply(
            page, settings, job.get("job_id", ""))
        if early_status:
            return early_status, early_notes, []
        # Close the old LinkedIn job page to prevent tab accumulation across jobs.
        if old_linkedin_page is not page:
            safe_close_page(old_linkedin_page)
        session.page = page

    if posting_closed(page):
        return "closed", "No longer accepting applications.", []

    profile = json.dumps(flatten_profile(intake), default=str)
    job_ctx = f"{job.get('title', '')} @ {job.get('company', '')}".strip()

    for step in range(MAX_STEPS):
        if _is_blocked(page):
            _screenshot(settings, job, page)
            return "human_review", "Blocked by captcha/login/OTP on external site.", []

        if posting_closed(page):
            return "closed", "No longer accepting applications.", []

        # Step 0: try an obvious Apply / Apply now CTA before asking the LLM.
        if step == 0:
            cta = find_apply_button_anywhere(page)
            if cta is not None:
                try:
                    cta.scroll_into_view_if_needed(timeout=3000)
                    cta.click(timeout=8000)
                    human_delay(settings.min_delay_ms, settings.max_delay_ms)
                except Exception:
                    pass

        elements = _snapshot(page)
        if not elements:
            _scroll_page(page)
            human_delay(settings.min_delay_ms, settings.max_delay_ms)
            elements = _snapshot(page)
        if not elements:
            return "human_review", "No interactive elements detected on apply page.", []

        try:
            plan = llm.chat_json(
                EXTERNAL_PLAN_SYSTEM,
                EXTERNAL_PLAN_USER.format(
                    profile=profile,
                    job=job_ctx,
                    url=page.url,
                    page_text=_page_summary(page),
                    elements=_render_elements(elements),
                    step=step + 1,
                    max_steps=MAX_STEPS,
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

def _page_summary(page, max_chars: int = 3500) -> str:
    """Visible page text so the planner can read headings/instructions."""
    for sel in ("main", "[role='main']", "article", "body"):
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            text = (loc.inner_text(timeout=2500) or "").strip()
            if len(text) > 120:
                return text[:max_chars]
        except Exception:
            continue
    return ""


def _scroll_page(page) -> None:
    try:
        page.evaluate("window.scrollBy(0, Math.min(window.innerHeight, 900))")
    except Exception:
        try:
            page.mouse.wheel(0, 900)
        except Exception:
            pass


def _snapshot(page) -> list[dict]:
    """Collect visible, interactable elements in DOM order (viewport-biased)."""
    elements: list[dict] = []
    selectors = "input, textarea, select, button, a[href]"
    nodes = page.locator(selectors)
    count = min(nodes.count(), 80)
    for i in range(count):
        try:
            el = nodes.nth(i)
            if not el.is_visible():
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            etype = el.get_attribute("type") or ""
            label = _element_label(el, tag)
            current = ""
            if tag in {"input", "textarea"}:
                try:
                    current = (el.input_value(timeout=500) or "").strip()[:60]
                except Exception:
                    pass
            elements.append({
                "index": len(elements),
                "tag": tag,
                "type": etype,
                "label": label[:100],
                "value": current,
                "_loc": el,
            })
        except Exception:
            continue
    return elements


def _element_label(el, tag: str) -> str:
    for attr in ("aria-label", "placeholder", "name", "id"):
        try:
            val = el.get_attribute(attr)
            if val and val.strip():
                return val.strip()
        except Exception:
            pass
    if tag in {"button", "a"}:
        try:
            t = (el.inner_text(timeout=500) or "").strip()
            if t:
                return t.split("\n")[0]
        except Exception:
            pass
    # Associated <label for="id">
    try:
        eid = el.get_attribute("id")
        if eid:
            lbl = el.page.locator(f"label[for='{eid}']").first
            if lbl.count() > 0:
                return (lbl.inner_text(timeout=500) or "").strip()
    except Exception:
        pass
    return ""


def _render_elements(elements: list[dict]) -> str:
    lines = []
    for e in elements:
        val = f' value="{e["value"]}"' if e.get("value") else ""
        lines.append(f"[{e['index']}] <{e['tag']} type={e['type']}{val}> {e['label']}")
    return "\n".join(lines)


def _is_blocked(page) -> bool:
    try:
        body = (page.inner_text("body", timeout=2000) or "").lower()
    except Exception:
        return False
    return any(sign in body for sign in BLOCK_SIGNS)


def _looks_like_apply_link(url: str) -> bool:
    return any(k in url.lower() for k in ("apply", "job", "career", "greenhouse",
                                          "lever", "workday", "ashby", "icims",
                                          "smartrecruiters", "bamboohr"))


# ---- action execution ------------------------------------------------------

def _execute(page, action, idx, value, elements, resume_path, settings) -> bool:
    if action == "scroll":
        _scroll_page(page)
        human_delay(settings.min_delay_ms, settings.max_delay_ms)
        return True

    if action == "upload_resume":
        if not resume_path:
            logger.warning("upload_resume action requested but no resume_path provided")
            return False
        if not resume_path.exists():
            logger.warning("Resume file not found for upload: %s", resume_path)
            return False
        try:
            file_input = page.locator("input[type='file']").first
            file_input.set_input_files(str(resume_path))
            # Verify the file was actually attached.
            try:
                file_count = file_input.evaluate("el => el.files?.length || 0")
                if file_count == 0:
                    logger.warning(
                        "External resume upload: set_input_files succeeded but "
                        "no files attached to input element"
                    )
                    return False
            except Exception:
                pass
            return True
        except Exception as exc:
            logger.warning("External resume upload failed: %s", exc)
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
            try:
                loc.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass
            loc.click(timeout=8000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
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
