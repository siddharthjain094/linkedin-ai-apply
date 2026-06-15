"""Best-effort scan of LinkedIn posts advertising open roles (#hiring).

Posts are noisy and unstructured, so each becomes a `post` job with the post text
as its description and any external link captured for the AI applier. If no
actionable apply path is found, the apply phase will route it to human_review.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlencode

from agent.browser.finders import human_delay
from agent.config import Settings

CONTENT_SEARCH = "https://www.linkedin.com/search/results/content/"


def _post_id(text: str, author: str) -> str:
    h = hashlib.sha1(f"{author}:{text[:200]}".encode("utf-8")).hexdigest()[:16]
    return f"post-{h}"


def scrape_hiring_posts(session, settings: Settings, title: str) -> list[dict]:
    page = session.page
    query = f"hiring {title}"
    params = {"keywords": query, "datePosted": "past-week", "sortBy": "date_posted"}
    page.goto(f"{CONTENT_SEARCH}?{urlencode(params)}", wait_until="domcontentloaded")
    human_delay(settings.min_delay_ms, settings.max_delay_ms)

    results: dict[str, dict] = {}
    for _ in range(4):
        try:
            page.mouse.wheel(0, 3000)
        except Exception:
            pass
        human_delay(settings.min_delay_ms, settings.max_delay_ms)

    posts = page.locator("div.feed-shared-update-v2, div.update-components-text")
    count = min(posts.count(), settings.search.max_jobs_per_search)
    for i in range(count):
        try:
            post = posts.nth(i)
            text = (post.inner_text(timeout=2000) or "").strip()
            if not text or "hiring" not in text.lower():
                continue
            author = _safe(post, "span.update-components-actor__name, "
                                 "span.feed-shared-actor__name")
            link = _first_external_link(post)
            pid = _post_id(text, author)
            if pid in results:
                continue
            results[pid] = {
                "job_id": pid,
                "title": f"Hiring post: {title}",
                "company": author or "(from post)",
                "location": "",
                "url": link or page.url,
                "description": text[:4000],
                "source": "hiring_post",
                "apply_type": "post",
            }
        except Exception:
            continue
    return list(results.values())


def _first_external_link(scope) -> str:
    try:
        anchors = scope.locator("a[href^='http']")
        n = anchors.count()
        for i in range(n):
            href = anchors.nth(i).get_attribute("href") or ""
            if href and "linkedin.com" not in href:
                return href.split("?")[0]
    except Exception:
        pass
    return ""


def _safe(scope, selector: str) -> str:
    try:
        loc = scope.locator(selector).first
        if loc.count() == 0:
            return ""
        return re.sub(r"\s+", " ", (loc.inner_text(timeout=1500) or "")).strip()
    except Exception:
        return ""
