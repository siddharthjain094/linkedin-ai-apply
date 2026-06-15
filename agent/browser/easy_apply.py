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

from pathlib import Path

from agent.browser.finders import human_delay, try_click
from agent.browser.forms import resolve_answer
from agent.config import Settings
from agent.llm.provider import LLMClient

MODAL = "div.jobs-easy-apply-modal, div[data-test-modal]"
MAX_STEPS = 14


def easy_apply(
    session,
    settings: Settings,
    job: dict,
    intake: dict,
    llm: LLMClient | None,
    resume_path: Path | None,
) -> tuple[str, str, list[dict]]:
    page = session.page
    try:
        page.goto(job["url"], wait_until="domcontentloaded")
        human_delay(settings.min_delay_ms, settings.max_delay_ms)
    except Exception as exc:
        return "error", f"navigation failed: {exc}", []

    # Restart/idempotency guard: if LinkedIn already shows this as applied, do not
    # resubmit (a prior run may have submitted then crashed before recording it).
    if _already_applied(page):
        return "applied", "Already applied (detected on job page).", []

    # Closed posting: nothing we can do, mark it terminal so we never retry.
    if _no_longer_accepting(page):
        return "closed", "No longer accepting applications.", []

    if not try_click(page, "button.jobs-apply-button:has-text('Easy Apply'), "
                           "button[aria-label*='Easy Apply'], button:has-text('Easy Apply')",
                     timeout=6000):
        # Not an Easy Apply job (or button missing). Signal the caller to try the
        # external/AI path instead of parking it.
        return "not_easy", "No Easy Apply button (likely external).", []

    human_delay(settings.min_delay_ms, settings.max_delay_ms)

    for _ in range(MAX_STEPS):
        if page.locator(MODAL).count() == 0:
            break

        unanswered = _fill_visible_fields(page, settings, intake, llm, resume_path)
        if unanswered:
            _save_screenshot(settings, job, page)
            _dismiss_modal(page)
            labels = ", ".join(u["question"] for u in unanswered)
            return "human_review", f"Could not answer required field(s): {labels}", unanswered

        # Final submit.
        if _footer_has(page, "Submit application"):
            if settings.dry_run:
                _dismiss_modal(page)
                return "human_review", "DRY_RUN: stopped before submit.", []
            _uncheck_follow(page)
            if try_click(page, "button[aria-label='Submit application'], "
                               "button:has-text('Submit application')"):
                human_delay(settings.min_delay_ms, settings.max_delay_ms)
                _dismiss_modal(page)
                return "applied", "Submitted via Easy Apply.", []
            return "error", "Submit button click failed.", []

        # Otherwise advance.
        advanced = (
            try_click(page, "button[aria-label='Continue to next step'], "
                            "button:has-text('Next'), button:has-text('Continue')")
            or try_click(page, "button[aria-label='Review your application'], "
                               "button:has-text('Review')")
        )
        if not advanced:
            _save_screenshot(settings, job, page)
            _dismiss_modal(page)
            return "human_review", "Could not advance the Easy Apply form.", []
        human_delay(settings.min_delay_ms, settings.max_delay_ms)

    return "human_review", "Easy Apply flow did not reach submit.", []


def _already_applied(page) -> bool:
    """Detect LinkedIn's 'Applied' state so we never submit twice."""
    try:
        return page.locator(
            "span.artdeco-inline-feedback--success:has-text('Applied'), "
            "div.jobs-s-apply--applied, "
            "button.jobs-apply-button[disabled]:has-text('Applied')"
        ).count() > 0
    except Exception:
        return False


def _no_longer_accepting(page) -> bool:
    """Detect LinkedIn's 'No longer accepting applications' banner (job closed)."""
    phrase = "No longer accepting applications"
    try:
        if page.get_by_text(phrase, exact=False).count() > 0:
            return True
    except Exception:
        pass
    try:
        body = (page.inner_text("body", timeout=2000) or "").lower()
        return phrase.lower() in body
    except Exception:
        return False


def _footer_has(page, text: str) -> bool:
    return page.locator(f"button:has-text('{text}'), button[aria-label*='{text}']").count() > 0


def _fill_visible_fields(page, settings, intake, llm, resume_path) -> list[dict]:
    """Fill every field in the current step.

    Returns a list of {question, options, type} for required fields we could not
    answer (empty list means the step is fully filled).
    """
    unanswered: list[dict] = []

    # Resume upload, if the step asks for it.
    if resume_path and page.locator("input[type='file']").count() > 0:
        try:
            page.locator("input[type='file']").first.set_input_files(str(resume_path))
            human_delay(settings.min_delay_ms, settings.max_delay_ms)
        except Exception:
            pass

    groups = page.locator("div.jobs-easy-apply-form-section__grouping, "
                          "div.fb-dash-form-element, div[data-test-form-element]")
    n = groups.count()
    for i in range(n):
        g = groups.nth(i)
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
