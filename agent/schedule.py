"""Schedule configuration and OS task installation (launchd / Task Scheduler).

The scheduler runs the existing CLI pipeline (``linkedin-apply schedule-run`` by default)
on a user-configured interval or daily time. Configuration lives in
``config.yaml`` under ``schedule:``.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from agent.config import (
    ALLOWED_INTERVAL_HOURS,
    PROJECT_ROOT,
    ScheduleConfig,
    ScheduleMode,
    ScheduleWorkflow,
    Settings,
)

LAUNCHD_LABEL = "com.linkedin-ai-apply.daily"
LAUNCHD_FILENAME = f"{LAUNCHD_LABEL}.plist"
WINDOWS_TASK_NAME = "LinkedIn-AI-Apply"
WINDOWS_RUN_SCRIPT = "run_scheduled.cmd"

SCHTASKS_DAY_MAP: dict[str, str] = {
    "mon": "MON",
    "tue": "TUE",
    "wed": "WED",
    "thu": "THU",
    "fri": "FRI",
    "sat": "SAT",
    "sun": "SUN",
}

DEFAULT_DAYS = ["mon", "tue", "wed", "thu", "fri"]

DAY_ALIASES: dict[str, int] = {
    "sun": 0,
    "sunday": 0,
    "mon": 1,
    "monday": 1,
    "tue": 2,
    "tues": 2,
    "tuesday": 2,
    "wed": 3,
    "wednesday": 3,
    "thu": 4,
    "thur": 4,
    "thurs": 4,
    "thursday": 4,
    "fri": 5,
    "friday": 5,
    "sat": 6,
    "saturday": 6,
}

CRON_DAY_MAP: dict[int, str] = {
    0: "0",  # Sunday
    1: "1",
    2: "2",
    3: "3",
    4: "4",
    5: "5",
    6: "6",
}

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

INTERVAL_LABELS: dict[int, str] = {
    1: "Every hour",
    2: "Every 2 hours",
    4: "Every 4 hours",
    6: "Every 6 hours",
    12: "Every 12 hours",
    24: "Every 24 hours",
}


class ScheduleError(ValueError):
    """Invalid schedule input or install failure."""


@dataclass(frozen=True)
class ParsedTime:
    hour: int
    minute: int

    @property
    def as_hhmm(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    message: str
    plist_path: str = ""
    loaded: bool = False


def parse_interval_hours(value: int | str) -> int:
    """Validate interval hours (1, 2, 4, 6, 12, or 24)."""
    try:
        hours = int(value)
    except (TypeError, ValueError) as exc:
        raise ScheduleError(f"Invalid interval {value!r}.") from exc
    if hours not in ALLOWED_INTERVAL_HOURS:
        allowed = ", ".join(str(h) for h in ALLOWED_INTERVAL_HOURS)
        raise ScheduleError(f"Interval must be one of: {allowed} hours.")
    return hours


def interval_seconds(hours: int) -> int:
    return parse_interval_hours(hours) * 3600


def schedule_description(schedule: ScheduleConfig) -> str:
    """Human-readable summary for CLI/UI."""
    if schedule.mode == ScheduleMode.interval:
        label = INTERVAL_LABELS.get(schedule.interval_hours, f"Every {schedule.interval_hours}h")
        return label
    parsed = parse_time(schedule.time)
    return f"Daily at {parsed.as_hhmm} ({', '.join(schedule.days)})"


def parse_time(value: str) -> ParsedTime:
    """Parse ``HH:MM`` (24-hour) and return hour/minute."""
    raw = (value or "").strip()
    match = TIME_RE.match(raw)
    if not match:
        raise ScheduleError(f"Invalid time {value!r}; use 24-hour HH:MM (e.g. 09:00).")
    return ParsedTime(hour=int(match.group(1)), minute=int(match.group(2)))


def parse_days(raw: str | list[str] | None) -> list[str]:
    """Normalize day names from a comma-separated string or list."""
    if raw is None:
        return list(DEFAULT_DAYS)
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    else:
        parts = [str(p).strip().lower() for p in raw if str(p).strip()]
    if not parts:
        raise ScheduleError("At least one weekday is required.")
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if part not in DAY_ALIASES:
            valid = ", ".join(sorted({k for k in DAY_ALIASES if len(k) == 3}))
            raise ScheduleError(f"Unknown day {part!r}; use: {valid}")
        canonical = _canonical_day(part)
        if canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out


def _canonical_day(part: str) -> str:
    idx = DAY_ALIASES[part]
    for name, val in DAY_ALIASES.items():
        if val == idx and len(name) == 3:
            return name
    return part[:3]


def weekday_numbers(days: list[str]) -> list[int]:
    """Map day abbreviations to launchd weekday integers (0=Sunday)."""
    nums = sorted({DAY_ALIASES[d.lower()] for d in days})
    return nums


def resolve_linkedin_apply_bin() -> Path:
    """Return the ``linkedin-apply`` executable path, or raise."""
    found = shutil.which("linkedin-apply")
    if found:
        return Path(found)
    # Dev fallback: same interpreter, module entry.
    return Path(sys.executable)


def workflow_argv(schedule: ScheduleConfig) -> list[str]:
    """CLI arguments after the executable for the configured workflow."""
    wf = schedule.workflow
    if wf == ScheduleWorkflow.schedule_run:
        args = ["schedule-run"]
    elif wf == ScheduleWorkflow.find:
        args = ["find"]
    elif wf == ScheduleWorkflow.apply:
        args = ["apply"]
        if schedule.only_approved:
            args.append("--only-approved")
    else:
        raise ScheduleError(f"Unknown workflow: {wf}")
    if schedule.skip_generate and wf in (ScheduleWorkflow.schedule_run, ScheduleWorkflow.apply):
        args.append("--no-generate")
    return args


def build_program_arguments(settings: Settings, schedule: ScheduleConfig) -> list[str]:
    exe = resolve_linkedin_apply_bin()
    args = workflow_argv(schedule)
    if exe.name == Path(sys.executable).name and not shutil.which("linkedin-apply"):
        return [str(exe), "-m", "agent", *args]
    return [str(exe), *args]


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return sys.platform == "win32"


def supports_auto_install() -> bool:
    return is_macos() or is_windows()


def install_platform() -> str | None:
    if is_macos():
        return "macos"
    if is_windows():
        return "windows"
    return None


def install_label() -> str:
    if is_macos():
        return "Install on Mac"
    if is_windows():
        return "Install on Windows"
    return "Manual install"


def launchd_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / LAUNCHD_FILENAME


def windows_run_script_path(settings: Settings) -> Path:
    return settings.db_file.parent / WINDOWS_RUN_SCRIPT


def _quote_windows(value: str) -> str:
    """Quote a path/argument for cmd.exe and schtasks."""
    if not value:
        return '""'
    if " " in value or '"' in value:
        return f'"{value.replace(chr(34), chr(92) + chr(34))}"'
    return value


def build_windows_run_script(settings: Settings, schedule: ScheduleConfig) -> str:
    """Return a ``.cmd`` batch file that runs the scheduled workflow."""
    proj = str(PROJECT_ROOT.resolve())
    log = str((settings.db_file.parent / "daily.log").resolve())
    args = build_program_arguments(settings, schedule)
    exe = _quote_windows(args[0])
    tail = " ".join(_quote_windows(a) for a in args[1:])
    cmd = f"{exe} {tail}".strip()
    return (
        "@echo off\r\n"
        f'cd /d {_quote_windows(proj)}\r\n'
        f"{cmd} >> {_quote_windows(log)} 2>&1\r\n"
    )


def build_schtasks_create_args(schedule: ScheduleConfig, script_path: Path) -> list[str]:
    """Arguments for ``schtasks /Create`` (Windows Task Scheduler)."""
    tr = str(script_path.resolve())
    if " " in tr:
        tr = f'\\"{tr}\\"'
    args = [
        "schtasks", "/Create",
        "/TN", WINDOWS_TASK_NAME,
        "/TR", tr,
        "/F",
    ]
    if schedule.mode == ScheduleMode.interval:
        hours = parse_interval_hours(schedule.interval_hours)
        if hours == 24:
            args.extend(["/SC", "DAILY", "/ST", "00:00"])
        else:
            args.extend(["/SC", "HOURLY", "/MO", str(hours)])
    else:
        parsed = parse_time(schedule.time)
        days = ",".join(SCHTASKS_DAY_MAP[d.lower()] for d in schedule.days)
        args.extend(["/SC", "WEEKLY", "/D", days, "/ST", parsed.as_hhmm])
    return args


def build_manual_install_message(settings: Settings) -> str:
    """Instructions when automatic install is unavailable (Linux, etc.)."""
    cron = build_cron_line(settings, settings.schedule)
    return (
        "Automatic install is supported on macOS (launchd) and Windows (Task Scheduler). "
        f"On this platform, add a cron entry:\n{cron}"
    )


def build_launchd_plist(settings: Settings, schedule: ScheduleConfig) -> dict[str, Any]:
    """Build a launchd plist dict (testable without writing files)."""
    if not schedule.enabled:
        raise ScheduleError("Schedule is disabled; enable it before installing.")

    log_path = str(settings.db_file.parent / "daily.log")
    plist: dict[str, Any] = {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": build_program_arguments(settings, schedule),
        "WorkingDirectory": str(PROJECT_ROOT),
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
        "EnvironmentVariables": {
            "PATH": _path_env(),
        },
    }

    if schedule.mode == ScheduleMode.interval:
        plist["StartInterval"] = interval_seconds(schedule.interval_hours)
        # Run once as soon as launchd loads the agent, then every StartInterval.
        plist["RunAtLoad"] = True
        return plist

    parsed = parse_time(schedule.time)
    days = weekday_numbers(schedule.days)
    intervals = [
        {"Weekday": day, "Hour": parsed.hour, "Minute": parsed.minute}
        for day in days
    ]
    plist["StartCalendarInterval"] = intervals if len(intervals) > 1 else intervals[0]
    return plist


def build_cron_line(settings: Settings, schedule: ScheduleConfig) -> str:
    """Return a single cron line for non-macOS environments."""
    args = " ".join(build_program_arguments(settings, schedule))
    log_path = settings.db_file.parent / "daily.log"
    prefix = f"cd {PROJECT_ROOT} && {args} >> {log_path} 2>&1"

    if schedule.mode == ScheduleMode.interval:
        hours = parse_interval_hours(schedule.interval_hours)
        if hours == 1:
            return f"0 * * * * {prefix}"
        if hours == 24:
            return f"0 0 * * * {prefix}"
        return f"0 */{hours} * * * {prefix}"

    parsed = parse_time(schedule.time)
    days = weekday_numbers(schedule.days)
    cron_dow = ",".join(CRON_DAY_MAP[d] for d in days)
    return (
        f"{parsed.minute} {parsed.hour} * * {cron_dow} {prefix}"
    )


def _path_env() -> str:
    path = os.environ.get("PATH", "")
    # Ensure common install locations are present for cron/launchd.
    extras = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(Path.home() / ".local" / "bin"),
    ]
    parts = path.split(":") if path else []
    for extra in extras:
        if extra not in parts:
            parts.append(extra)
    return ":".join(p for p in parts if p)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def config_yaml_path() -> Path:
    return PROJECT_ROOT / "config.yaml"


def save_schedule_config(schedule: ScheduleConfig, path: Path | None = None) -> Path:
    """Merge ``schedule`` into ``config.yaml`` and return the path written."""
    target = path or config_yaml_path()
    data = _load_yaml(target)
    data["schedule"] = schedule.model_dump(mode="json")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    return target


def merge_schedule_update(current: ScheduleConfig, **updates: Any) -> ScheduleConfig:
    """Apply partial updates with validation."""
    data = current.model_dump()
    for key, value in updates.items():
        if value is None:
            continue
        if key == "time":
            data["time"] = parse_time(str(value)).as_hhmm
        elif key == "days":
            data["days"] = parse_days(value)
        elif key == "mode":
            data["mode"] = ScheduleMode(str(value)).value
        elif key == "interval_hours":
            data["interval_hours"] = parse_interval_hours(value)
        elif key == "workflow":
            wf = str(value)
            if wf == "daily":
                wf = ScheduleWorkflow.schedule_run.value
            data["workflow"] = ScheduleWorkflow(wf).value
        elif key == "enabled":
            data["enabled"] = bool(value)
        elif key == "only_approved":
            data["only_approved"] = bool(value)
        elif key == "skip_generate":
            data["skip_generate"] = bool(value)
        else:
            raise ScheduleError(f"Unknown schedule field: {key}")
    return ScheduleConfig(**data)


def plist_is_loaded(plist_path: Path, run_cmd: Callable[..., subprocess.CompletedProcess] | None = None) -> bool:
    """True if launchd knows about this job label."""
    if not is_macos():
        return False
    runner = run_cmd or subprocess.run
    try:
        proc = runner(
            ["launchctl", "print", f"gui/{_gui_uid()}/{LAUNCHD_LABEL}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0
    except OSError:
        return False


def windows_task_is_registered(run_cmd: Callable[..., subprocess.CompletedProcess] | None = None) -> bool:
    """True if the Windows scheduled task exists."""
    if not is_windows():
        return False
    runner = run_cmd or subprocess.run
    try:
        proc = runner(
            ["schtasks", "/Query", "/TN", WINDOWS_TASK_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0
    except OSError:
        return False


def _gui_uid() -> int:
    try:
        return os.getuid()
    except AttributeError:
        return 501


def _schedule_install_state(settings: Settings, run_cmd: Callable[..., subprocess.CompletedProcess] | None) -> tuple[bool, bool, str]:
    """Return (installed, loaded/registered, agent_path)."""
    if is_macos():
        path = launchd_agent_path()
        installed = path.exists()
        loaded = plist_is_loaded(path, run_cmd) if installed else False
        return installed, loaded, str(path)
    if is_windows():
        script = windows_run_script_path(settings)
        registered = windows_task_is_registered(run_cmd)
        return script.exists() and registered, registered, str(script)
    return False, False, ""


def schedule_entry_status(schedule: ScheduleConfig, installed: bool, loaded: bool) -> str:
    """Human-readable lifecycle state for the single saved schedule."""
    if loaded:
        return "active"
    if installed:
        return "installed"
    if schedule.enabled:
        return "saved"
    return "disabled"


def build_schedule_entry(
    settings: Settings,
    *,
    installed: bool,
    loaded: bool,
) -> dict[str, Any]:
    """The one saved schedule record (only one slot is supported)."""
    schedule = settings.schedule
    parsed = parse_time(schedule.time)
    status = schedule_entry_status(schedule, installed, loaded)
    wf_label = {
        ScheduleWorkflow.schedule_run: "Search + apply",
        ScheduleWorkflow.find: "Search only",
        ScheduleWorkflow.apply: "Apply queued jobs",
    }.get(schedule.workflow, schedule.workflow.value)
    return {
        "id": "primary",
        "description": schedule_description(schedule),
        "enabled": schedule.enabled,
        "mode": schedule.mode.value,
        "interval_hours": schedule.interval_hours,
        "time": parsed.as_hhmm,
        "days": list(schedule.days),
        "workflow": schedule.workflow.value,
        "workflow_label": wf_label,
        "only_approved": schedule.only_approved,
        "skip_generate": schedule.skip_generate,
        "status": status,
        "status_label": {
            "active": "Running",
            "installed": "Installed (not loaded)",
            "saved": "Saved (not installed)",
            "disabled": "Disabled",
        }.get(status, status),
        "loaded": loaded,
        "installed": installed,
        "can_delete": loaded or installed,
        "task_name": WINDOWS_TASK_NAME if is_windows() else LAUNCHD_LABEL,
        "command": build_program_arguments(settings, schedule),
        "command_line": " ".join(build_program_arguments(settings, schedule)),
        "log_path": str(settings.db_file.parent / "daily.log"),
        "agent_path": _schedule_install_state(settings, None)[2],
    }


def build_schedules_list(settings: Settings) -> list[dict[str, Any]]:
    """Return saved schedules (at most one — only one may be active at a time)."""
    installed, loaded, _ = _schedule_install_state(settings, None)
    schedule = settings.schedule
    if not schedule.enabled and not installed and not loaded:
        return []
    return [build_schedule_entry(settings, installed=installed, loaded=loaded)]


def delete_active_schedule(
    settings: Settings,
    run_cmd: Callable[..., subprocess.CompletedProcess] | None = None,
) -> InstallResult:
    """Remove the OS scheduler task and disable the saved schedule."""
    result = uninstall_schedule(settings, run_cmd)
    if not result.ok:
        return result
    updated = merge_schedule_update(settings.schedule, enabled=False)
    save_schedule_config(updated)
    return InstallResult(
        ok=True,
        message=f"{result.message} Schedule disabled in config.",
    )


def schedule_status(settings: Settings) -> dict[str, Any]:
    """Full schedule status for CLI and API."""
    schedule = settings.schedule
    parsed = parse_time(schedule.time)
    installed, loaded, agent_path = _schedule_install_state(settings, None)
    schedules = build_schedules_list(settings)
    active = next((s for s in schedules if s["status"] == "active"), None)
    return {
        "enabled": schedule.enabled,
        "mode": schedule.mode.value,
        "interval_hours": schedule.interval_hours,
        "interval_options": list(ALLOWED_INTERVAL_HOURS),
        "description": schedule_description(schedule),
        "time": parsed.as_hhmm,
        "days": list(schedule.days),
        "workflow": schedule.workflow.value,
        "only_approved": schedule.only_approved,
        "skip_generate": schedule.skip_generate,
        "platform": install_platform() or sys.platform,
        "supports_install": supports_auto_install(),
        "install_platform": install_platform(),
        "install_label": install_label(),
        "task_name": WINDOWS_TASK_NAME if is_windows() else LAUNCHD_LABEL,
        "installed": installed,
        "loaded": loaded,
        "agent_path": agent_path,
        "log_path": str(settings.db_file.parent / "daily.log"),
        "command": build_program_arguments(settings, schedule),
        "cron_line": build_cron_line(settings, schedule),
        "manual_install": build_manual_install_message(settings),
        "config_path": str(config_yaml_path()),
        "max_schedules": 1,
        "schedules": schedules,
        "active_schedule": active,
    }


def install_schedule(
    settings: Settings,
    run_cmd: Callable[..., subprocess.CompletedProcess] | None = None,
) -> InstallResult:
    """Install the schedule (launchd on macOS, Task Scheduler on Windows)."""
    if not supports_auto_install():
        return InstallResult(ok=False, message=build_manual_install_message(settings))

    schedule = settings.schedule
    if not schedule.enabled:
        return InstallResult(ok=False, message="Enable the schedule first (schedule set --enable).")

    if is_macos():
        return _install_launchd(settings, schedule, run_cmd)
    return _install_windows_task(settings, schedule, run_cmd)


def _install_launchd(
    settings: Settings,
    schedule: ScheduleConfig,
    run_cmd: Callable[..., subprocess.CompletedProcess] | None,
) -> InstallResult:
    runner = run_cmd or subprocess.run
    plist_data = build_launchd_plist(settings, schedule)
    agent_path = launchd_agent_path()
    agent_path.parent.mkdir(parents=True, exist_ok=True)

    if agent_path.exists():
        _launchd_unload(agent_path, runner)

    with agent_path.open("wb") as fh:
        plistlib.dump(plist_data, fh)

    load_err = _launchd_load(agent_path, runner)
    if load_err:
        return InstallResult(
            ok=False,
            message=load_err,
            plist_path=str(agent_path),
            loaded=False,
        )

    loaded = plist_is_loaded(agent_path, runner)
    when = (
        "First run starts now; repeats "
        f"{schedule_description(schedule).lower()}."
        if schedule.mode == ScheduleMode.interval
        else f"Next run at the scheduled time ({schedule_description(schedule)})."
    )
    return InstallResult(
        ok=True,
        message=f"Installed schedule: {schedule_description(schedule)}. {when} "
                f"Logs: {settings.db_file.parent / 'daily.log'}",
        plist_path=str(agent_path),
        loaded=loaded,
    )


def _install_windows_task(
    settings: Settings,
    schedule: ScheduleConfig,
    run_cmd: Callable[..., subprocess.CompletedProcess] | None,
) -> InstallResult:
    runner = run_cmd or subprocess.run
    script_path = windows_run_script_path(settings)
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(build_windows_run_script(settings, schedule), encoding="utf-8")

    if windows_task_is_registered(runner):
        runner(
            ["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"],
            capture_output=True,
            text=True,
            check=False,
        )

    create_args = build_schtasks_create_args(schedule, script_path)
    proc = runner(create_args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return InstallResult(
            ok=False,
            message=err or "schtasks /Create failed",
            plist_path=str(script_path),
            loaded=False,
        )

    if schedule.mode == ScheduleMode.interval:
        runner(
            ["schtasks", "/Run", "/TN", WINDOWS_TASK_NAME],
            capture_output=True,
            text=True,
            check=False,
        )

    loaded = windows_task_is_registered(runner)
    when = (
        "First run started now; repeats "
        f"{schedule_description(schedule).lower()}."
        if schedule.mode == ScheduleMode.interval
        else f"Next run at the scheduled time ({schedule_description(schedule)})."
    )
    return InstallResult(
        ok=True,
        message=f"Installed Windows task '{WINDOWS_TASK_NAME}': {schedule_description(schedule)}. {when} "
                f"Logs: {settings.db_file.parent / 'daily.log'}",
        plist_path=str(script_path),
        loaded=loaded,
    )


def uninstall_schedule(
    settings: Settings | None = None,
    run_cmd: Callable[..., subprocess.CompletedProcess] | None = None,
) -> InstallResult:
    """Remove the installed schedule (launchd agent or Windows task)."""
    if is_macos():
        return _uninstall_launchd(run_cmd)
    if is_windows():
        return _uninstall_windows_task(settings, run_cmd)
    return InstallResult(ok=False, message="Nothing to uninstall on this platform.")


def _uninstall_launchd(
    run_cmd: Callable[..., subprocess.CompletedProcess] | None,
) -> InstallResult:
    runner = run_cmd or subprocess.run
    agent_path = launchd_agent_path()
    if not agent_path.exists() and not plist_is_loaded(agent_path, runner):
        return InstallResult(ok=True, message="Schedule agent is not installed.")

    _launchd_unload(agent_path, runner)
    agent_path.unlink(missing_ok=True)
    return InstallResult(ok=True, message=f"Removed {agent_path}")


def _uninstall_windows_task(
    settings: Settings | None,
    run_cmd: Callable[..., subprocess.CompletedProcess] | None,
) -> InstallResult:
    runner = run_cmd or subprocess.run
    if not windows_task_is_registered(runner):
        if settings:
            script = windows_run_script_path(settings)
            script.unlink(missing_ok=True)
        return InstallResult(ok=True, message="Windows scheduled task is not installed.")

    proc = runner(
        ["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return InstallResult(ok=False, message=err or "schtasks /Delete failed")

    if settings:
        windows_run_script_path(settings).unlink(missing_ok=True)
    return InstallResult(ok=True, message=f"Removed Windows task '{WINDOWS_TASK_NAME}'")


def _launchd_load(agent_path: Path, runner: Callable[..., subprocess.CompletedProcess]) -> str:
    uid = _gui_uid()
    domain = f"gui/{uid}"
    label_target = f"{domain}/{LAUNCHD_LABEL}"

    # Ventura+ prefers bootstrap; older macOS uses load -w.
    for cmd in (
        ["launchctl", "bootstrap", domain, str(agent_path)],
        ["launchctl", "load", "-w", str(agent_path)],
    ):
        proc = runner(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            return ""
        err = (proc.stderr or proc.stdout or "").strip()
        if "already loaded" in err.lower() or "service already bootstrapped" in err.lower():
            runner(["launchctl", "bootout", label_target], capture_output=True, check=False)
            proc2 = runner(cmd, capture_output=True, text=True, check=False)
            if proc2.returncode == 0:
                return ""
            err = (proc2.stderr or proc2.stdout or "").strip()
    return err or "launchctl load failed"


def _launchd_unload(agent_path: Path, runner: Callable[..., subprocess.CompletedProcess]) -> None:
    uid = _gui_uid()
    label_target = f"gui/{uid}/{LAUNCHD_LABEL}"
    for cmd in (
        ["launchctl", "bootout", label_target],
        ["launchctl", "unload", "-w", str(agent_path)],
    ):
        proc = runner(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            return

