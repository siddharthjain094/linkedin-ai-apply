"""Pure serialization/safety helpers behind the web UI."""

import agent.config as config
from agent.config import load_settings
from agent.db import Database
from agent.web.server import _under_output, serialize_job


def _settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    s = load_settings(env_file=str(tmp_path / ".env"))
    s.ensure_dirs()
    return s


def test_serialize_job_shape(tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch)
    db = Database(settings.db_file)
    db.upsert_discovered([{
        "job_id": "1", "title": "Eng", "company": "Acme", "location": "Remote",
        "url": "https://x/1", "description": "d", "source": "search",
    }])
    db.update("1", match_score=88, approved=True, use_master_resume=True)
    with db.session() as s:
        row = serialize_job(db.get(s, "1"), settings)
    assert row["job_id"] == "1"
    assert row["approved"] is True
    assert row["use_master_resume"] is True
    assert row["match_score"] == 88
    assert row["resume_exists"] is False         # no doc generated yet
    assert "id" in row and "resume_filename" in row


def test_under_output_rejects_paths_outside_output(tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch)
    # A real file outside the output dir must never be servable.
    outside = tmp_path / "secret.txt"
    outside.write_text("nope")
    assert _under_output(settings, str(outside)) is False
    assert _under_output(settings, "") is False

    inside = settings.output_path / "resumes" / "ok.docx"
    inside.write_text("ok")
    assert _under_output(settings, str(inside)) is True


def test_reset_api_clears_database(tmp_path, monkeypatch):
    import agent.config as config
    from fastapi.testclient import TestClient

    from agent.web.server import create_app

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    settings = load_settings(env_file=str(tmp_path / ".env"))
    settings.ensure_dirs()
    db = Database(settings.db_file)
    db.upsert_discovered([{
        "job_id": "1", "title": "Eng", "company": "Acme", "location": "Remote",
        "url": "https://x/1", "description": "d", "source": "search",
    }])
    db.log_run("discover", discovered=1)

    client = TestClient(create_app())
    res = client.post("/api/reset")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["jobs_deleted"] == 1
    assert body["runs_deleted"] == 1
    assert db.all_jobs() == []


def test_score_action_endpoint(tmp_path, monkeypatch):
    import agent.config as config
    from fastapi.testclient import TestClient

    from agent.web.server import create_app

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    settings = load_settings(env_file=str(tmp_path / ".env"))
    settings.ensure_dirs()
    resume = settings.master_resume_file.parent / "master_resume.txt"
    resume.parent.mkdir(parents=True, exist_ok=True)
    resume.write_text(
        "Jane Engineer — 8 years Python, distributed systems, AWS, Kubernetes, "
        "PostgreSQL, and building APIs at scale.\n" * 3,
        encoding="utf-8",
    )
    db = Database(settings.db_file)
    db.upsert_discovered([{
        "job_id": "1", "title": "Eng", "company": "Acme", "location": "Remote",
        "url": "https://x/1", "description": "Python role", "source": "search",
    }])
    db.update("1", match_score=50, match_reasons="old")

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MODEL", "test-model")

    class StubLLM:
        def chat_json(self, *a, **k):
            return {"score": 88, "reasons": "fresh"}

    import agent.llm.provider as llm_provider

    monkeypatch.setattr(llm_provider, "LLMClient", lambda _s: StubLLM())
    monkeypatch.setattr(llm_provider, "llm_is_configured", lambda _s: True)

    client = TestClient(create_app())
    res = client.post("/api/actions/score", json={"job_ids": ["1"], "rescore": True})
    assert res.status_code == 202

    # Wait for background thread
    import time
    for _ in range(50):
        snap = client.get("/api/actions/status").json()
        if not snap["running"]:
            break
        time.sleep(0.05)

    snap = client.get("/api/actions/status").json()
    assert snap["error"] is None
    assert snap["result"]["scored"] == 1

    with db.session() as s:
        job = db.get(s, "1")
        assert job is not None
        assert job.match_score == 88
        assert job.match_reasons == "fresh"
