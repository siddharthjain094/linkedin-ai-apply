"""FastAPI app: a thin, local-only UI over the existing pipeline.

Reads (job list, profile) are plain DB queries. Mutations (approve/reject) are
fast DB writes. The heavy actions (find / generate / apply) are delegated to a
single background worker thread via :class:`ActionRunner`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.config import Settings, load_settings
from agent.db import Database
from agent.intake_refresh import merge_intake_from_resume
from agent.models import Job, Status
from agent.web.runner import ActionRunner

STATIC_DIR = Path(__file__).parent / "static"

runner = ActionRunner()


def _settings() -> Settings:
    s = load_settings()
    s.ensure_dirs()
    return s


def _db(settings: Settings) -> Database:
    return Database(settings.db_file)


# ---- serialization (pure, unit-testable) -----------------------------------

def serialize_job(job: Job, settings: Settings) -> dict:
    row = job.as_row()
    row["id"] = job.id
    row["approved"] = bool(job.approved)
    row["use_master_resume"] = bool(job.use_master_resume)
    row["resume_exists"] = _under_output(settings, job.resume_path)
    row["cover_exists"] = _under_output(settings, job.cover_letter_path)
    row["resume_filename"] = Path(job.resume_path).name if job.resume_path else ""
    return row


ALLOWED_MASTER_SUFFIXES = {".docx", ".pdf", ".txt"}
ALLOWED_JOB_RESUME_SUFFIXES = {".docx", ".pdf"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def save_master_resume(settings: Settings, filename: str, content: bytes) -> Path:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_MASTER_SUFFIXES:
        raise ValueError(f"unsupported type {suffix or '(none)'}; use .docx, .pdf, or .txt")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("file too large (max 10 MB)")
    profile_dir = settings.master_resume_file.parent
    profile_dir.mkdir(parents=True, exist_ok=True)
    for old in profile_dir.glob("master_resume.*"):
        old.unlink(missing_ok=True)
    dest = profile_dir / f"master_resume{suffix}"
    dest.write_bytes(content)
    return dest


def save_job_resume(settings: Settings, job: Job, filename: str, content: bytes) -> Path:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_JOB_RESUME_SUFFIXES:
        raise ValueError(f"unsupported type {suffix or '(none)'}; use .docx or .pdf")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("file too large (max 10 MB)")

    if job.resume_path and _under_output(settings, job.resume_path):
        dest = Path(job.resume_path).with_suffix(suffix)
    else:
        from agent.resume.builder import job_basename
        dest = settings.output_path / "resumes" / f"{job_basename(job.company, job.title)}{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return dest


def master_resume_info(settings: Settings) -> dict:
    master = settings.resolve_master_resume()
    exists = master.exists()
    return {
        "available": exists,
        "name": master.name if exists else "",
        "url": "/api/master-resume" if exists else "",
    }


def _under_output(settings: Settings, path: str) -> bool:
    """True if ``path`` exists and lives under the output dir (safe to serve)."""
    if not path:
        return False
    try:
        p = Path(path).resolve()
        p.relative_to(settings.output_path.resolve())
        return p.exists()
    except (ValueError, OSError):
        return False


# ---- action implementations (run on the worker thread) ---------------------

def _run_find() -> dict:
    from agent.browser.session import open_session
    from agent.llm.provider import LLMClient
    from agent.pipeline import discover, match
    from agent import sheet
    from agent.runlock import run_lock

    settings = _settings()
    db = _db(settings)
    with run_lock(settings.db_file.parent / "run.lock"):
        with open_session(settings) as session:
            session._progress = runner.set_progress  # noqa: SLF001
            if not session.ensure_login(interactive=False):
                raise RuntimeError(
                    "Not logged in. Run `linkedin-apply login` in a terminal once.")
            runner.set_progress("LinkedIn login OK — starting job search")
            disc = discover.discover(
                session, settings, db,
                should_stop=runner.should_stop,
                progress=runner.set_progress,
            )
        if not runner.should_stop():
            match.score_jobs(settings, db, LLMClient(settings), should_stop=runner.should_stop)
        sheet.export(db, settings.sheet_file)
    return disc


def _run_generate(regenerate: bool = False) -> dict:
    from agent.llm.provider import LLMClient, llm_is_configured
    from agent.pipeline.generate import generate_all
    from agent import sheet
    from agent.runlock import run_lock

    settings = _settings()
    if not llm_is_configured(settings):
        raise RuntimeError("No LLM configured (set LLM_API_KEY + LLM_MODEL in .env).")
    db = _db(settings)
    with run_lock(settings.db_file.parent / "run.lock"):
        stats = generate_all(settings, db, LLMClient(settings), regenerate=regenerate,
                             should_stop=runner.should_stop)
        sheet.export(db, settings.sheet_file)
    return stats


def _run_apply(only_approved: bool = False, job_ids: list[str] | None = None) -> dict:
    from agent.browser.session import open_session
    from agent.llm.provider import LLMClient
    from agent.pipeline import apply as apply_phase
    from agent import sheet
    from agent.runlock import run_lock

    settings = _settings()
    db = _db(settings)
    with run_lock(settings.db_file.parent / "run.lock"):
        with open_session(settings) as session:
            session._progress = runner.set_progress  # noqa: SLF001
            if not session.ensure_login(interactive=False):
                raise RuntimeError(
                    "Not logged in. Run `linkedin-apply login` in a terminal once.")
            runner.set_progress("LinkedIn login OK — starting applications")
            stats = apply_phase.run_apply(
                session, settings, db, LLMClient(settings),
                only_approved=only_approved,
                job_ids=job_ids,
                should_stop=runner.should_stop, progress=runner.set_progress)
        sheet.export(db, settings.sheet_file)
    return stats


# ---- request bodies --------------------------------------------------------

class ApprovePayload(BaseModel):
    job_ids: list[str]
    approved: bool = True


class ResumeSourcePayload(BaseModel):
    job_ids: list[str]
    use_master: bool = False


class GeneratePayload(BaseModel):
    regenerate: bool = False


class ApplyPayload(BaseModel):
    only_approved: bool = False
    job_ids: list[str] = Field(default_factory=list)


# ---- app -------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(title="linkedin-ai-apply", docs_url="/api/docs")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/jobs")
    def list_jobs() -> dict:
        settings = _settings()
        db = _db(settings)
        jobs = [serialize_job(j, settings) for j in db.all_jobs()]
        return {
            "jobs": jobs,
            "count": len(jobs),
            "master_resume": master_resume_info(settings),
        }

    @app.get("/api/stats")
    def stats() -> dict:
        settings = _settings()
        db = _db(settings)
        counts: dict[str, int] = {}
        approved = 0
        for j in db.all_jobs():
            counts[j.status] = counts.get(j.status, 0) + 1
            if j.approved:
                approved += 1
        return {"by_status": counts, "approved": approved}

    @app.get("/api/profile")
    def profile() -> dict:
        settings = _settings()
        return {
            "intake": settings.intake or {},
            "intake_path": str(settings.intake_file),
        }

    @app.get("/api/runs")
    def runs() -> dict:
        settings = _settings()
        db = _db(settings)
        return {"runs": [r.as_row() for r in db.recent_runs(25)]}

    @app.post("/api/jobs/approve")
    def approve(payload: ApprovePayload) -> dict:
        settings = _settings()
        db = _db(settings)
        changed = sum(1 for jid in payload.job_ids if db.set_approved(jid, payload.approved))
        return {"changed": changed, "approved": payload.approved}

    @app.post("/api/jobs/resume-source")
    def resume_source(payload: ResumeSourcePayload) -> dict:
        settings = _settings()
        if payload.use_master and not settings.resolve_master_resume().exists():
            raise HTTPException(
                400, "Master resume not found. Upload one under Your resume → Replace.")
        db = _db(settings)
        changed = sum(
            1 for jid in payload.job_ids
            if db.set_use_master_resume(jid, payload.use_master))
        return {"changed": changed, "use_master": payload.use_master}

    @app.get("/api/master-resume")
    def master_resume():
        settings = _settings()
        path = settings.resolve_master_resume()
        if not path.exists():
            raise HTTPException(404, "Master resume not found")
        return FileResponse(path, filename=path.name)

    @app.post("/api/master-resume/upload")
    async def upload_master_resume(file: UploadFile = File(...)) -> dict:
        settings = _settings()
        content = await file.read()
        try:
            dest = save_master_resume(settings, file.filename or "master_resume.docx", content)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        intake = merge_intake_from_resume(load_settings())
        return {
            "ok": True,
            "name": dest.name,
            "path": str(dest),
            "intake": intake,
            "master_resume": master_resume_info(load_settings()),
        }

    @app.post("/api/jobs/{job_id}/resume/upload")
    async def upload_job_resume(job_id: str, file: UploadFile = File(...)) -> dict:
        settings = _settings()
        db = _db(settings)
        content = await file.read()
        with db.session() as s:
            job = db.get(s, job_id)
            if job is None:
                raise HTTPException(404, "job not found")
            try:
                dest = save_job_resume(settings, job, file.filename or "resume.docx", content)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        db.update(
            job_id,
            resume_path=str(dest),
            status=Status.generated.value,
            approved=False,
        )
        from agent import sheet
        sheet.export(db, settings.sheet_file)
        return {"ok": True, "name": dest.name, "path": str(dest)}

    @app.get("/api/jobs/{job_id}/resume")
    def resume(job_id: str):
        return _serve_doc(job_id, "resume")

    @app.get("/api/jobs/{job_id}/cover")
    def cover(job_id: str):
        return _serve_doc(job_id, "cover")

    def _serve_doc(job_id: str, which: str):
        settings = _settings()
        db = _db(settings)
        with db.session() as s:
            job = db.get(s, job_id)
            path = (job.resume_path if which == "resume" else job.cover_letter_path) if job else ""
        if not _under_output(settings, path):
            raise HTTPException(status_code=404, detail="document not available")
        return FileResponse(path, filename=Path(path).name)

    @app.get("/api/actions/status")
    def action_status() -> dict:
        return runner.snapshot()

    @app.post("/api/reset")
    def reset_data() -> dict:
        """Wipe all jobs and run history so the user can start fresh."""
        if runner.running:
            raise HTTPException(
                status_code=409,
                detail="Cannot reset while an action is running. Stop it first.",
            )
        settings = _settings()
        db = _db(settings)
        counts = db.clear_all()
        from agent import sheet
        sheet.export(db, settings.sheet_file)
        return {"ok": True, **counts}

    @app.post("/api/actions/find", status_code=202)
    def action_find() -> dict:
        if not runner.start("find", _run_find):
            raise HTTPException(status_code=409, detail="another action is running")
        return runner.snapshot()

    @app.post("/api/actions/generate", status_code=202)
    def action_generate(payload: GeneratePayload = GeneratePayload()) -> dict:
        if not runner.start("generate", lambda: _run_generate(payload.regenerate)):
            raise HTTPException(status_code=409, detail="another action is running")
        return runner.snapshot()

    @app.post("/api/actions/apply", status_code=202)
    def action_apply(payload: ApplyPayload = ApplyPayload()) -> dict:
        if not payload.job_ids and not payload.only_approved:
            raise HTTPException(
                400,
                "Select jobs in the grid or set only_approved to apply all approved jobs.",
            )
        if not runner.start(
            "apply",
            lambda: _run_apply(payload.only_approved, payload.job_ids or None),
        ):
            raise HTTPException(status_code=409, detail="another action is running")
        return runner.snapshot()

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)


app = create_app()
