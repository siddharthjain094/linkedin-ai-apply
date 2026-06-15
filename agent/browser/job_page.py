"""LinkedIn job-page state checks and navigation to an external apply target."""

from __future__ import annotations

import logging
import re
import time

from agent.browser.finders import human_delay

logger = logging.getLogger(__name__)


def safe_close_page(page) -> bool:
    """Close a page, ignoring errors if already closed. Returns True if closed."""
    try:
        page.close()
        return True
    except Exception:
        return False

# Apply controls on the job detail page (Easy Apply, Apply, Apply now).
_APPLY_TOP_CARD = (
    "div.jobs-apply-button--top-card",
    "div.jobs-s-apply",
)

_APPLY_BUTTON_SELECTORS = (
    "button[aria-label*='Easy Apply' i]",
    "button[aria-label*='Apply to' i]",
    "a[aria-label*='Apply to' i]",
    "a[aria-label*='Apply for' i]",
    "button.jobs-apply-button",
    "a.jobs-apply-button",
    "a.jobs-s-apply__application-link",
    "button.jobs-s-apply__application-link",
    "div.jobs-apply-button--top-card a.artdeco-button--primary",
    "div.jobs-apply-button--top-card button.artdeco-button--primary",
    "div.jobs-s-apply a.artdeco-button--primary",
    "div.jobs-s-apply button.artdeco-button--primary",
    # External apply ("Responses managed off LinkedIn") is often <a>Apply</a>.
    'a:text-is("Apply")',
    'button:text-is("Apply")',
    "button:has-text('Easy Apply')",
    "a:has-text('Easy Apply')",
    "button:has-text('Apply now')",
    "a:has-text('Apply now')",
    "button:has-text('Apply')",
    "a:has-text('Apply')",
    "a:has-text('Apply on company website')",
)

_JOB_SHELL_SELECTORS = (
    "button.jobs-apply-button",
    "div.jobs-apply-button--top-card",
    "div.jobs-unified-top-card",
    "div.jobs-details__main-content",
    "div.jobs-description__content",
    "div.jobs-search__job-details",
)


def is_linkedin_job_url(url: str) -> bool:
    u = (url or "").lower()
    return "linkedin.com" in u and ("/jobs/" in u or "currentjobid=" in u)


def _looks_like_apply_button(loc) -> bool:
    """True only for a real Apply / Easy Apply CTA, not a job-card link."""
    try:
        label = (loc.inner_text(timeout=800) or "").strip()
        aria = loc.get_attribute("aria-label") or ""
        title = loc.get_attribute("title") or ""
        if not _apply_label_looks_valid(f"{label} {aria} {title}"):
            return False
        # Job-card <a> links pull in title/company/location; real CTAs are short.
        if len(label) > 60 and not re.search(
            r"easy apply|apply now", f"{aria} {title}", re.I
        ):
            return False
        return True
    except Exception:
        return False


def _first_apply_button(page, selector: str):
    root = page.locator(selector)
    try:
        count = root.count()
    except Exception:
        return None
    for i in range(count):
        item = root.nth(i)
        try:
            if item.is_visible() and _looks_like_apply_button(item):
                return item
        except Exception:
            continue
    return None


def _first_visible(page, selector: str):
    root = page.locator(selector)
    try:
        count = root.count()
    except Exception:
        return None
    for i in range(count):
        item = root.nth(i)
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def _scroll_apply_into_view(page) -> None:
    for sel in (
        "div.jobs-apply-button--top-card",
        "div.jobs-s-apply",
        "button.jobs-apply-button",
        "div.jobs-unified-top-card",
    ):
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.scroll_into_view_if_needed(timeout=3000)
                return
        except Exception:
            continue
    try:
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


_SKIP_APPLY_LABELS = (
    "already applied",
    "application submitted",
    "no longer accepting",
    "how you match",
    "applicants",
    "people clicked apply",
)


def _apply_label_looks_valid(text: str) -> bool:
    t = (text or "").strip().lower()
    if "apply" not in t:
        return False
    if any(s in t for s in _SKIP_APPLY_LABELS):
        return False
    return bool(re.search(r"easy apply|apply now|\bapply\b", t))


def _pick_apply_candidate(loc) -> object | None:
    """Return ``loc`` if it looks like an apply CTA, else None."""
    try:
        if not loc.is_visible():
            return None
        if _looks_like_apply_button(loc):
            return loc
    except Exception:
        pass
    return None


