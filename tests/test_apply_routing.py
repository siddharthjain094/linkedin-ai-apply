"""Routing logic in run_apply: which applier handles which job, and fallbacks."""

from agent.config import Settings, SubmitMode
from agent.db import Database
from agent.models import Status
from agent.pipeline import apply as apply_mod


def _seed(tmp_path, apply_type):
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([{
        "job_id": "1", "title": "Eng", "company": "Acme", "location": "Remote",
        "url": "https://www.linkedin.com/jobs/view/1/", "description": "d",
        "source": "search", "apply_type": apply_type,
    }])
    db.update("1", match_score=90)
    return db


def _patch(monkeypatch, easy_ret=None, ext_ret=None):
    calls = {"easy": 0, "external": 0}

    def fake_generate(settings, llm, job):
        return "", ""

    def fake_easy(*a, **k):
        calls["easy"] += 1
        return easy_ret

    def fake_external(*a, **k):
        calls["external"] += 1
        return ext_ret

    monkeypatch.setattr(apply_mod, "generate_documents", fake_generate)
    monkeypatch.setattr(apply_mod, "easy_apply", fake_easy)
    monkeypatch.setattr(apply_mod, "external_apply", fake_external)
    return calls


def test_easy_apply_job_uses_easy(tmp_path, monkeypatch):
    db = _seed(tmp_path, "easy")
    calls = _patch(monkeypatch, easy_ret=("applied", "ok", []))
    stats = apply_mod.run_apply(session=None, settings=Settings(), db=db, llm=object())
    assert (calls["easy"], calls["external"]) == (1, 0)
    assert stats["applied"] == 1


def test_unknown_tries_easy_then_falls_back_to_external(tmp_path, monkeypatch):
    db = _seed(tmp_path, "unknown")
    calls = _patch(monkeypatch,
                   easy_ret=("not_easy", "no easy button", []),
                   ext_ret=("applied", "ok", []))
    apply_mod.run_apply(session=None, settings=Settings(), db=db, llm=object())
    assert (calls["easy"], calls["external"]) == (1, 1)


def test_post_goes_straight_to_external(tmp_path, monkeypatch):
    db = _seed(tmp_path, "post")
    calls = _patch(monkeypatch, ext_ret=("applied", "ok", []))
    apply_mod.run_apply(session=None, settings=Settings(), db=db, llm=object())
    assert (calls["easy"], calls["external"]) == (0, 1)


def test_easy_only_parks_external(tmp_path, monkeypatch):
    db = _seed(tmp_path, "unknown")
    calls = _patch(monkeypatch, easy_ret=("not_easy", "no easy button", []))
    settings = Settings(submit_mode=SubmitMode.easy_only)
    stats = apply_mod.run_apply(session=None, settings=settings, db=db, llm=object())
    assert calls["external"] == 0          # never attempted in easy_only
    assert stats["human_review"] == 1
    with db.session() as s:
        assert db.get(s, "1").status == Status.human_review.value


def test_closed_job_is_marked_terminal(tmp_path, monkeypatch):
    db = _seed(tmp_path, "easy")
    calls = _patch(monkeypatch, easy_ret=("closed", "No longer accepting applications.", []))
    stats = apply_mod.run_apply(session=None, settings=Settings(), db=db, llm=object())
    assert (calls["easy"], calls["external"]) == (1, 0)
    assert stats["closed"] == 1
    assert stats["applied"] == 0 and stats["errors"] == 0
    with db.session() as s:
        assert db.get(s, "1").status == Status.closed.value
    # Terminal: a closed job is never offered up for another apply attempt.
    assert all(j.job_id != "1" for j in db.pending_for_apply())


def test_external_linkedin_tries_easy_first(tmp_path, monkeypatch):
    db = _seed(tmp_path, "external")
    calls = _patch(monkeypatch,
                   easy_ret=("applied", "ok", []),
                   ext_ret=("applied", "ext ok", []))
    stats = apply_mod.run_apply(session=None, settings=Settings(), db=db, llm=object())
    assert (calls["easy"], calls["external"]) == (1, 0)
    assert stats["applied"] == 1


