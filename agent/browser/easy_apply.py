"""LinkedIn Easy Apply multi-step form automation.

Returns (status, notes, needs_input) where status is one of:
  "applied"        - submitted successfully
  "closed"         - job is no longer accepting applications (can't apply)
  "human_review"   - blocked (unanswerable required field, captcha, etc.)
  "error"          - unexpected failure
and needs_input is a list of {question, options, type} dicts describing the
fields that blocked us (empty unless status == "human_review").
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from agent.browser.finders import human_delay, try_click
from agent.browser.forms import resolve_answer
from agent.browser.job_page import (
    already_applied,
    find_linkedin_apply_button,
    is_linkedin_job_url,
    no_longer_accepting,
    safe_close_page,
    wait_for_apply_button,
)
from agent.config import Settings
from agent.llm.provider import LLMClient

logger = logging.getLogger(__name__)

MODAL = "div.jobs-easy-apply-modal, div[data-test-modal]"
MODAL_CONTENT = (
    "div.jobs-easy-apply-content",
    "div.jobs-easy-apply-modal__content",
    "div[data-test-modal-content]",
    "div.artdeco-modal__content",
)
MAX_STEPS = 14


def easy_apply(
    session,
    settings: Settings,
    job: dict,
    intake: dict,
    llm: LLMClient | None,
    resume_path: Path | None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> tuple[str, str, list[dict]]:
    page = session.page
    try:
        if hasattr(session, "navigate"):
            session.navigate(job["url"], progress=getattr(session, "_progress", None))
        else:
            page.goto(job["url"], wait_until="domcontentloaded")
            human_delay(settings.min_delay_ms, settings.max_delay_ms)
    except Exception as exc:
        return "error", f"navigation failed: {exc}", []

    # Restart/idempotency guard: if LinkedIn already shows this as applied, do not
    # resubmit (a prior run may have submitted then crashed before recording it).
    if already_applied(page):
        return "applied", "Already applied (detected on job page).", []

    # Closed posting: nothing we can do, mark it terminal so we never retry.
    if no_longer_accepting(page):
        return "closed", "No longer accepting applications.", []

    wait_for_apply_button(page)
    apply_btn = find_linkedin_apply_button(page)
    if apply_btn is None:
        hint = ""
        try:
            url = (page.url or "").lower()
            if "login" in url or "authwall" in url:
                hint = " (not logged in — run `linkedin-apply login`)"
        except Exception:
            pass
        from agent.browser.job_page import save_apply_debug_screenshot
        shot = save_apply_debug_screenshot(settings, job.get("job_id", "job"), page)
        extra = f" Screenshot: {shot}" if shot else ""
        return "not_easy", f"No Apply / Easy Apply button on job page.{hint}{extra}", []

    try:
        apply_btn.scroll_into_view_if_needed(timeout=5000)
        apply_btn.click(timeout=8000)
    except Exception as exc:
        return "not_easy", f"Could not click Apply button: {exc}", []

    human_delay(settings.min_delay_ms, settings.max_delay_ms)

    try:
        page.locator(MODAL).first.wait_for(state="visible", timeout=6000)
    except Exception:
        ext = _external_tab(page)
        if ext is not None:
            # Close the old LinkedIn job page before switching to the external tab
            # to prevent tab accumulation across jobs.
            safe_close_page(page)
            session.page = ext
            return "not_easy", "External apply tab opened from LinkedIn.", []
        return "not_easy", "Apply clicked but no Easy Apply modal opened (likely external).", []

    for _ in range(MAX_STEPS):
        if should_stop and should_stop():
            _dismiss_modal(page)
            return "stopped", "Stop requested during Easy Apply.", []

        if page.locator(MODAL).count() == 0:
            break

        unanswered = _fill_visible_fields(page, settings, intake, llm, resume_path)
        if unanswered:
            _save_screenshot(settings, job, page)
            _dismiss_modal(page)
            labels = ", ".join(u["question"] for u in unanswered)
            return "human_review", f"Could not answer required field(s): {labels}", unanswered

        # LinkedIn keeps Next/Submit in a sticky footer; scroll the modal body first so
        # hidden required fields are filled and footer buttons are clickable.
        _scroll_modal_content(page)

        # Final submit.
        if _footer_has(page, "Submit application"):
            if settings.dry_run:
                _dismiss_modal(page)
                return "human_review", "DRY_RUN: stopped before submit.", []
            _uncheck_follow(page)
            if _click_modal_footer(
                page,
                "button[aria-label='Submit application']",
                "button:has-text('Submit application')",
            ):
                human_delay(settings.min_delay_ms, settings.max_delay_ms)
                _dismiss_modal(page)
                return "applied", "Submitted via Easy Apply.", []
            return "error", "Submit button click failed.", []

        # Otherwise advance (never click the draft "Save" control).
        advanced = (
            _click_modal_footer(
                page,
                "button[aria-label='Continue to next step']",
                "button[aria-label='Review your application']",
                "footer button:has-text('Next')",
                "footer button:has-text('Review')",
                "button:has-text('Next')",
                "button:has-text('Review')",
            )
        )
        if not advanced:
            _save_screenshot(settings, job, page)
            _dismiss_modal(page)
            return "human_review", "Could not advance the Easy Apply form.", []
        human_delay(settings.min_delay_ms, settings.max_delay_ms)

    return "human_review", "Easy Apply flow did not reach submit.", []


def _external_tab(page):
    # Search from the end to prefer the most recently opened tab.
    for p in reversed(page.context.pages):
        if p is page:
            continue
        try:
            if not is_linkedin_job_url(p.url or ""):
                return p
        except Exception:
            continue
    return None


def _modal(page):
    return page.locator(MODAL).first


def _footer_has(page, text: str) -> bool:
    modal = _modal(page)
    if modal.count() == 0:
        return False
    return modal.locator(
        f"button:has-text('{text}'), button[aria-label*='{text}']"
    ).count() > 0


def _scroll_modal_content(page) -> None:
    """Scroll the Easy Apply modal body so off-screen fields and footer CTAs are reachable."""
    modal = _modal(page)
    if modal.count() == 0:
        return
    for sel in MODAL_CONTENT:
        content = modal.locator(sel).first
        if content.count() == 0:
            continue
        try:
            content.evaluate(
                """el => {
                    const step = Math.max(200, el.clientHeight * 0.85);
                    const max = Math.max(0, el.scrollHeight - el.clientHeight);
                    el.scrollTop = 0;
                    for (let y = 0; y <= max; y += step) {
                        el.scrollTop = y;
                    }
                    el.scrollTop = max;
                    el.scrollTop = 0;
                }"""
            )
            return
        except Exception:
            continue
    try:
        modal.evaluate("el => { el.scrollTop = el.scrollHeight; el.scrollTop = 0; }")
    except Exception:
        pass


def _click_modal_footer(page, *selectors: str) -> bool:
    """Click a footer action inside the Easy Apply modal (Next / Review / Submit)."""
    modal = _modal(page)
    if modal.count() == 0:
        return False
    _scroll_modal_content(page)
    for sel in selectors:
        btn = modal.locator(sel).first
        if btn.count() == 0:
            continue
        try:
            btn.scroll_into_view_if_needed(timeout=3000)
            btn.wait_for(state="visible", timeout=4000)
            if btn.is_disabled():
                continue
            btn.click(timeout=8000)
            return True
        except Exception:
            continue
    return False


def _fill_visible_fields(page, settings, intake, llm, resume_path) -> list[dict]:
    """Fill every field in the current step.

    Returns a list of {question, options, type} for required fields we could not
    answer (empty list means the step is fully filled).
    """
    unanswered: list[dict] = []
    modal = _modal(page)
    _scroll_modal_content(page)

    # Resume upload, if the step asks for it.
    file_inputs = modal if modal.count() > 0 else page
    if resume_path and file_inputs.locator("input[type='file']").count() > 0:
        try:
            if not resume_path.exists():
                logger.warning("Resume file not found for upload: %s", resume_path)
            else:
                file_input = file_inputs.locator("input[type='file']").first
                file_input.set_input_files(str(resume_path))
                human_delay(settings.min_delay_ms, settings.max_delay_ms)
                # Verify the file was actually attached to the input.
                try:
                    file_count = file_input.evaluate("el => el.files?.length || 0")
                    if file_count == 0:
                        logger.warning(
                            "Resume upload: set_input_files succeeded but "
                            "no files attached to input element"
                        )
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Resume upload failed for %s: %s", resume_path, exc)

    groups = (modal if modal.count() > 0 else page).locator(
        "div.jobs-easy-apply-form-section__grouping, "
        "div.fb-dash-form-element, div[data-test-form-element]"
    )
    n = groups.count()
    for i in range(n):
        g = groups.nth(i)
        try:
            g.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        try:
            label = _label_of(g)
            if not label:
                continue

            # Select dropdown.
            if g.locator("select").count() > 0:
                sel = g.locator("select").first
                opts = [o.strip() for o in sel.locator("option").all_inner_texts() if o.strip()]
                opts = [o for o in opts if o.lower() not in {"select an option", "select"}]
                ans = resolve_answer(label, opts, intake, llm)
                if ans is None:
                    if _is_required(g):
                        unanswered.append({"question": label, "options": opts, "type": "select"})
                    continue
                sel.select_option(label=ans)
                continue

            # Radio group.
            if g.locator("input[type='radio']").count() > 0:
                opts = _radio_options(g)
                ans = resolve_answer(label, list(opts.keys()), intake, llm)
                if ans is None:
                    if _is_required(g):
                        unanswered.append({"question": label,
                                           "options": list(opts.keys()), "type": "radio"})
                    continue
                _select_radio(opts.get(ans))
                continue

            # Checkbox (agreements) - tick if required.
            if g.locator("input[type='checkbox']").count() > 0:
                if _is_required(g):
                    try:
                        g.locator("input[type='checkbox']").first.check()
                    except Exception:
                        pass
                continue

            # Text / number / textarea.
            field = g.locator("input[type='text'], input[type='number'], "
                              "input:not([type]), textarea").first
            if field.count() > 0:
                current = field.input_value() if hasattr(field, "input_value") else ""
                if current and current.strip():
                    continue
                ans = resolve_answer(label, None, intake, llm)
                if ans is None:
                    if _is_required(g):
                        unanswered.append({"question": label, "options": [], "type": "text"})
                    continue
                field.fill(str(ans))
                continue
        except Exception:
            continue
    return unanswered


def _label_of(group) -> str:
    for sel in ("label", "legend", "span[data-test-form-element-label]"):
        try:
            loc = group.locator(sel).first
            if loc.count() > 0:
                t = (loc.inner_text(timeout=1000) or "").strip()
                if t:
                    return t.split("\n")[0]
        except Exception:
            continue
    return ""


def _is_required(group) -> bool:
    try:
        if group.locator("[aria-required='true'], [required]").count() > 0:
            return True
        return "*" in (group.inner_text(timeout=800) or "")
    except Exception:
        return True  # assume required to be safe


def _radio_options(group) -> dict:
    out = {}
    radios = group.locator("input[type='radio']")
    for i in range(radios.count()):
        r = radios.nth(i)
        rid = r.get_attribute("value") or r.get_attribute("id") or str(i)
        text = ""
        lbl = group.locator(f"label[for='{r.get_attribute('id')}']")
        if lbl.count() > 0:
            text = (lbl.first.inner_text() or "").strip()
        out[text or rid] = r
    return out


def _select_radio(radio) -> None:
    if radio is None:
        return
    try:
        radio.check(force=True)
    except Exception:
        try:
            radio.click(force=True)
        except Exception:
            pass


def _uncheck_follow(page) -> None:
    try:
        cb = page.locator("input#follow-company-checkbox, label:has-text('Follow') input")
        if cb.count() > 0 and cb.first.is_checked():
            cb.first.uncheck()
    except Exception:
        pass


def _dismiss_modal(page) -> None:
    try_click(page, "button[aria-label='Dismiss']")
    try_click(page, "button[aria-label='Discard']")
    try_click(page, "button:has-text('Discard')")


def _save_screenshot(settings: Settings, job: dict, page) -> None:
    try:
        out = settings.output_path / "screenshots" / f"{job['job_id']}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out))
    except Exception:
        pass
