"""SQLite engine, session factory, and upsert/query helpers."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from agent.models import ApplyType, Base, Job, RunLog, Status, TERMINAL_STATUSES


class Database:
    def __init__(self, db_file: Path):
        db_file.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_file}", future=True)
        Base.metadata.create_all(self.engine)
        self._migrate()
        self._Session = sessionmaker(bind=self.engine, future=True)

    def _migrate(self) -> None:
        """Add columns introduced after a DB was first created (SQLite)."""
        with self.engine.begin() as conn:
            existing = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
            if "needs_input" not in existing:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN needs_input TEXT DEFAULT ''"))
            if "review_resolved" not in existing:
                conn.execute(
                    text("ALTER TABLE jobs ADD COLUMN review_resolved BOOLEAN DEFAULT 0")
                )
            if "approved" not in existing:
                conn.execute(text("ALTER TABLE jobs ADD COLUMN approved BOOLEAN DEFAULT 0"))
            if "use_master_resume" not in existing:
                conn.execute(
                    text("ALTER TABLE jobs ADD COLUMN use_master_resume BOOLEAN DEFAULT 0")
                )

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ---- queries -----------------------------------------------------------
    def known_job_ids(self) -> set[str]:
        with self.session() as s:
            return {row[0] for row in s.execute(select(Job.job_id)).all()}

    def get(self, s: Session, job_id: str) -> Job | None:
        return s.execute(select(Job).where(Job.job_id == job_id)).scalar_one_or_none()

    def upsert_discovered(self, jobs: Iterable[dict]) -> tuple[int, int]:
        """Insert new jobs; refresh last_seen for existing. Returns (new, seen)."""
        new_count = 0
        seen_count = 0
        now = datetime.now(timezone.utc)
        with self.session() as s:
            for data in jobs:
                existing = self.get(s, data["job_id"])
                if existing:
                    existing.last_seen = now
                    # Refresh description if we just learned more (e.g. a prior
                    # description fetch failed and left it empty).
                    if data.get("description") and not existing.description:
                        existing.description = data["description"]
                    # Refresh apply_type once we detect a concrete one (a prior
                    # run may have left it "unknown" if the detail page failed).
                    new_type = data.get("apply_type")
                    if new_type and new_type != ApplyType.unknown.value and (
                        not existing.apply_type
                        or existing.apply_type == ApplyType.unknown.value
                    ):
                        existing.apply_type = new_type
                    seen_count += 1
                else:
                    s.add(Job(**data))
                    new_count += 1
        return new_count, seen_count

    def job_ids_missing_description(self) -> set[str]:
        """Known jobs whose description never got fetched (empty)."""
        with self.session() as s:
            stmt = select(Job.job_id).where(
                (Job.description.is_(None)) | (Job.description == "")
            )
            return {row[0] for row in s.execute(stmt).all()}

    def pending_for_apply(self, limit: int | None = None) -> list[Job]:
        """Jobs eligible to be acted on.

        Excludes permanently-terminal states (applied/skipped) and any
        human_review job that the user has NOT yet resolved (so unresolved
        reviews are left as-is and skipped on future runs). A human_review job
        becomes eligible again once review_resolved is set.
        """
        terminal = {s.value for s in TERMINAL_STATUSES}
        with self.session() as s:
            stmt = (
                select(Job)
                .where(Job.status.notin_(terminal))
                .where(
                    (Job.status != Status.human_review.value)
                    | (Job.review_resolved.is_(True))
                )
                .order_by(Job.match_score.desc().nullslast())
            )
            if limit:
                stmt = stmt.limit(limit)
            jobs = list(s.execute(stmt).scalars().all())
            for j in jobs:
                s.expunge(j)
            return jobs

    def human_review_jobs(self, unresolved_only: bool = True) -> list[Job]:
        """Jobs parked for human review (for the `review` CLI)."""
        with self.session() as s:
            stmt = select(Job).where(Job.status == Status.human_review.value)
            if unresolved_only:
                stmt = stmt.where(Job.review_resolved.is_(False))
            stmt = stmt.order_by(Job.match_score.desc().nullslast())
            jobs = list(s.execute(stmt).scalars().all())
            for j in jobs:
                s.expunge(j)
            return jobs

    def generated_jobs(self, approved: bool | None = None) -> list[Job]:
        """Jobs that have documents generated (status == generated).

        Pass approved=True/False to filter by sign-off, or None for all. Powers
        the 'review the resumes, then approve' step (and a future UI)."""
        with self.session() as s:
            stmt = select(Job).where(Job.status == Status.generated.value)
            if approved is not None:
                stmt = stmt.where(Job.approved.is_(approved))
            stmt = stmt.order_by(Job.match_score.desc().nullslast())
            jobs = list(s.execute(stmt).scalars().all())
            for j in jobs:
                s.expunge(j)
            return jobs

    def set_approved(self, job_id: str, value: bool = True) -> bool:
        """Approve/unapprove one job. Returns True if the job exists.

        Approving a job parked in ``human_review`` marks it resolved so
        ``apply --only-approved`` can retry it."""
        with self.session() as s:
            job = self.get(s, job_id)
            if job is None:
                return False
            job.approved = value
            if value and job.status == Status.human_review.value:
                job.review_resolved = True
            return True

    def resolve_approved_human_review(self) -> int:
        """Let approved human_review jobs be retried on the next apply run."""
        with self.session() as s:
            jobs = list(
                s.execute(
                    select(Job).where(
                        Job.approved.is_(True),
                        Job.status == Status.human_review.value,
                        Job.review_resolved.is_(False),
                    )
                ).scalars().all()
            )
            for job in jobs:
                job.review_resolved = True
            return len(jobs)

    def resolve_human_review_for(self, job_ids: Iterable[str]) -> int:
        """Mark specific human_review jobs resolved so apply can retry them."""
        wanted = set(job_ids)
        if not wanted:
            return 0
        with self.session() as s:
            jobs = list(
                s.execute(
                    select(Job).where(
                        Job.job_id.in_(wanted),
                        Job.status == Status.human_review.value,
                        Job.review_resolved.is_(False),
                    )
                ).scalars().all()
            )
            for job in jobs:
                job.review_resolved = True
            return len(jobs)

    def set_use_master_resume(self, job_id: str, value: bool = True) -> bool:
        """Choose master vs tailored resume for apply. Returns True if the job exists."""
        with self.session() as s:
            job = self.get(s, job_id)
            if job is None:
                return False
            job.use_master_resume = value
            return True

    def approve_all_generated(self) -> int:
        """Approve every job currently in the generated state. Returns the count."""
        with self.session() as s:
            jobs = list(
                s.execute(
                    select(Job).where(Job.status == Status.generated.value)
                ).scalars().all()
            )
            for j in jobs:
                j.approved = True
            return len(jobs)

    def needs_scoring(self) -> list[Job]:
        with self.session() as s:
            stmt = select(Job).where(Job.match_score.is_(None))
            jobs = list(s.execute(stmt).scalars().all())
            for j in jobs:
                s.expunge(j)
            return jobs

    def all_jobs(self) -> list[Job]:
        with self.session() as s:
            jobs = list(s.execute(select(Job).order_by(Job.created_at.desc())).scalars().all())
            for j in jobs:
                s.expunge(j)
            return jobs

    def update(self, job_id: str, **fields) -> None:
        with self.session() as s:
            job = self.get(s, job_id)
            if job is None:
                return
            for k, v in fields.items():
                setattr(job, k, v)
            if fields.get("status") == Status.applied.value and job.applied_at is None:
                job.applied_at = datetime.now(timezone.utc)

    # ---- run history -------------------------------------------------------
    def log_run(
        self,
        phase: str,
        *,
        discovered: int = 0,
        applied: int = 0,
        review: int = 0,
        skipped: int = 0,
        errors: int = 0,
        notes: str = "",
    ) -> None:
        """Append one row to the run log (observability for cron/UI runs)."""
        with self.session() as s:
            s.add(RunLog(
                phase=phase, discovered=discovered, applied=applied, review=review,
                skipped=skipped, errors=errors, notes=notes,
            ))

    def recent_runs(self, limit: int = 25) -> list[RunLog]:
        with self.session() as s:
            rows = list(
                s.execute(
                    select(RunLog).order_by(RunLog.started_at.desc()).limit(limit)
                ).scalars().all()
            )
            for r in rows:
                s.expunge(r)
            return rows

    def clear_all(self) -> dict[str, int]:
        """Delete every job and run-log row. Returns counts removed."""
        with self.session() as s:
            jobs = s.execute(select(Job)).scalars().all()
            runs = s.execute(select(RunLog)).scalars().all()
            for row in jobs:
                s.delete(row)
            for row in runs:
                s.delete(row)
            return {"jobs_deleted": len(jobs), "runs_deleted": len(runs)}