def test_external_linkedin_falls_back_when_not_easy(tmp_path, monkeypatch):
    db = _seed(tmp_path, "external")
    calls = _patch(monkeypatch,
                   easy_ret=("not_easy", "no easy button", []),
                   ext_ret=("applied", "ok", []))
    apply_mod.run_apply(session=None, settings=Settings(), db=db, llm=object())
    assert (calls["easy"], calls["external"]) == (1, 1)


def test_closed_from_external_route(tmp_path, monkeypatch):
    db = _seed(tmp_path, "external")
    calls = _patch(monkeypatch,
                   easy_ret=("not_easy", "no easy button", []),
                   ext_ret=("closed", "No longer accepting applications.", []))
    stats = apply_mod.run_apply(session=None, settings=Settings(), db=db, llm=object())
    assert calls["easy"] == 1
    assert calls["external"] == 1
    assert stats["closed"] == 1
    with db.session() as s:
        assert db.get(s, "1").status == Status.closed.value


def test_apply_uses_master_resume_when_flagged(tmp_path, monkeypatch):
    master = tmp_path / "profile" / "master_resume.pdf"
    master.parent.mkdir(parents=True)
    master.write_bytes(b"%PDF-1.4 test")
    db = _seed(tmp_path, "easy")
    db.update("1", use_master_resume=True)
    uploaded = {}

    def fake_easy(session, settings, job, intake, llm, resume_path):
        uploaded["path"] = resume_path
        return "applied", "ok", []

    monkeypatch.setattr(apply_mod, "generate_documents", lambda *a, **k: ("", ""))
    monkeypatch.setattr(apply_mod, "easy_apply", fake_easy)
    monkeypatch.setattr(apply_mod, "external_apply", lambda *a, **k: ("applied", "ok", []))

    settings = Settings(master_resume_path=str(master))
    stats = apply_mod.run_apply(session=None, settings=settings, db=db, llm=object())
    assert stats["applied"] == 1
    assert uploaded["path"] == master


def test_apply_selected_job_ids_only(tmp_path, monkeypatch):
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([
        {"job_id": "1", "title": "A", "company": "X", "location": "R",
         "url": "https://www.linkedin.com/jobs/view/1/", "description": "d",
         "source": "search", "apply_type": "easy"},
        {"job_id": "2", "title": "B", "company": "Y", "location": "R",
         "url": "https://www.linkedin.com/jobs/view/2/", "description": "d",
         "source": "search", "apply_type": "easy"},
    ])
    db.update("1", match_score=90)
    db.update("2", match_score=90)
    seen = []

    def fake_easy(session, settings, job, intake, llm, resume_path):
        seen.append(job["job_id"])
        return "applied", "ok", []

    monkeypatch.setattr(apply_mod, "generate_documents", lambda *a, **k: ("", ""))
    monkeypatch.setattr(apply_mod, "easy_apply", fake_easy)
    monkeypatch.setattr(apply_mod, "external_apply", lambda *a, **k: ("applied", "ok", []))

    stats = apply_mod.run_apply(
        session=None, settings=Settings(), db=db, llm=object(), job_ids=["2"])
    assert seen == ["2"]
    assert stats["applied"] == 1
    assert stats["targeted"] == 1


def test_review_mode_drafts_only(tmp_path, monkeypatch):
    db = _seed(tmp_path, "easy")
    calls = _patch(monkeypatch)
    settings = Settings(submit_mode=SubmitMode.review)
    stats = apply_mod.run_apply(session=None, settings=settings, db=db, llm=object())
    assert (calls["easy"], calls["external"]) == (0, 0)
    assert stats["human_review"] == 1


def test_skip_generate_never_calls_generate_documents(tmp_path, monkeypatch):
    db = _seed(tmp_path, "easy")
    calls = _patch(monkeypatch, easy_ret=("applied", "ok", []))
    gen_called = {"n": 0}

    def counting_generate(*a, **k):
        gen_called["n"] += 1
        return "", ""

    monkeypatch.setattr(apply_mod, "generate_documents", counting_generate)
    stats = apply_mod.run_apply(
        session=None, settings=Settings(), db=db, llm=object(), skip_generate=True)
    assert gen_called["n"] == 0
    assert stats["generated"] == 0
    assert stats["applied"] == 1
