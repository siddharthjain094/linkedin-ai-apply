"""ORM models and status/enum definitions."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ApplyType(str, enum.Enum):
    easy = "easy"
    external = "external"
    post = "post"
    unknown = "unknown"


class Status(str, enum.Enum):
    new = "new"              # discovered, not yet processed
    generated = "generated"  # docs generated, not yet submitted
    applied = "applied"      # successfully submitted
    human_review = "human_review"  # AI blocked / external needs you
    skipped = "skipped"      # below threshold / blacklisted
    closed = "closed"        # no longer accepting applications (can't apply)
    error = "error"          # unexpected failure


# States that are permanently done and never re-attempted.
TERMINAL_STATUSES = {Status.applied, Status.skipped, Status.closed}


class Source(str, enum.Enum):
    search = "search"
    hiring_post = "hiring_post"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("job_id", name="uq_jobs_job_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    company: Mapped[str] = mapped_column(String(512), default="")
    location: Mapped[str] = mapped_column(String(512), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default=Source.search.value)

    match_score: Mapped[float | None] = mapped_column(Float, default=None)
    match_reasons: Mapped[str] = mapped_column(Text, default="")

    apply_type: Mapped[str] = mapped_column(String(32), default=ApplyType.unknown.value)
    status: Mapped[str] = mapped_column(String(32), default=Status.new.value, index=True)

    resume_path: Mapped[str] = mapped_column(Text, default="")
    cover_letter_path: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    # When a job is parked in human_review, this holds the structured questions
    # that blocked us (JSON list of {question, options, type}). The user answers
    # them (via `review` CLI or learned_answers.yaml) and sets review_resolved,
    # which makes the job eligible for a retry on the next run.
    needs_input: Mapped[str] = mapped_column(Text, default="")
    review_resolved: Mapped[bool] = mapped_column(Boolean, default=False)

    # User sign-off for the review-gated flow: generate documents for everything,
    # let the user review/edit them, then they approve the subset they actually
    # want submitted. `apply --only-approved` acts only on approved jobs.
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    # When True, apply uploads the user's master resume instead of the tailored draft.
    use_master_resume: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    def as_row(self) -> dict:
        return {
            "job_id": self.job_id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "url": self.url,
            "source": self.source,
            "match_score": self.match_score,
            "match_reasons": self.match_reasons,
            "apply_type": self.apply_type,
            "status": self.status,
            "approved": "yes" if self.approved else "",
            "use_master_resume": "yes" if self.use_master_resume else "",
            "needs_input": self.needs_input,
            "review_resolved": "yes" if self.review_resolved else "",
            "resume_path": self.resume_path,
            "cover_letter_path": self.cover_letter_path,
            "applied_at": self.applied_at.isoformat() if self.applied_at else "",
            "last_seen": self.last_seen.isoformat() if self.last_seen else "",
            "notes": self.notes,
        }


class RunLog(Base):
    __tablename__ = "run_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    phase: Mapped[str] = mapped_column(String(32), default="")
    discovered: Mapped[int] = mapped_column(Integer, default=0)
    applied: Mapped[int] = mapped_column(Integer, default=0)
    review: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")

    def as_row(self) -> dict:
        return {
            "id": self.id,
            "started_at": self.started_at.isoformat() if self.started_at else "",
            "phase": self.phase,
            "discovered": self.discovered,
            "applied": self.applied,
            "review": self.review,
            "skipped": self.skipped,
            "errors": self.errors,
            "notes": self.notes,
        }
