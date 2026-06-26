"""Scoring is informational: no skip-by-threshold, auto-approve above threshold."""

from agent.config import Settings
from agent.db import Database
from agent.models import Status


def _job(jid="1"):
    return {
        "job_id": jid,
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "Remote",
        "url": f"https://www.linkedin.com/jobs/view/{jid}/",
        "description": "Python, APIs, 5+ years",
        "source": "search",
    }


class FakeLLM:
    def __init__(self, score: int):
        self.score = score

    def chat_json(self, *a, **k):
        return {"score": self.score, "reasons": "test"}


def test_low_score_does_not_skip_job(tmp_path, monkeypatch):
    from agent.pipeline import match as match_mod

    resume = tmp_path / "resume.txt"
    resume.write_text(
        "Jane Engineer — 8 years Python, distributed systems, AWS, Kubernetes, "
        "PostgreSQL, and building APIs at scale.\n" * 3,
        encoding="utf-8",
    )
    settings = Settings(master_resume_path=str(resume), match_threshold=70)
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([_job()])

    match_mod.score_jobs(settings, db, FakeLLM(45))

    with db.session() as s:
        job = db.get(s, "1")
        assert job is not None
        assert job.match_score == 45
        assert job.status == Status.new.value
        assert job.approved is False


def test_high_score_auto_approves(tmp_path):
    from agent.pipeline import match as match_mod

    resume = tmp_path / "resume.txt"
    resume.write_text(
        "Jane Engineer — 8 years Python, distributed systems, AWS, Kubernetes, "
        "PostgreSQL, and building APIs at scale.\n" * 3,
        encoding="utf-8",
    )
    settings = Settings(master_resume_path=str(resume), match_threshold=70)
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([_job()])

    stats = match_mod.score_jobs(settings, db, FakeLLM(85))

    with db.session() as s:
        job = db.get(s, "1")
        assert job is not None
        assert job.match_score == 85
        assert job.approved is True
    assert stats["auto_approved"] == 1


def test_empty_resume_aborts_without_scoring_zero(tmp_path):
    from agent.pipeline import match as match_mod

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n% empty\n")  # not a real PDF; yields no text
    settings = Settings(master_resume_path=str(resume), match_threshold=70)
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([_job()])

    result = match_mod.score_jobs(settings, db, FakeLLM(90))

    assert "error" in result
    with db.session() as s:
        job = db.get(s, "1")
        assert job is not None
        assert job.match_score is None
