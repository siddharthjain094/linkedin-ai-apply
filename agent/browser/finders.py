"""Robust locate/click/type helpers with human-like pacing.

Inspired by GodsScion/Auto_job_applier_linkedIn's clickers_and_finders, adapted
to Playwright. Everything is best-effort and never raises on a miss.
"""

from __future__ import annotations

import random
import time


def human_delay(min_ms: int, max_ms: int) -> None:
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


def try_click(page, selector: str, timeout: int = 4000) -> bool:
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout)
        loc.click()
        return True
    except Exception:
        return False


def click_by_text(page, text: str, timeout: int = 4000) -> bool:
    for sel in (
        f"button:has-text('{text}')",
        f"a:has-text('{text}')",
        f"[aria-label*='{text}' i]",
    ):
        if try_click(page, sel, timeout=timeout):
            return True
    return False


def try_fill(page, selector: str, value: str, timeout: int = 4000) -> bool:
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout)
        loc.fill("")
        for ch in value:
            loc.type(ch, delay=random.uniform(20, 70))
        return True
    except Exception:
        return False


def exists(page, selector: str) -> bool:
    try:
        return page.locator(selector).count() > 0
    except Exception:
        return False


def text_of(page, selector: str, default: str = "") -> str:
    try:
        loc = page.locator(selector).first
        if loc.count() == 0:
            return default
        return (loc.inner_text(timeout=2000) or default).strip()
    except Exception:
        return default


def attr_of(page, selector: str, attr: str, default: str = "") -> str:
    try:
        loc = page.locator(selector).first
        if loc.count() == 0:
            return default
        return (loc.get_attribute(attr) or default).strip()
    except Exception:
        return default
