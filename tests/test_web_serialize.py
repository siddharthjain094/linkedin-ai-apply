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
    db.update("1", match_score=88, approved=True)
    with db.session() as s:
        row = serialize_job(db.get(s, "1"), settings)
    assert row["job_id"] == "1"
    assert row["approved"] is True
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
