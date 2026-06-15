import threading
import time

from agent.web.runner import ActionRunner


def _wait(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_idle_snapshot():
    r = ActionRunner()
    snap = r.snapshot()
    assert snap["running"] is False
    assert snap["stop_requested"] is False
    assert r.request_stop() is False          # nothing to stop


def test_cooperative_stop():
    r = ActionRunner()
    started = threading.Event()

    def fn():
        started.set()
        while not r.should_stop():
            time.sleep(0.005)
        return {"stopped": True}

    assert r.start("loop", fn) is True
    assert started.wait(1)
    assert r.start("loop", fn) is False        # single-flight: can't start a second

    assert r.request_stop() is True
    assert _wait(lambda: not r.running)
    snap = r.snapshot()
    assert snap["result"] == {"stopped": True}
    assert snap["error"] is None


def test_error_is_captured():
    r = ActionRunner()

    def boom():
        raise ValueError("nope")

    r.start("boom", boom)
    assert _wait(lambda: not r.running)
    assert "ValueError" in (r.snapshot()["error"] or "")
