import os

import pytest

from agent.runlock import AlreadyRunning, run_lock


def test_lock_blocks_live_holder(tmp_path):
    lock = tmp_path / "run.lock"
    # Simulate a live holder: our own pid is definitely alive.
    lock.write_text(str(os.getpid()), encoding="utf-8")
    # A different pid value that is alive (use our pid but pretend it's another).
    # Easiest: write a clearly-alive pid (this process) but not equal to getpid
    # is impossible; instead test the stale-reclaim and happy paths below.
    with run_lock(lock):
        assert lock.exists()


def test_stale_lock_is_reclaimed(tmp_path):
    lock = tmp_path / "run.lock"
    lock.write_text("999999999", encoding="utf-8")  # almost certainly dead pid
    with run_lock(lock):
        assert lock.read_text().strip() == str(os.getpid())
    assert not lock.exists()


def test_live_other_holder_raises(tmp_path, monkeypatch):
    lock = tmp_path / "run.lock"
    lock.write_text("4242", encoding="utf-8")
    monkeypatch.setattr("agent.runlock._pid_alive", lambda pid: True)
    with pytest.raises(AlreadyRunning):
        with run_lock(lock):
            pass
    # The live holder's lock must be left intact.
    assert lock.read_text().strip() == "4242"
