"""Resume upload helpers and intake refresh."""

import yaml

import agent.config as config
from agent.config import load_settings
from agent.db import Database
from agent.intake_refresh import merge_intake_from_resume
from agent.web.server import save_job_resume, save_master_resume


def _settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    s = load_settings(env_file=str(tmp_path / ".env"))
    s.ensure_dirs()
    return s


def test_save_master_resume_replaces_and_picks_pdf(tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch)
    dest = save_master_resume(settings, "my_resume.pdf", b"%PDF-1.4")
    assert dest.name == "master_resume.pdf"
    assert dest.exists()
    assert settings.resolve_master_resume() == dest


def test_save_job_resume_writes_to_output(tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch)
    db = Database(settings.db_file)
    db.upsert_discovered([{
        "job_id": "1", "title": "Eng", "company": "Acme", "location": "Remote",
        "url": "https://x/1", "description": "d", "source": "search",
    }])
    with db.session() as s:
        job = db.get(s, "1")
        dest = save_job_resume(settings, job, "fixed.docx", b"PK fake docx")
    assert dest.parent.name == "resumes"
    assert dest.exists()


def test_merge_intake_from_resume_updates_yaml(tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch)
    resume = settings.master_resume_file.parent / "master_resume.txt"
    resume.parent.mkdir(parents=True, exist_ok=True)
    resume.write_text(
        "Jane Doe\njane@example.com\n555-123-4567\n"
        "https://linkedin.com/in/janedoe\n6 years experience\n",
        encoding="utf-8",
    )
    intake = settings.intake_file
    intake.parent.mkdir(parents=True, exist_ok=True)
    with intake.open("w", encoding="utf-8") as fh:
        yaml.safe_dump({"personal": {"full_name": "Old Name"}, "screening_answers": {"x": "y"}}, fh)

    # Point config at .txt master
    monkeypatch.setenv("MASTER_RESUME_PATH", str(resume))
    settings = load_settings(env_file=str(tmp_path / ".env"))

    result = merge_intake_from_resume(settings)
    assert result["ok"] is True
    with intake.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    assert data["personal"]["email"] == "jane@example.com"
    assert data["screening_answers"]["x"] == "y"  # preserved
