"""Review-gated flow: generate-all, edit preservation, approval gating."""

from agent.config import Settings, SubmitMode
from agent.db import Database
from agent.models import Status
from agent.pipeline import apply as apply_mod
from agent.pipeline import generate as gen_mod


def _seed(tmp_path, **kw):
    db = Database(tmp_path / "s.db")
    row = {
        "job_id": "1", "title": "Eng", "company": "Acme", "location": "Remote",
        "url": "https://www.linkedin.com/jobs/view/1/", "description": "d",
        "source": "search", "apply_type": "easy",
    }
    db.upsert_discovered([row])
    db.update("1", match_score=90, **kw)
    return db


# ---- generate_all ----------------------------------------------------------

def test_generate_all_drafts_missing(tmp_path, monkeypatch):
    db = _seed(tmp_path)
    made = {}

    def fake_gen(settings, llm, job):
        f = tmp_path / "r.docx"
        f.write_text("doc")
        made[job.job_id] = True
        return str(f), ""

    monkeypatch.setattr(gen_mod, "generate_documents", fake_gen)
    stats = gen_mod.generate_all(Settings(), db, llm=object())
    assert stats["generated"] == 1
    with db.session() as s:
        job = db.get(s, "1")
        assert job.status == Status.generated.value
        assert job.approved is False


def test_generate_all_skips_existing_to_preserve_edits(tmp_path, monkeypatch):
    existing = tmp_path / "r.docx"
    existing.write_text("user-edited")
    db = _seed(tmp_path, resume_path=str(existing), status=Status.generated.value)

    calls = {"n": 0}

    def fake_gen(settings, llm, job):
        calls["n"] += 1
        return "x", ""

    monkeypatch.setattr(gen_mod, "generate_documents", fake_gen)
    stats = gen_mod.generate_all(Settings(), db, llm=object())
    assert stats["generated"] == 0
    assert stats["skipped_existing"] == 1
    assert stats["failed"] == 0
    assert calls["n"] == 0                      # never re-drafted -> edits preserved
    assert existing.read_text() == "user-edited"


def test_generate_all_regenerate_forces(tmp_path, monkeypatch):
    existing = tmp_path / "r.docx"
    existing.write_text("old")
    db = _seed(tmp_path, resume_path=str(existing), status=Status.generated.value,
               approved=True)

    def fake_gen(settings, llm, job):
        return str(existing), ""

    monkeypatch.setattr(gen_mod, "generate_documents", fake_gen)
    stats = gen_mod.generate_all(Settings(), db, llm=object(), regenerate=True)
    assert stats["generated"] == 1
    with db.session() as s:
        assert db.get(s, "1").approved is False   # regenerate resets approval


# ---- finalize for upload ---------------------------------------------------

def test_finalize_returns_docx_when_format_docx(tmp_path):
    docx = tmp_path / "r.docx"
    docx.write_text("d")
    out = gen_mod.finalize_resume_for_upload(Settings(resume_output_format="docx"), str(docx))
    assert out == docx


def test_finalize_none_when_missing(tmp_path):
    assert gen_mod.finalize_resume_for_upload(Settings(), "") is None
    assert gen_mod.finalize_resume_for_upload(Settings(), str(tmp_path / "nope.docx")) is None


# ---- apply reuses docs / approval gate -------------------------------------

def test_apply_reuses_existing_docs(tmp_path, monkeypatch):
    docx = tmp_path / "r.docx"
    docx.write_text("user-edited")
    db = _seed(tmp_path, resume_path=str(docx), status=Status.generated.value)

    regen = {"n": 0}
    monkeypatch.setattr(apply_mod, "generate_documents",
                        lambda *a, **k: regen.__setitem__("n", regen["n"] + 1) or ("x", "y"))
    monkeypatch.setattr(apply_mod, "easy_apply", lambda *a, **k: ("applied", "ok", []))

    settings = Settings(resume_output_format="docx")
    stats = apply_mod.run_apply(session=None, settings=settings, db=db, llm=object())
    assert regen["n"] == 0                 # did not regenerate -> edits preserved
    assert stats["applied"] == 1
    assert docx.read_text() == "user-edited"


def test_apply_only_approved_gates(tmp_path, monkeypatch):
    docx = tmp_path / "r.docx"
    docx.write_text("d")
    db = _seed(tmp_path, resume_path=str(docx), status=Status.generated.value, approved=False)

    monkeypatch.setattr(apply_mod, "easy_apply", lambda *a, **k: ("applied", "ok", []))
    settings = Settings(submit_mode=SubmitMode.auto, resume_output_format="docx")

    # Not approved -> nothing happens.
    stats = apply_mod.run_apply(session=None, settings=settings, db=db, llm=object(),
                                only_approved=True)
    assert stats["applied"] == 0

    # Approve, then it submits.
    db.set_approved("1", True)
    stats = apply_mod.run_apply(session=None, settings=settings, db=db, llm=object(),
                                only_approved=True)
    assert stats["applied"] == 1
