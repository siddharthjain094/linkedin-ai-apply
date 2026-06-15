"""Persistent Playwright browser session + LinkedIn login handling."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from agent.config import LoginMode, Settings

LINKEDIN_HOME = "https://www.linkedin.com/feed/"
LINKEDIN_LOGIN = "https://www.linkedin.com/login"

_AUTH_WALL_MARKERS = ("/login", "/authwall", "/checkpoint", "/uas/login")


class LoggedOutError(RuntimeError):
    """Raised mid-run when the LinkedIn session has ended (logged out / auth wall),
    so the caller can abort cleanly instead of silently failing every item."""

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class BrowserSession:
    """Wraps a persistent Chromium context so the LinkedIn session is reused."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._pw = None
        self._browser = None  # set in CDP / system-chrome attach modes
        self._proc = None     # Chrome we spawned ourselves (system-chrome mode)
        self.context = None
        self.page = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self.settings.ensure_dirs()
        self._pw = sync_playwright().start()

        if self.settings.cdp_url:
            self._attach_over_cdp(self.settings.cdp_url)
            self.page = self.context.new_page()
        elif self.settings.use_system_chrome:
            self._spawn_system_chrome_and_attach()
            self.page = self.context.new_page()
        else:
            self._launch_persistent()
            self.page = (
                self.context.pages[0] if self.context.pages else self.context.new_page()
            )
        return self

    def _attach_over_cdp(self, cdp_url: str) -> None:
        """Attach to a Chrome you started yourself (keeps your real session)."""
        try:
            self._browser = self._pw.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:
            raise RuntimeError(
                f"Could not attach to Chrome at {cdp_url}. Start Chrome with "
                f"remote debugging first, e.g.:\n"
                f"  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' "
                f"--remote-debugging-port=9222\n"
                f"(quit Chrome completely first so it relaunches with your profile)."
            ) from exc
        self.context = (
            self._browser.contexts[0]
            if self._browser.contexts
            else self._browser.new_context()
        )

    def _spawn_system_chrome_and_attach(self) -> None:
        """Launch the real Chrome (real Keychain -> real cookies) + attach via CDP.

        Chrome 136+ ignores --remote-debugging-port on the default profile dir, so
        we run against a dedicated copy dir (seeded once from your real profile if
        available). This both satisfies Chrome's restriction and avoids touching
        your live profile."""
        s = self.settings
        binary = _find_browser_binary(s.chrome_binary, s.browser_channel or "chrome")
        if not binary:
            raise RuntimeError(
                "Could not find your Chrome binary. Set CHROME_BINARY to its full path."
            )

        profile = s.chrome_profile_directory or "Default"
        work_dir = s.system_chrome_dir
        if not (work_dir / profile).exists():
            source = (
                Path(s.chrome_user_data_dir).expanduser()
                if s.chrome_user_data_dir
                else _default_chrome_user_data_dir()
            )
            _seed_profile_copy(source, profile, work_dir)

        user_data_dir = str(work_dir)
        _clear_stale_singleton_lock(work_dir)
        port = s.chrome_remote_port

        args = [
            binary,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            f"--profile-directory={s.chrome_profile_directory or 'Default'}",
            "--no-first-run",
            "--no-default-browser-check",
            "--restore-last-session=false",
        ]
        if s.headless:
            args.append("--headless=new")

        self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        cdp_http = f"http://127.0.0.1:{port}"
        if not _wait_for_cdp(cdp_http, timeout=25):
            self._terminate_proc()
            raise RuntimeError(
                f"Started Chrome but its debugging endpoint never came up on port {port}. "
                "Make sure Google Chrome is fully QUIT before running (its profile is "
                "locked while it's open), then try again."
            )
        self._attach_over_cdp(cdp_http)

    def _launch_persistent(self) -> None:
        """Launch Chromium/Chrome with a persistent user-data dir."""
        s = self.settings
        args = ["--disable-blink-features=AutomationControlled"]
        if s.chrome_profile_directory:
            args.append(f"--profile-directory={s.chrome_profile_directory}")

        kwargs: dict = {
            "user_data_dir": str(s.effective_user_data_dir),
            "headless": s.headless,
            "viewport": {"width": 1366, "height": 900},
            "args": args,
        }
        if s.browser_channel:
            # Use the system browser (real Chrome/Edge) and its native UA.
            kwargs["channel"] = s.browser_channel
        else:
            kwargs["user_agent"] = USER_AGENT

        try:
            self.context = self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:
            hint = ""
            if s.chrome_user_data_dir or s.browser_channel:
                hint = (
                    "\nIf you're pointing at your real Chrome profile, make sure Google "
                    "Chrome is fully QUIT (the profile is locked while it runs), or use "
                    "CDP_URL mode to attach to a running Chrome instead."
                )
            raise RuntimeError(f"Failed to launch browser: {exc}{hint}") from exc

    def __exit__(self, *exc):
        try:
            # In CDP mode the browser belongs to the user; just disconnect.
            if self._browser is not None:
                self._browser.close()
            elif self.context:
                self.context.close()
        finally:
            # If we spawned Chrome ourselves (system-chrome mode), shut it down.
            self._terminate_proc()
            if self._pw:
                self._pw.stop()

    def _terminate_proc(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        except Exception:
            pass
        finally:
            self._proc = None

    # ---- login -------------------------------------------------------------
    def has_auth_cookie(self) -> bool:
        """The `li_at` cookie is the authoritative signal that we're signed in."""
        try:
            cookies = self.context.cookies("https://www.linkedin.com")
        except Exception:
            return False
        return any(c.get("name") == "li_at" and c.get("value") for c in cookies)

    def logged_out(self) -> bool:
        """Cheap mid-run check that the session is still valid.

        True if the auth cookie is gone, or we've been bounced to a LinkedIn auth
        wall. Safe to call between items (no navigation)."""
        try:
            if not self.has_auth_cookie():
                return True
            url = self.page.url or ""
            if "linkedin.com" in url and any(m in url for m in _AUTH_WALL_MARKERS):
                return True
            return False
        except Exception:
            return False

    def is_logged_in(self) -> bool:
        # Cookie-based check is the most reliable and survives DOM/layout changes.
        if self.has_auth_cookie():
            return True
        self.page.goto(LINKEDIN_HOME, wait_until="domcontentloaded")
        self._sleep(2)
        # Re-check the cookie after loading (it may have just been set on login).
        if self.has_auth_cookie():
            return True
        url = self.page.url
        if any(x in url for x in ("/login", "/authwall", "/checkpoint", "/uas/login")):
            return False
        selectors = (
            "input[role='combobox'], #global-nav-search, .global-nav__me, "
            "img.global-nav__me-photo, [data-control-name='nav.settings']"
        )
        return self.page.locator(selectors).count() > 0

    def ensure_login(self, interactive: bool = True) -> bool:
        if self.is_logged_in():
            return True

        if self.settings.login_mode == LoginMode.credentials and self.settings.linkedin_email:
            self._login_with_credentials()
            self._sleep(2)
            if self.is_logged_in():
                return True

        if interactive and not self.settings.headless:
            self.page.goto(LINKEDIN_LOGIN, wait_until="domcontentloaded")
            print(
                "\n>>> Please log in to LinkedIn in the opened browser window "
                "(handle any 2FA). The session will be saved.\n"
                ">>> Press Enter here once you see your LinkedIn feed..."
            )
            try:
                input()
            except EOFError:
                self._sleep(60)
            return self.is_logged_in()

        return False

    def _login_with_credentials(self) -> None:
        self.page.goto(LINKEDIN_LOGIN, wait_until="domcontentloaded")
        self._sleep(1)
        try:
            self.page.fill("#username", self.settings.linkedin_email)
            self.page.fill("#password", self.settings.linkedin_password)
            self.page.click("button[type='submit']")
        except Exception:
            return
        # Give the user time to clear 2FA / checkpoint manually if needed.
        for _ in range(30):
            self._sleep(2)
            if self.is_logged_in():
                return

    # ---- pacing ------------------------------------------------------------
    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)