def _find_in_apply_top_card(page):
    """Apply CTA inside LinkedIn's top-card apply strip (most reliable on job view)."""
    for container_sel in _APPLY_TOP_CARD:
        try:
            container = page.locator(container_sel).first
            if container.count() == 0 or not container.is_visible():
                continue
            for child_sel in (
                "a.artdeco-button--primary",
                "button.artdeco-button--primary",
                "a.jobs-apply-button",
                "button.jobs-apply-button",
                "a",
                "button",
            ):
                children = container.locator(child_sel)
                for i in range(min(children.count(), 8)):
                    picked = _pick_apply_candidate(children.nth(i))
                    if picked is not None:
                        return picked
        except Exception:
            continue
    return None


def _text_search_apply_button(page):
    """Last resort: scan visible clickables whose label/aria contains 'apply'."""
    try:
        loc = page.get_by_text(re.compile(r"^(easy apply|apply now|apply)$", re.I))
        count = loc.count()
        for i in range(min(count, 20)):
            item = loc.nth(i)
            try:
                if not item.is_visible():
                    continue
                tag = (item.evaluate("el => el.tagName") or "").lower()
                role = item.get_attribute("role") or ""
                if tag in ("button", "a") or role == "button":
                    picked = _pick_apply_candidate(item)
                    if picked is not None:
                        return picked
                parent = item.locator(
                    "xpath=ancestor-or-self::button|ancestor-or-self::a"
                ).first
                picked = _pick_apply_candidate(parent)
                if picked is not None:
                    return picked
            except Exception:
                continue
    except Exception:
        pass

    try:
        candidates = page.locator(
            "div.jobs-apply-button--top-card a, div.jobs-apply-button--top-card button, "
            "div.jobs-s-apply a, div.jobs-s-apply button, "
            "a.artdeco-button--primary, button.artdeco-button--primary, "
            "button, a[role='button'], [role='button']"
        )
        count = min(candidates.count(), 100)
    except Exception:
        return None

    best = None
    for i in range(count):
        item = candidates.nth(i)
        try:
            if not item.is_visible():
                continue
            if not _looks_like_apply_button(item):
                continue
            hay = (
                f"{(item.inner_text(timeout=400) or '').strip()} "
                f"{item.get_attribute('aria-label') or ''} "
                f"{item.get_attribute('title') or ''}"
            ).lower()
            if "easy apply" in hay:
                return item
            if best is None:
                best = item
        except Exception:
            continue
    return best


def find_apply_button_anywhere(page):
    """Find a visible Apply / Apply now / Easy Apply control on any site."""
    for sel in _APPLY_BUTTON_SELECTORS:
        btn = _first_apply_button(page, sel)
        if btn is not None:
            return btn
    btn = _role_apply_button(page)
    if btn is not None:
        return btn
    return _text_search_apply_button(page)


def _role_apply_button(page):
    for role in ("button", "link"):
        for pattern in (
            re.compile(r"easy apply", re.I),
            re.compile(r"apply now", re.I),
            re.compile(r"^apply$", re.I),
        ):
            try:
                loc = page.get_by_role(role, name=pattern)
                count = loc.count()
                for i in range(count):
                    item = loc.nth(i)
                    picked = _pick_apply_candidate(item)
                    if picked is not None:
                        return picked
            except Exception:
                continue
    return None


def wait_for_job_page(page, timeout: int = 12_000) -> None:
    """Wait for LinkedIn's job detail UI (apply buttons load async after domcontentloaded)."""
    for sel in _JOB_SHELL_SELECTORS:
        try:
            page.locator(sel).first.wait_for(state="visible", timeout=timeout)
            return
        except Exception:
            continue
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout, 8000))
    except Exception:
        pass


def wait_for_apply_button(page, timeout: int = 15_000) -> None:
    """Poll until an Apply / Easy Apply / Apply now control appears."""
    wait_for_job_page(page, timeout=min(timeout, 12_000))
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        if find_linkedin_apply_button(page, scroll=False):
            return
        try:
            page.wait_for_timeout(400)
        except Exception:
            time.sleep(0.4)
    _scroll_apply_into_view(page)


def find_linkedin_apply_button(page, *, scroll: bool = True):
    """Return the first visible LinkedIn apply control, or None."""
    if scroll:
        _scroll_apply_into_view(page)

    btn = _find_in_apply_top_card(page)
    if btn is not None:
        return btn

    for sel in _APPLY_BUTTON_SELECTORS:
        btn = _first_apply_button(page, sel)
        if btn is not None:
            return btn

    btn = _role_apply_button(page)
    if btn is not None:
        return btn

    btn = _text_search_apply_button(page)
    if btn is not None:
        return btn

    # One retry after scrolling (avoid infinite recursion when scroll=False).
    if scroll:
        return find_linkedin_apply_button(page, scroll=False)
    return None


