"""Configuration loading.

Precedence (lowest -> highest):
  1. config.yaml         (non-secret defaults, committed)
  2. intake.yaml         (per-user profile; may include a `search:` override)
  3. .env / environment  (secrets + machine-specific overrides)
  4. CLI flags           (applied by the caller via `settings.override(...)`)
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class SubmitMode(str, Enum):
    auto = "auto"
    easy_only = "easy_only"
    review = "review"


class LoginMode(str, Enum):
    persistent = "persistent"
    credentials = "credentials"


class SearchConfig(BaseModel):
    titles: list[str] = Field(default_factory=lambda: ["Software Engineer"])
    keywords: str = ""
    locations: list[str] = Field(default_factory=lambda: ["United States"])
    remote: bool = True
    on_site: bool = False
    hybrid: bool = True
    experience_levels: list[str] = Field(default_factory=list)
    date_posted: str = "past-week"
    easy_apply_only: bool = False
    max_jobs_per_search: int = 60


class Settings(BaseModel):
    # Policy
    submit_mode: SubmitMode = SubmitMode.auto
    dry_run: bool = False

    # Matching / volume
    match_threshold: int = 70
    max_applies_per_run: int = 20
    min_delay_ms: int = 1200
    max_delay_ms: int = 4200

    # Browser
    headless: bool = False
    login_mode: LoginMode = LoginMode.persistent
    browser_profile_dir: str = "browser_profile"
    linkedin_email: str = ""
    linkedin_password: str = ""
    # Reuse your real browser so you're already logged in:
    #   browser_channel        = "" (bundled Chromium) | "chrome" | "msedge"
    #   chrome_user_data_dir   = path to your real Chrome user-data dir (Chrome
    #                            must be CLOSED while the bot uses it)
    #   chrome_profile_directory = which profile inside it, e.g. "Default"
    #   cdp_url                = attach to an ALREADY-RUNNING Chrome instead of
    #                            launching one (you keep using your browser),
    #                            e.g. "http://localhost:9222"
    browser_channel: str = ""
    chrome_user_data_dir: str = ""
    chrome_profile_directory: str = ""
    cdp_url: str = ""
    # use_system_chrome: the tool launches your REAL Chrome itself (so it uses
    # the macOS Keychain and your existing cookies/login) with a debugging port,
    # then attaches over CDP. This is the reliable way to reuse your session on
    # macOS - launching your profile *through Playwright* cannot decrypt existing
    # cookies (Playwright forces --use-mock-keychain). Chrome must be QUIT first.
    use_system_chrome: bool = False
    chrome_remote_port: int = 9222
    chrome_binary: str = ""  # optional explicit path to the Chrome/Edge binary

    # LLM
    llm_model: str = "openai/gpt-4o-mini"
    llm_temperature: float = 0.4
    llm_api_base: str = ""
    # Universal API key, used for EVERY LLM call (resume parsing, scoring,
    # cover letters, screening answers) regardless of provider. Takes precedence
    # over provider-specific env vars (OPENAI_API_KEY, etc.).
    llm_api_key: str = ""

    # Discovery
    enable_hiring_posts: bool = True

    # Documents
    resume_output_format: str = "pdf"

    # Paths
    db_path: str = "data/state.db"
    sheet_path: str = "data/jobs.xlsx"
    master_resume_path: str = "profile/master_resume.docx"
    intake_path: str = "profile/intake.yaml"
    learned_answers_path: str = "profile/learned_answers.yaml"
    output_dir: str = "output"

    # Nested
    search: SearchConfig = Field(default_factory=SearchConfig)
    blacklist_companies: list[str] = Field(default_factory=list)
    blacklist_title_keywords: list[str] = Field(default_factory=list)

    # The raw intake dict (personal info, screening answers, eeo, etc.)
    intake: dict[str, Any] = Field(default_factory=dict)

    # ---- path helpers ------------------------------------------------------
    def _abs(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def db_file(self) -> Path:
        return self._abs(self.db_path)

    @property
    def sheet_file(self) -> Path:
        return self._abs(self.sheet_path)

    @property
    def master_resume_file(self) -> Path:
        return self._abs(self.master_resume_path)

    @property
    def learned_answers_file(self) -> Path:
        return self._abs(self.learned_answers_path)

    @property
    def output_path(self) -> Path:
        return self._abs(self.output_dir)

    @property
    def browser_profile_path(self) -> Path:
        return self._abs(self.browser_profile_dir)

    @property
    def system_chrome_dir(self) -> Path:
        """Dedicated user-data dir used by USE_SYSTEM_CHROME (seeded from your real
        profile). Kept separate because Chrome blocks remote debugging on the
        default dir and we never want to drive your live profile directly."""
        return self._abs(self.browser_profile_dir + "_chrome")

    @property
    def effective_user_data_dir(self) -> Path:
        """Where Chromium stores the session.

        If you point CHROME_USER_DATA_DIR at your real Chrome profile dir we use
        that (so your existing LinkedIn login/cookies are reused); otherwise the
        app's own isolated profile directory."""
        if self.chrome_user_data_dir:
            return Path(self.chrome_user_data_dir).expanduser()
        return self.browser_profile_path

    def ensure_dirs(self) -> None:
        for d in (
            self.db_file.parent,
            self.sheet_file.parent,
            self.output_path / "resumes",
            self.output_path / "cover_letters",
            self.output_path / "screenshots",
            self.browser_profile_path,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def override(self, **kwargs: Any) -> "Settings":
        """Return a copy with the given (non-None) keys overridden (CLI flags)."""
        updates = {k: v for k, v in kwargs.items() if v is not None}
        return self.model_copy(update=updates)


# ---- merge helpers ---------------------------------------------------------

_ENV_KEYS = {
    "submit_mode": ("SUBMIT_MODE", str),
    "dry_run": ("DRY_RUN", "bool"),
    "match_threshold": ("MATCH_THRESHOLD", int),
    "max_applies_per_run": ("MAX_APPLIES_PER_RUN", int),
    "min_delay_ms": ("MIN_DELAY_MS", int),
    "max_delay_ms": ("MAX_DELAY_MS", int),
    "headless": ("HEADLESS", "bool"),
    "login_mode": ("LOGIN_MODE", str),
    "browser_profile_dir": ("BROWSER_PROFILE_DIR", str),
    "browser_channel": ("BROWSER_CHANNEL", str),
    "chrome_user_data_dir": ("CHROME_USER_DATA_DIR", str),
    "chrome_profile_directory": ("CHROME_PROFILE_DIRECTORY", str),
    "cdp_url": ("CDP_URL", str),
    "use_system_chrome": ("USE_SYSTEM_CHROME", "bool"),
    "chrome_remote_port": ("CHROME_REMOTE_PORT", int),
    "chrome_binary": ("CHROME_BINARY", str),
    "linkedin_email": ("LINKEDIN_EMAIL", str),
    "linkedin_password": ("LINKEDIN_PASSWORD", str),
    "llm_model": ("LLM_MODEL", str),
    "llm_temperature": ("LLM_TEMPERATURE", float),
    "llm_api_base": ("LLM_API_BASE", str),
    "llm_api_key": ("LLM_API_KEY", str),
    "enable_hiring_posts": ("ENABLE_HIRING_POSTS", "bool"),
    "resume_output_format": ("RESUME_OUTPUT_FORMAT", str),
    "db_path": ("DB_PATH", str),
    "sheet_path": ("SHEET_PATH", str),
    "master_resume_path": ("MASTER_RESUME_PATH", str),
    "intake_path": ("INTAKE_PATH", str),
    "learned_answers_path": ("LEARNED_ANSWERS_PATH", str),
    "output_dir": ("OUTPUT_DIR", str),
}


def _coerce(raw: str, kind: Any) -> Any:
    if kind == "bool":
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return kind(raw)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings(env_file: str | None = None) -> Settings:
    load_dotenv(env_file or (PROJECT_ROOT / ".env"), override=False)

    data: dict[str, Any] = _load_yaml(PROJECT_ROOT / "config.yaml")

    # Resolve intake path (env may relocate it) and merge.
    intake_path = os.getenv("INTAKE_PATH", data.get("intake_path", "profile/intake.yaml"))
    intake_abs = Path(intake_path)
    if not intake_abs.is_absolute():
        intake_abs = PROJECT_ROOT / intake_abs
    intake_data = _load_yaml(intake_abs)
    if intake_data:
        data["intake"] = intake_data
        # Allow intake.yaml to override the search block + match_threshold.
        if "search" in intake_data:
            data["search"] = {**data.get("search", {}), **intake_data["search"]}
            if "match_threshold" in intake_data["search"]:
                data["match_threshold"] = intake_data["search"]["match_threshold"]

    # Merge learned answers (from resolved human_review jobs) into the intake's
    # screening_answers so the form fillers can use them on the next run. Learned
    # answers take precedence over the original intake answers.
    learned_path = os.getenv(
        "LEARNED_ANSWERS_PATH", data.get("learned_answers_path", "profile/learned_answers.yaml")
    )
    learned_abs = Path(learned_path)
    if not learned_abs.is_absolute():
        learned_abs = PROJECT_ROOT / learned_abs
    learned = _load_yaml(learned_abs)
    if learned:
        intake = data.setdefault("intake", {})
        merged = {**(intake.get("screening_answers") or {}), **learned}
        intake["screening_answers"] = merged

    # Environment overrides.
    for field, (env_name, kind) in _ENV_KEYS.items():
        raw = os.getenv(env_name)
        if raw is not None and raw != "":
            data[field] = _coerce(raw, kind)

    return Settings(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
