from agent.db import Database
from agent.models import Status


def _job(job_id, **kw):
    base = {
        "job_id": job_id,
        "title": "Engineer",
        "company": "Acme",
        "location": "Remote",
        "url": f"https://x/{job_id}",
        "description": "desc",
        "source": "search",
    }
    base.update(kw)
    return base


def test_dedup_and_seen(tmp_path):
    db = Database(tmp_path / "s.db")
    new, seen = db.upsert_discovered([_job("1"), _job("2")])
    assert (new, seen) == (2, 0)
    new, seen = db.upsert_discovered([_job("2"), _job("3")])
    assert (new, seen) == (1, 1)
    assert db.known_job_ids() == {"1", "2", "3"}


def test_missing_description_tracking_and_refetch(tmp_path):
    db = Database(tmp_path / "s.db")
    # First discovery: description fetch failed -> empty.
    db.upsert_discovered([_job("1", description="")])
    assert db.job_ids_missing_description() == {"1"}
    # A later run learns the description; it gets persisted.
    db.upsert_discovered([_job("1", description="now we have text")])
    assert db.job_ids_missing_description() == set()


def test_apply_type_upgraded_from_unknown(tmp_path):
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([_job("1", apply_type="unknown")])
    db.upsert_discovered([_job("1", apply_type="easy")])
    with db.session() as s:
        assert db.get(s, "1").apply_type == "easy"
    # A concrete type is not clobbered back to unknown.
    db.upsert_discovered([_job("1", apply_type="unknown")])
    with db.session() as s:
        assert db.get(s, "1").apply_type == "easy"


def test_pending_excludes_terminal(tmp_path):
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([_job("1"), _job("2"), _job("3")])
    db.update("1", status=Status.applied.value, match_score=90)
    db.update("2", status=Status.skipped.value, match_score=10)
    db.update("3", status=Status.new.value, match_score=80)
    pending_ids = {j.job_id for j in db.pending_for_apply()}
    assert pending_ids == {"3"}


def test_applied_at_set_on_apply(tmp_path):
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([_job("1")])
    db.update("1", status=Status.applied.value)
    with db.session() as s:
        job = db.get(s, "1")
        assert job.applied_at is not None


def test_unresolved_human_review_is_held(tmp_path):
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([_job("1")])
    db.update("1", status=Status.human_review.value, review_resolved=False, match_score=95)
    # Left as-is: not eligible for apply until the user resolves it.
    assert {j.job_id for j in db.pending_for_apply()} == set()
    assert {j.job_id for j in db.human_review_jobs()} == {"1"}


def test_resolved_human_review_is_picked_up(tmp_path):
    db = Database(tmp_path / "s.db")
    db.upsert_discovered([_job("1")])
    db.update("1", status=Status.human_review.value, review_resolved=False, match_score=95)
    # User resolves it (e.g. via `review` CLI / sheet edit).
    db.update("1", review_resolved=True)
    assert {j.job_id for j in db.pending_for_apply()} == {"1"}
    # And it no longer shows up as awaiting review.
    assert {j.job_id for j in db.human_review_jobs(unresolved_only=True)} == set()