def find_easy_apply_button(page):
    wait_for_apply_button(page)
    btn = find_linkedin_apply_button(page)
    if btn is None:
        return None
    try:
        label = (btn.inner_text(timeout=1000) or "").lower()
        aria = (btn.get_attribute("aria-label") or "").lower()
        hay = f"{label} {aria}"
        if "easy apply" in hay:
            return btn
    except Exception:
        pass
    return None


def find_external_apply_button(page):
    wait_for_apply_button(page)
    btn = find_linkedin_apply_button(page)
    if btn is None:
        return None
    try:
        label = (btn.inner_text(timeout=1000) or "").lower()
        aria = (btn.get_attribute("aria-label") or "").lower()
        hay = f"{label} {aria}"
        if "easy apply" in hay:
            return None
    except Exception:
        pass
    return btn


def _login_wall_hint(page) -> str:
    try:
        url = (page.url or "").lower()
        if any(x in url for x in ("/login", "/authwall", "/checkpoint")):
            return " (LinkedIn login/auth wall — run `linkedin-apply login`)"
        body = (page.inner_text("body", timeout=1500) or "").lower()
        if "sign in" in body and "apply" not in body:
            return " (page may require login)"
    except Exception:
        pass
    return ""


def save_apply_debug_screenshot(settings, job_id: str, page) -> str:
    """Best-effort screenshot when apply controls are missing."""
    try:
        out = settings.output_path / "screenshots" / f"{job_id}_apply_debug.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out), full_page=True)
        return str(out)
    except Exception:
        return ""


def click_linkedin_apply_button(page, settings, job_id: str = ""):
    """Click the job-page apply control. Returns (page, error_status, notes)."""
    if already_applied(page):
        return page, "applied", "Already applied (detected on job page)."
    if posting_closed(page):
        return page, "closed", "No longer accepting applications."

    wait_for_apply_button(page)
    btn = find_linkedin_apply_button(page)
    if btn is None:
        hint = _login_wall_hint(page)
        shot = save_apply_debug_screenshot(settings, job_id, page) if job_id else ""
        extra = f" Screenshot: {shot}" if shot else ""
        return page, "human_review", f"No Apply button found on LinkedIn job page.{hint}{extra}"

    try:
        btn.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    context = page.context
    try:
        with context.expect_page(timeout=12_000) as pinfo:
            btn.click(timeout=8000)
        target = pinfo.value
        target.wait_for_load_state("domcontentloaded", timeout=15_000)
        human_delay(settings.min_delay_ms, settings.max_delay_ms)
        return target, None, ""
    except Exception:
        pass

    try:
        btn.click(timeout=8000)
        human_delay(settings.min_delay_ms, settings.max_delay_ms)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass
        return page, None, ""
    except Exception as exc:
        hint = _login_wall_hint(page)
        return page, "human_review", f"Could not click Apply button: {exc}{hint}"


def already_applied(page) -> bool:
    """Detect LinkedIn's 'Applied' state so we never submit twice."""
    try:
        return page.locator(
            "span.artdeco-inline-feedback--success:has-text('Applied'), "
            "div.jobs-s-apply--applied, "
            "button.jobs-apply-button[disabled]:has-text('Applied')"
        ).count() > 0
    except Exception:
        return False


def no_longer_accepting(page) -> bool:
    """Detect LinkedIn's 'No longer accepting applications' banner."""
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


def posting_closed(page) -> bool:
    """Generic 'this job is closed' signals (LinkedIn or ATS pages)."""
    if no_longer_accepting(page):
        return True
    try:
        body = (page.inner_text("body", timeout=2000) or "").lower()
    except Exception:
        return False
    signs = (
        "position has been filled",
        "job is no longer available",
        "this job is closed",
        "application period has ended",
        "we are no longer accepting",
    )
    return any(s in body for s in signs)


def follow_external_apply(page, settings, job_id: str = ""):
    """From a LinkedIn job view, click Apply and return the page with the form.

    Returns ``(target_page, early_status, notes)``. When ``early_status`` is set it
    is one of ``applied``, ``closed``, or ``human_review`` and the caller should
    stop. Otherwise ``target_page`` is where the AI applier should work."""
    target, early_status, notes = click_linkedin_apply_button(page, settings, job_id)
    if early_status:
        return target, early_status, notes
    return target, None, ""
