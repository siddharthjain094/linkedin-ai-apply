"""Logout detection, loud LLM-failure abort, and run-history logging."""

import pytest

from agent.browser.session import LoggedOutError
from agent.config import Settings
from agent.db import Database
from agent.llm.provider import LLMUnavailableError
from agent.models import Status
from agent.pipeline import apply as apply_mod
from agent.pipeline.match import score_jobs


def _job(jid, **kw):
    base = {
        "job_id": jid, "title": "Engineer", "company": "Acme", "location": "Remote",
        "url": f"https://www.linkedin.com/jobs/view/{jid}/", "description": "desc",
        "source": "search",
    }
    base.update(kw)
    return base


class FakeSession:
    def __init__(self, out):
        self._out = out

    def logged_out(self):
        return self._out


# ---- logout detection ------------------------------------------------------

def test_run_apply_aborts_on_logout(tmp_path):
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([_job("1", apply_type="easy")])
    db.update("1", match_score=90, status=Status.generated.value)

    with pytest.raises(LoggedOutError):
        apply_mod.run_apply(
            session=FakeSession(True), settings=Settings(resume_output_format="docx"),
            db=db, llm=object())

    # The job must be left untouched (not marked error) and a run must be logged.
    with db.session() as s:
        assert db.get(s, "1").status == Status.generated.value
    runs = db.recent_runs()
    assert any(r.phase == "apply" for r in runs)


# ---- loud LLM failure ------------------------------------------------------

class BoomLLM:
    def chat_json(self, *a, **k):
        raise RuntimeError("401 unauthorized")


def test_score_jobs_aborts_after_repeated_llm_failures(tmp_path):
    resume = tmp_path / "resume.txt"
    resume.write_text(
        "Jane Engineer - 6 years building backends with Python, Go, Postgres, "
        "and AWS at high-traffic companies.\n" * 2,
        encoding="utf-8",
    )
    settings = Settings(master_resume_path=str(resume), match_threshold=70)
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([_job("1"), _job("2"), _job("3"), _job("4")])

    with pytest.raises(LLMUnavailableError):
        score_jobs(settings, db, BoomLLM())

    # It must abort early, not grind through all four jobs.
    runs = db.recent_runs()
    assert any(r.phase == "score" for r in runs)


# ---- run history -----------------------------------------------------------

def test_log_run_and_recent_runs(tmp_path):
    db = Database(tmp_path / "s.db")
    db.log_run("discover", discovered=5, notes="first")
    db.log_run("apply", applied=2, errors=1, notes="second")
    runs = db.recent_runs()
    assert len(runs) == 2
    assert runs[0].phase == "apply"        # newest first
    assert runs[0].applied == 2
    assert runs[1].discovered == 5
    # serialization for the API
    row = runs[0].as_row()
    assert row["phase"] == "apply" and row["errors"] == 1
