"""Tests for schedule configuration and launchd plist generation."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest
import yaml

import agent.config as config
import agent.schedule as schedule
from agent.config import ScheduleConfig, ScheduleMode, ScheduleWorkflow, Settings, load_settings
from agent.schedule import (
    ScheduleError,
    WINDOWS_TASK_NAME,
    build_cron_line,
    build_launchd_plist,
    build_schedule_entry,
    build_schedules_list,
    build_schtasks_create_args,
    build_windows_run_script,
    delete_active_schedule,
    interval_seconds,
    merge_schedule_update,
    parse_days,
    parse_interval_hours,
    parse_time,
    save_schedule_config,
    schedule_description,
    schedule_entry_status,
    weekday_numbers,
    workflow_argv,
)


def test_parse_time_valid():
    t = parse_time("09:00")
    assert t.hour == 9 and t.minute == 0
    assert t.as_hhmm == "09:00"
    t2 = parse_time("23:59")
    assert t2.hour == 23 and t2.minute == 59


@pytest.mark.parametrize("bad", ["25:00", "9:0", "noon", "", "9:60"])
def test_parse_time_invalid(bad):
    with pytest.raises(ScheduleError):
        parse_time(bad)


def test_parse_days_from_string():
    days = parse_days("mon, wed ,FRI")
    assert days == ["mon", "wed", "fri"]


def test_parse_days_rejects_unknown():
    with pytest.raises(ScheduleError):
        parse_days("monday, foo")


def test_weekday_numbers():
    assert weekday_numbers(["mon", "fri"]) == [1, 5]


def test_merge_schedule_update_partial():
    base = ScheduleConfig()
    updated = merge_schedule_update(base, time="14:30", enabled=True)
    assert updated.time == "14:30"
    assert updated.enabled is True
    assert updated.days == base.days  # unchanged


def test_save_schedule_config_merges_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"match_threshold": 80}), encoding="utf-8")

    sched = ScheduleConfig(enabled=True, time="10:15", days=["mon"], workflow=ScheduleWorkflow.find)
    save_schedule_config(sched, cfg)

    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["match_threshold"] == 80
    assert data["schedule"]["enabled"] is True
    assert data["schedule"]["workflow"] == "find"


def test_load_settings_reads_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({
            "schedule": {
                "enabled": True,
                "time": "08:00",
                "days": ["sat", "sun"],
                "workflow": "apply",
                "only_approved": True,
            }
        }),
        encoding="utf-8",
    )
    s = load_settings(env_file=str(tmp_path / ".env"))
    assert s.schedule.enabled is True
    assert s.schedule.time == "08:00"
    assert s.schedule.days == ["sat", "sun"]
    assert s.schedule.workflow == ScheduleWorkflow.apply
    assert s.schedule.only_approved is True


def test_parse_interval_hours_valid():
    assert parse_interval_hours(4) == 4
    assert parse_interval_hours("12") == 12


@pytest.mark.parametrize("bad", [0, 3, 5, 48, "x"])
def test_parse_interval_hours_invalid(bad):
    with pytest.raises(ScheduleError):
        parse_interval_hours(bad)


def test_schedule_description_interval():
    s = ScheduleConfig(mode=ScheduleMode.interval, interval_hours=2)
    assert schedule_description(s) == "Every 2 hours"


def test_schedule_description_daily():
    s = ScheduleConfig(mode=ScheduleMode.daily, time="09:30", days=["mon", "fri"])
    assert "09:30" in schedule_description(s)
    assert "mon" in schedule_description(s)


def test_build_launchd_plist_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    settings = Settings(
        schedule=ScheduleConfig(
            enabled=True,
            mode=ScheduleMode.interval,
            interval_hours=4,
        ),
    )
    plist = build_launchd_plist(settings, settings.schedule)
    assert plist["StartInterval"] == interval_seconds(4)
    assert plist["RunAtLoad"] is True
    assert "StartCalendarInterval" not in plist


def test_build_launchd_plist_daily_no_run_at_load(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    settings = Settings(
        schedule=ScheduleConfig(
            enabled=True,
            mode=ScheduleMode.daily,
            time="09:00",
            days=["mon"],
        ),
    )
    plist = build_launchd_plist(settings, settings.schedule)
    assert "RunAtLoad" not in plist
    assert "StartCalendarInterval" in plist


def test_build_cron_line_interval(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    settings = Settings(
        schedule=ScheduleConfig(enabled=True, mode=ScheduleMode.interval, interval_hours=6),
    )
    line = build_cron_line(settings, settings.schedule)
    assert line.startswith("0 */6 * * *")


def test_merge_schedule_update_interval():
    base = ScheduleConfig()
    updated = merge_schedule_update(base, mode="interval", interval_hours=1)
    assert updated.mode == ScheduleMode.interval
    assert updated.interval_hours == 1


def test_load_settings_reads_interval_schedule(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({
            "schedule": {
                "enabled": True,
                "mode": "interval",
                "interval_hours": 2,
                "workflow": "find",
            }
        }),
        encoding="utf-8",
    )
    s = load_settings(env_file=str(tmp_path / ".env"))
    assert s.schedule.mode == ScheduleMode.interval
    assert s.schedule.interval_hours == 2


def test_build_schedules_list_empty_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    settings = Settings(schedule=ScheduleConfig(enabled=False))
    assert build_schedules_list(settings) == []


def test_build_schedules_list_includes_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    settings = Settings(
        schedule=ScheduleConfig(enabled=True, mode=ScheduleMode.interval, interval_hours=2),
    )
    entries = build_schedules_list(settings)
    assert len(entries) == 1
    assert entries[0]["status"] == "saved"
    assert entries[0]["can_delete"] is False


def test_schedule_entry_status_active():
    s = ScheduleConfig(enabled=True)
    assert schedule_entry_status(s, installed=True, loaded=True) == "active"


def test_delete_active_schedule_disables_config(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(schedule, "is_macos", lambda: True)
    monkeypatch.setattr(schedule, "supports_auto_install", lambda: True)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump({"schedule": {"enabled": True, "mode": "interval", "interval_hours": 4}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(schedule, "config_yaml_path", lambda: cfg)
    monkeypatch.setattr(schedule, "uninstall_schedule", lambda *a, **k: schedule.InstallResult(ok=True, message="removed"))

    settings = load_settings(env_file=str(tmp_path / ".env"))
    result = delete_active_schedule(settings)
    assert result.ok
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["schedule"]["enabled"] is False


def test_workflow_argv_schedule_run_includes_no_generate_by_default():
    sched = ScheduleConfig(workflow=ScheduleWorkflow.schedule_run, skip_generate=True)
    assert workflow_argv(sched) == ["schedule-run", "--no-generate"]


def test_workflow_argv_schedule_run_can_generate_when_disabled():
    sched = ScheduleConfig(workflow=ScheduleWorkflow.schedule_run, skip_generate=False)
    assert workflow_argv(sched) == ["schedule-run"]


def test_legacy_workflow_daily_coerced_to_schedule_run():
    sched = ScheduleConfig(workflow="daily")
    assert sched.workflow == ScheduleWorkflow.schedule_run


def test_build_launchd_plist_structure(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    settings = Settings(
        db_path="data/state.db",
        schedule=ScheduleConfig(
            enabled=True,
            mode=ScheduleMode.daily,
            time="09:30",
            days=["mon", "wed"],
            workflow=ScheduleWorkflow.schedule_run,
        ),
    )
    plist = build_launchd_plist(settings, settings.schedule)
    assert plist["Label"] == schedule.LAUNCHD_LABEL
    assert plist["WorkingDirectory"] == str(tmp_path)
    assert "schedule-run" in plist["ProgramArguments"]
    intervals = plist["StartCalendarInterval"]
    assert isinstance(intervals, list)
    assert len(intervals) == 2
    assert intervals[0]["Hour"] == 9 and intervals[0]["Minute"] == 30


def test_build_launchd_plist_single_day_uses_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    settings = Settings(
        schedule=ScheduleConfig(enabled=True, mode=ScheduleMode.daily, time="07:00", days=["tue"]),
    )
    plist = build_launchd_plist(settings, settings.schedule)
    interval = plist["StartCalendarInterval"]
    assert isinstance(interval, dict)
    assert interval["Weekday"] == 2


def test_build_launchd_plist_disabled_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    settings = Settings(schedule=ScheduleConfig(enabled=False))
    with pytest.raises(ScheduleError):
        build_launchd_plist(settings, settings.schedule)


def test_build_cron_line(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    settings = Settings(
        db_path="data/state.db",
        schedule=ScheduleConfig(
            enabled=True, mode=ScheduleMode.daily, time="09:00", days=["mon", "fri"]),
    )
    line = build_cron_line(settings, settings.schedule)
    assert line.startswith("0 9 * * 1,5")
    assert str(tmp_path) in line
    assert "schedule-run" in line


def test_install_schedule_writes_plist(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(schedule, "is_macos", lambda: True)
    agent_dir = tmp_path / "LaunchAgents"
    agent_path = agent_dir / schedule.LAUNCHD_FILENAME
    monkeypatch.setattr(schedule, "launchd_agent_path", lambda: agent_path)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(schedule, "plist_is_loaded", lambda *a, **k: True)

    settings = Settings(
        db_path="data/state.db",
        schedule=ScheduleConfig(enabled=True, time="09:00"),
    )
    result = schedule.install_schedule(settings, run_cmd=fake_run)
    assert result.ok is True
    assert agent_path.exists()
    data = plistlib.loads(agent_path.read_bytes())
    assert data["Label"] == schedule.LAUNCHD_LABEL
    assert any("bootstrap" in " ".join(c) or "load" in c for c in calls)


def test_install_schedule_non_macos_returns_manual(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(schedule, "is_macos", lambda: False)
    monkeypatch.setattr(schedule, "is_windows", lambda: False)
    monkeypatch.setattr(schedule, "supports_auto_install", lambda: False)
    settings = Settings(
        schedule=ScheduleConfig(enabled=True, time="09:00"),
    )
    result = schedule.install_schedule(settings)
    assert result.ok is False
    assert "cron" in result.message.lower()


def test_build_schtasks_create_args_interval():
    sched = ScheduleConfig(enabled=True, mode=ScheduleMode.interval, interval_hours=4)
    args = build_schtasks_create_args(sched, Path(r"C:\proj\data\run_scheduled.cmd"))
    assert "schtasks" in args
    assert WINDOWS_TASK_NAME in args
    assert "/SC" in args and "HOURLY" in args
    assert "4" in args


def test_build_schtasks_create_args_daily():
    sched = ScheduleConfig(
        enabled=True,
        mode=ScheduleMode.daily,
        time="09:30",
        days=["mon", "wed"],
    )
    args = build_schtasks_create_args(sched, Path(r"C:\proj\run.cmd"))
    assert "WEEKLY" in args
    assert "MON,WED" in args
    assert "09:30" in args


def test_build_windows_run_script(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    settings = Settings(
        db_path="data/state.db",
        schedule=ScheduleConfig(enabled=True, mode=ScheduleMode.interval, interval_hours=2),
    )
    text = build_windows_run_script(settings, settings.schedule)
    assert "@echo off" in text
    assert "schedule-run" in text
    assert "daily.log" in text
    assert str(tmp_path.resolve()) in text or str(tmp_path) in text


def test_install_schedule_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(schedule, "is_macos", lambda: False)
    monkeypatch.setattr(schedule, "is_windows", lambda: True)
    monkeypatch.setattr(schedule, "supports_auto_install", lambda: True)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(schedule, "windows_task_is_registered", lambda *a, **k: True)

    settings = Settings(
        db_path="data/state.db",
        schedule=ScheduleConfig(enabled=True, mode=ScheduleMode.interval, interval_hours=1),
    )
    result = schedule.install_schedule(settings, run_cmd=fake_run)
    assert result.ok is True
    script = settings.db_file.parent / "run_scheduled.cmd"
    assert script.exists()
    assert any("schtasks" in c and "/Create" in c for c in calls)
    assert any("schtasks" in c and "/Run" in c for c in calls)


def test_uninstall_schedule_removes_plist(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "is_macos", lambda: True)
    agent_path = tmp_path / schedule.LAUNCHD_FILENAME
    agent_path.write_bytes(b"plist")
    monkeypatch.setattr(schedule, "launchd_agent_path", lambda: agent_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    result = schedule.uninstall_schedule(None, run_cmd=fake_run)
    assert result.ok is True
    assert not agent_path.exists()
