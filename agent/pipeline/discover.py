"""FIND phase: search LinkedIn + hiring posts, fetch descriptions, persist."""

from __future__ import annotations

from typing import Callable, Optional

from rich.console import Console

from agent.browser import hiring_posts, search
from agent.browser.session import LoggedOutError
from agent.config import Settings
from agent.db import Database

console = Console()


def discover(
    session,
    settings: Settings,
    db: Database,
    should_stop: Optional[Callable[[], bool]] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> dict:
    sc = settings.search
    known = db.known_job_ids()
    collected: dict[str, dict] = {}

    def _stop() -> bool:
        return bool(should_stop and should_stop())

    def _guard() -> None:
        if session is not None and session.logged_out():
            raise LoggedOutError(
                "LinkedIn session ended mid-run (logged out / auth wall). "
                "Re-run `linkedin-apply login`, then try again.")

    stopped = False
    new_count = seen_count = 0
    try:
        for title in sc.titles:
            if _stop():
                stopped = True
                break
            for location in sc.locations:
                if _stop():
                    stopped = True
                    break
                _guard()
                msg = f"Searching LinkedIn: {title} in {location}"
                console.log(msg)
                if progress:
                    progress(msg)
                try:
                    for job in search.scrape_search(session, settings, title, location):
                        collected.setdefault(job["job_id"], job)
                except Exception as exc:
                    console.log(f"[yellow]search failed for {title}/{location}: {exc}[/]")

            if not stopped and settings.enable_hiring_posts:
                _guard()
                console.log(f"Scanning hiring posts for: '{title}'")
                try:
                    for post in hiring_posts.scrape_hiring_posts(session, settings, title):
                        collected.setdefault(post["job_id"], post)
                except Exception as exc:
                    console.log(f"[yellow]hiring-post scan failed for {title}: {exc}[/]")

        # Fetch descriptions for new search jobs AND for previously-known jobs whose
        # description fetch failed before (so a transient failure isn't permanent).
        # Hiring posts already carry their text, so they're never re-fetched here.
        # Skip jobs that are already scored — apply opens the job page anyway, and
        # re-fetching dozens of stale empty descriptions blocks scheduled runs for hours.
        missing_desc = db.job_ids_missing_description()
        already_scored = db.scored_job_ids()
        desc_fetches = 0
        desc_cap = max(20, settings.search.max_jobs_per_search)
        for jid, job in collected.items():
            if _stop():
                stopped = True
                break
            if job.get("source") != "search" or job.get("description"):
                continue
            if jid not in known and jid not in missing_desc:
                continue
            if jid in already_scored:
                continue
            if desc_fetches >= desc_cap:
                console.log(
                    f"[yellow]Description fetch cap ({desc_cap}) reached; "
                    "skipping remaining jobs this run.[/]"
                )
                break
            _guard()
            label = job.get("title") or jid
            msg = f"Fetching description: {label}"
            console.log(msg)
            if progress:
                progress(msg)
            search.fetch_description(session, settings, job)
            desc_fetches += 1
    finally:
        # Persist whatever we collected, even on an early stop or logout (partial).
        new_count, seen_count = db.upsert_discovered(collected.values())
        db.log_run("discover", discovered=new_count,
                   notes=f"{len(collected)} seen, {seen_count} known"
                         + (", stopped early" if stopped else ""))

    console.log(
        f"Discovered {len(collected)} jobs ({new_count} new, {seen_count} already known)."
        + (" (stopped early)" if stopped else ""))
    return {"total": len(collected), "new": new_count, "seen": seen_count, "stopped": stopped}
