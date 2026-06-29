"""Stop button API and cooperative cancellation."""

import threading
import time

from fastapi.testclient import TestClient

from agent.web.server import create_app, runner


def _wait(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_stop_endpoint_cancels_running_action():
    started = threading.Event()

    def slow():
        started.set()
        while not runner.should_stop():
            time.sleep(0.005)
        return {"stopped": True}

    assert runner.start("test-stop", slow) is True
    assert started.wait(1)

    client = TestClient(create_app())
    res = client.post("/api/actions/stop")
    assert res.status_code == 200
    body = res.json()
    assert body["stop_requested"] is True
    assert body["running"] is True

    assert _wait(lambda: not runner.running)
    snap = runner.snapshot()
    assert snap["result"] == {"stopped": True}
    runner.ack()


def test_stop_endpoint_409_when_idle():
    runner.ack()
    client = TestClient(create_app())
    res = client.post("/api/actions/stop")
    assert res.status_code == 409
