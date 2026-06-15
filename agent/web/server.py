"""FastAPI app: a thin, local-only UI over the existing pipeline.

Reads (job list, profile) are plain DB queries. Mutations (approve/reject) are
fast DB writes. The heavy actions (find / generate / apply) are delegated to a
single background worker thread via :class:`ActionRunner`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent.config import Settings, load_settings
from agent.db import Database
from agent.models import Job
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
    row["resume_exists"] = _under_output(settings, job.resume_path)
    row["cover_exists"] = _under_output(settings, job.cover_letter_path)
    row["resume_filename"] = Path(job.resume_path).name if job.resume_path else ""
    return row


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
            if not session.ensure_login(interactive=False):
                raise RuntimeError(
                    "Not logged in. Run `linkedin-apply login` in a terminal once.")
            disc = discover.discover(session, settings, db, should_stop=runner.should_stop)
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


def _run_apply(only_approved: bool = True) -> dict:
    from agent.browser.session import open_session
    from agent.llm.provider import LLMClient
    from agent.pipeline import apply as apply_phase
    from agent import sheet
    from agent.runlock import run_lock

    settings = _settings()
    db = _db(settings)
    with run_lock(settings.db_file.parent / "run.lock"):
        with open_session(settings) as session:
            if not session.ensure_login(interactive=False):
                raise RuntimeError(
                    "Not logged in. Run `linkedin-apply login` in a terminal once.")
            stats = apply_phase.run_apply(
                session, settings, db, LLMClient(settings), only_approved=only_approved,
                should_stop=runner.should_stop)
        sheet.export(db, settings.sheet_file)
    return stats


# ---- request bodies --------------------------------------------------------

class ApprovePayload(BaseModel):
    job_ids: list[str]
    approved: bool = True


class GeneratePayload(BaseModel):
    regenerate: bool = False


class ApplyPayload(BaseModel):
    only_approved: bool = True


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
        return {"jobs": jobs, "count": len(jobs)}

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
        return {"intake": _settings().intake or {}}

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

    @app.post("/api/actions/stop")
    def action_stop() -> dict:
        stopped = runner.request_stop()
        if not stopped:
            raise HTTPException(status_code=409, detail="nothing is running")
        return runner.snapshot()

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
        if not runner.start("apply", lambda: _run_apply(payload.only_approved)):
            raise HTTPException(status_code=409, detail="another action is running")
        return runner.snapshot()

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port)


app = create_app()