_BINARY_CANDIDATES = {
    "Darwin": {
        "chrome": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
        "msedge": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"],
    },
    "Linux": {
        "chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
        "msedge": ["microsoft-edge", "microsoft-edge-stable"],
    },
    "Windows": {
        "chrome": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ],
        "msedge": [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
    },
}


def _find_browser_binary(explicit: str, channel: str) -> str | None:
    if explicit and (Path(explicit).exists() or shutil.which(explicit)):
        return explicit
    candidates = _BINARY_CANDIDATES.get(platform.system(), {}).get(channel, [])
    for cand in candidates:
        if Path(cand).exists():
            return cand
        found = shutil.which(cand)
        if found:
            return found
    return None


def _default_chrome_user_data_dir() -> Path:
    sys = platform.system()
    home = Path.home()
    if sys == "Darwin":
        return home / "Library/Application Support/Google/Chrome"
    if sys == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", home)) / "Google/Chrome/User Data"
    return home / ".config/google-chrome"


# Big/regenerable subtrees we skip when seeding the copy (keeps it small).
_SEED_IGNORE = {
    "Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache",
    "DawnWebGPUCache", "GrShaderCache", "ShaderCache", "Service Worker",
    "Extension State", "blob_storage", "Crashpad", "component_crx_cache",
    "optimization_guide_model_store",
}


def _seed_profile_copy(source_udd: Path, profile: str, dest_udd: Path) -> None:
    """One-time best-effort copy of the essentials so the spawned Chrome starts
    from your real cookies/login when available (no-op if source is missing)."""
    try:
        dest_udd.mkdir(parents=True, exist_ok=True)
        if (source_udd / "Local State").exists():
            shutil.copy2(source_udd / "Local State", dest_udd / "Local State")
        src_profile = source_udd / profile
        if not src_profile.is_dir():
            (dest_udd / profile).mkdir(parents=True, exist_ok=True)
            return
        shutil.copytree(
            src_profile,
            dest_udd / profile,
            ignore=shutil.ignore_patterns(*_SEED_IGNORE),
            dirs_exist_ok=True,
        )
    except Exception:
        # A partial/empty copy is fine - user can just log in once in the window.
        (dest_udd / profile).mkdir(parents=True, exist_ok=True)


def _wait_for_cdp(cdp_http: str, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{cdp_http}/json/version", timeout=2) as resp:
                if resp.status == 200:
                    json.loads(resp.read() or b"{}")
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def _clear_stale_singleton_lock(user_data_dir: Path) -> None:
    """Remove a leftover SingletonLock if no live PID owns it.

    Chrome refuses to start a profile that still has a SingletonLock from an
    uncleanly-closed instance; if the referenced PID is dead we can safely drop
    it. (If Chrome is genuinely running this is a no-op and launch will fail with
    a clear message.)"""
    lock = user_data_dir / "SingletonLock"
    try:
        if not lock.is_symlink() and not lock.exists():
            return
        target = os.readlink(lock) if lock.is_symlink() else ""
        pid = None
        if "-" in target:
            try:
                pid = int(target.rsplit("-", 1)[1])
            except ValueError:
                pid = None
        alive = False
        if pid is not None:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        if not alive:
            lock.unlink()
    except Exception:
        pass


@contextmanager
def open_session(settings: Settings) -> Iterator[BrowserSession]:
    sess = BrowserSession(settings)
    sess.__enter__()
    try:
        yield sess
    finally:
        sess.__exit__()
