"""Single-flight background runner for the heavy browser/LLM actions.

The web server is async, but discover/apply drive Playwright's *sync* API and the
LLM, which must not run on the event loop. Each action therefore runs on a
dedicated worker thread. Only one action runs at a time (it also takes the shared
run-lock so it can't clash with a CLI invocation)."""

from __future__ import annotations

import threading
import traceback
from datetime import datetime, timezone
from typing import Callable, Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActionRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self.action: Optional[str] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.progress: str = ""

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def should_stop(self) -> bool:
        """Cooperative-cancellation flag the pipeline checks at safe points."""
        return self._cancel.is_set()

    def request_stop(self) -> bool:
        """Ask the in-flight action to stop. Returns False if nothing is running."""
        if not self.running:
            return False
        self._cancel.set()
        return True

    def start(self, name: str, fn: Callable[[], dict]) -> bool:
        """Begin running ``fn`` in the background. Returns False if one is already
        in progress."""
        with self._lock:
            if self.running:
                return False
            self._cancel.clear()
            self.action = name
            self.started_at = _now()
            self.finished_at = None
            self.result = None
            self.error = None
            self.progress = ""

            def _target() -> None:
                try:
                    self.result = fn() or {}
                except Exception as exc:  # surface to the UI, don't crash the server
                    self.error = f"{type(exc).__name__}: {exc}"
                    traceback.print_exc()
                finally:
                    self.finished_at = _now()

            self._thread = threading.Thread(target=_target, name=f"action-{name}", daemon=True)
            self._thread.start()
            return True

    def set_progress(self, message: str) -> None:
        """Best-effort live status for the UI (safe from worker threads)."""
        with self._lock:
            self.progress = message or ""

    def ack(self) -> bool:
        """Clear completed action state after the UI has consumed it."""
        with self._lock:
            if self.running:
                return False
            if not self.action and not self.error and not self.result:
                return True
            self.action = None
            self.error = None
            self.result = None
            self.finished_at = None
            self.progress = ""
            return True

    def snapshot(self) -> dict:
        with self._lock:
            progress = self.progress
        return {
            "running": self.running,
            "action": self.action,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
            "progress": progress,
            "stop_requested": self._cancel.is_set(),
        }
