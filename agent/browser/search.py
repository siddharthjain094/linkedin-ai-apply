"""Build LinkedIn job-search URLs, paginate, and scrape job cards.

LinkedIn's DOM changes often; selectors are written defensively with fallbacks
and centralised here so they are easy to update.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from typing import Callable, Optional

from agent.browser.finders import human_delay
from agent.config import SearchConfig, Settings

BASE = "https://www.linkedin.com/jobs/search/"

_EXP_MAP = {
    "internship": "1",
    "entry": "2",
    "associate": "3",
    "mid-senior": "4",
    "director": "5",
    "executive": "6",
}
_DATE_MAP = {
    "past-24h": "r86400",
    "past-week": "r604800",
    "past-month": "r2592000",
    "any": "",
}


def build_url(title: str, location: str, sc: SearchConfig) -> str:
    params: dict[str, str] = {"keywords": _keywords(title, sc), "location": location}

    exp = [_EXP_MAP[e] for e in sc.experience_levels if e in _EXP_MAP]
    if exp:
        params["f_E"] = ",".join(exp)

    tpr = _DATE_MAP.get(sc.date_posted, "")
    if tpr:
        params["f_TPR"] = tpr

    wt = []
    if sc.on_site:
        wt.append("1")
    if sc.remote:
        wt.append("2")
    if sc.hybrid:
        wt.append("3")
    if wt:
        params["f_WT"] = ",".join(wt)

    if sc.easy_apply_only:
        params["f_AL"] = "true"

    params["sortBy"] = "DD"  # most recent
    return f"{BASE}?{urlencode(params)}"


def _keywords(title: str, sc: SearchConfig) -> str:
    return f"{title} {sc.keywords}".strip() if sc.keywords else title


def _job_id_from_url(url: str) -> str:
    m = re.search(r"/jobs/view/(\d+)", url) or re.search(r"currentJobId=(\d+)", url)
    return m.group(1) if m else url


def scrape_search(
    session,
    settings: Settings,
    title: str,
    location: str,
    should_stop: Optional[Callable[[], bool]] = None,
) -> list[dict]:
    """Scrape one (title, location) search. Returns a list of job dicts."""
    def _stop() -> bool:
        return bool(should_stop and should_stop())

    sc = settings.search
    page = session.page
    url = build_url(title, location, sc)
    if hasattr(session, "navigate"):
        session.navigate(url, progress=getattr(session, "_progress", None))
    else:
        page.goto(url, wait_until="domcontentloaded")
        human_delay(settings.min_delay_ms, settings.max_delay_ms)

    found: dict[str, dict] = {}
    target = sc.max_jobs_per_search

    while len(found) < target:
        if _stop():
            break
        _scroll_list(page)
        cards = page.locator("div.job-card-container, li.jobs-search-results__list-item")
        count = cards.count()
        for i in range(count):
            if _stop():
                break
            try:
                card = cards.nth(i)
                link = card.locator("a.job-card-container__link, a.job-card-list__title").first
                href = link.get_attribute("href") or ""
                if not href:
                    continue
                if href.startswith("/"):
                    href = "https://www.linkedin.com" + href
                job_id = _job_id_from_url(href)
                if job_id in found:
                    continue
                title_txt = (link.inner_text() or "").strip().split("\n")[0]
                company = _safe(card, "span.job-card-container__primary-description, "
                                     "div.artdeco-entity-lockup__subtitle")
                loc = _safe(card, "li.job-card-container__metadata-item, "
                                  "div.artdeco-entity-lockup__caption")
                found[job_id] = {
                    "job_id": job_id,
                    "title": title_txt,
                    "company": company,
                    "location": loc,
                    "url": href.split("?")[0],
                    "source": "search",
                }
            except Exception:
                continue

        if _stop():
            break
        if not _go_next_page(page, settings):
            break

    return list(found.values())[:target]


_DESC_SELECTORS = (
    "div.jobs-description__content",
    "article.jobs-description__container",
    "div#job-details",
    "div.jobs-box__html-content",
    "div.show-more-less-html__markup",
    "section.description",
)

# The "...more" control that expands the truncated description. These are all
# BUTTONS / aria-labelled controls on purpose: the description body itself often
# contains real "Learn more" anchors (e.g. to the company site), and clicking one
# of those would navigate us away from the job. Never match plain <a> "more".
_EXPAND_SELECTORS = (
    "button[aria-label='Click to see more description']",
    "button[aria-label*='see more description' i]",
    "button[aria-label*='see more' i]",
    "button.jobs-description__footer-button",
    "button.show-more-less-html__button--more",
    "button.show-more-less-html__button:has-text('more')",
    "button:has-text('Show more')",
    "button:has-text('See more')",
)


def _expand_description(page) -> bool:
    """Click the description's expand ('...more') button if present. Returns True
    if something was clicked. Scoped to real buttons so we never follow a
    'Learn more' link inside the description text."""
    for sel in _EXPAND_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0 or not loc.is_visible():
                continue
            loc.click(timeout=1500)
            return True
        except Exception:
            continue
    return False


def fetch_description(session, settings: Settings, job: dict) -> dict:
    """Open a job's detail page and capture the FULL description + apply type.

    We always click the '...more' expander first so the stored description is the
    complete text, not LinkedIn's truncated preview."""
    page = session.page
    try:
        if hasattr(session, "navigate"):
            session.navigate(job["url"], progress=getattr(session, "_progress", None))
        else:
            page.goto(job["url"], wait_until="domcontentloaded")
            human_delay(settings.min_delay_ms, settings.max_delay_ms)
        # The job detail pane renders after the initial DOM; wait for any known
        # description container (best-effort, never fatal).
        try:
            page.locator(", ".join(_DESC_SELECTORS)).first.wait_for(
                state="visible", timeout=8000
            )
        except Exception:
            pass

        # Expand the truncated description, then give the DOM a moment to render
        # the full text before we read it.
        expanded = _expand_description(page)
        if expanded:
            try:
                page.wait_for_timeout(400)
            except Exception:
                pass

        desc = ""
        for sel in _DESC_SELECTORS:
            desc = _safe(page, sel)
            if desc:
                break
        job["description"] = desc
        job["apply_type"] = _detect_apply_type(page)
        if not desc:
            job["notes"] = "description container not found (LinkedIn DOM may have changed)"
        elif not expanded:
            job["notes"] = "captured description (no expander found; may be truncated)"
    except Exception as exc:
        job["description"] = job.get("description", "")
        job["notes"] = f"description fetch failed: {exc}"
    return job


def _detect_apply_type(page) -> str:
    """Classify the apply path. Easy Apply is detected by its distinctive button;
    a plain Apply (often opening an external site) is "external"."""
    try:
        if page.locator(
            "button.jobs-apply-button:has-text('Easy Apply'), "
            "button[aria-label*='Easy Apply'], button:has-text('Easy Apply')"
        ).count() > 0:
            return "easy"
        if page.locator(
            "button.jobs-apply-button, button[aria-label*='Apply'], "
            "a:has-text('Apply'), button:has-text('Apply')"
        ).count() > 0:
            return "external"
    except Exception:
        pass
    return "unknown"


def _safe(scope, selector: str) -> str:
    try:
        loc = scope.locator(selector).first
        if loc.count() == 0:
            return ""
        return (loc.inner_text(timeout=2000) or "").strip()
    except Exception:
        return ""


def _scroll_list(page) -> None:
    try:
        page.mouse.wheel(0, 2500)
    except Exception:
        pass


def _go_next_page(page, settings: Settings) -> bool:
    from agent.browser.finders import try_click

    human_delay(settings.min_delay_ms, settings.max_delay_ms)
    return try_click(page, "button[aria-label='View next page'], "
                           "li.artdeco-pagination__indicator--number + li button")
