"""Command-line interface (typer)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console

from agent.browser.session import LoggedOutError
from agent.config import PROJECT_ROOT, Settings, SubmitMode, load_settings
from agent.db import Database
from agent.llm.provider import LLMClient, LLMUnavailableError

RUN_ERRORS = (LoggedOutError, LLMUnavailableError)

app = typer.Typer(add_completion=False, help="Agentic daily LinkedIn job apply tool.")
console = Console()


# ---- shared option wiring --------------------------------------------------

def _settings(
    submit_mode: Optional[str],
    dry_run: Optional[bool],
    max_applies: Optional[int],
    match_threshold: Optional[int],
    headless: Optional[bool],
    model: Optional[str],
) -> Settings:
    s = load_settings()
    overrides = {}
    if submit_mode is not None:
        overrides["submit_mode"] = SubmitMode(submit_mode)
    if dry_run is not None:
        overrides["dry_run"] = dry_run
    if max_applies is not None:
        overrides["max_applies_per_run"] = max_applies
    if match_threshold is not None:
        overrides["match_threshold"] = match_threshold
    if headless is not None:
        overrides["headless"] = headless
    if model is not None:
        overrides["llm_model"] = model
    s = s.override(**overrides)
    s.ensure_dirs()
    return s


def _llm(settings: Settings) -> LLMClient:
    return LLMClient(settings)


def _llm_ready(settings: Settings) -> bool:
    """True if an LLM is configured; otherwise print a helpful message."""
    from agent.llm.provider import llm_is_configured

    if llm_is_configured(settings):
        return True
    console.print("[red]No LLM configured.[/] Set LLM_API_KEY (or a provider key) "
                  "and LLM_MODEL in .env to draft tailored documents.")
    return False


def _db(settings: Settings) -> Database:
    return Database(settings.db_file)


def _acquire_lock(settings: Settings):
    """Fail fast if another browser-driving run is already in progress."""
    from agent.runlock import AlreadyRunning, run_lock

    lock = run_lock(settings.db_file.parent / "run.lock")
    try:
        lock.__enter__()
    except AlreadyRunning as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    return lock


def _extract_from_resume(settings: Settings) -> dict:
    """Parse the master resume and return structured fields to pre-fill intake.

    Falls back gracefully: if there's no resume, parsing fails, or no LLM is
    configured, returns whatever could be found (possibly just regex contact
    details, or {})."""
    resume = settings.resolve_master_resume()
    if not resume.exists():
        console.print(f"[dim]No resume at {resume}; you'll be asked for everything.[/]\n")
        return {}

    from agent.llm.provider import llm_is_configured
    from agent.resume.parser import extract_text
    from agent.resume.profile import extract_profile_fields

    try:
        text = extract_text(resume)
    except Exception as exc:
        console.print(f"[yellow]Could not read resume ({exc}); asking for everything.[/]\n")
        return {}

    llm = None
    if llm_is_configured(settings):
        try:
            llm = LLMClient(settings)
        except Exception:
            llm = None
    else:
        console.print("[dim]No LLM API key set; extracting contact details only. "
                      "Set one in .env to also auto-fill name/title/experience.[/]")

    fields = extract_profile_fields(text, llm)
    if fields:
        console.print(f"[green]Parsed {len(fields)} field(s) from your resume.[/] "
                      "You'll only be asked for what's missing.\n")
    else:
        console.print("[yellow]Couldn't auto-extract fields from the resume; "
                      "asking for everything.[/]\n")
    return fields


# ---- commands --------------------------------------------------------------

@app.command()
def intake():
    """Interactively build profile/intake.yaml.

    Reads your master resume first and pre-fills everything it can; you are only
    asked for the fields it could not recover from the resume.
    """
    settings = load_settings()
    extracted = _extract_from_resume(settings)

    console.print("[bold]Let's build your application profile.[/] Press Enter to skip a field.\n")

    def ask(prompt, default=""):
        return typer.prompt(prompt, default=default, show_default=bool(default))

    def auto(field_key, prompt, default=""):
        """Use the resume value if we found one (skip the question); else ask."""
        val = extracted.get(field_key)
        if val not in (None, ""):
            console.print(f"  [green]\u2713 from resume[/] {prompt}: [cyan]{val}[/]")
            return str(val)
        return ask(prompt, default)

    personal = {
        "full_name": auto("full_name", "Full name"),
        "email": auto("email", "Email"),
        "phone": auto("phone", "Phone"),
        "city": auto("city", "City"),
        "state": auto("state", "State/Region"),
        "country": auto("country", "Country", "United States"),
    }
    links = {
        "linkedin": auto("linkedin", "LinkedIn URL"),
        "github": auto("github", "GitHub URL"),
        "portfolio": auto("portfolio", "Portfolio URL"),
    }
    experience = {
        "total_years": int(auto("total_years", "Total years of experience", "0") or 0),
        "current_title": auto("current_title", "Current title"),
        "current_company": auto("current_company", "Current company"),
    }

    # Fields a resume can't tell us - always ask.
    console.print("\n[bold]A few things not in your resume:[/]")
    eligibility = {
        "work_authorization": ask("Work authorization", "Authorized to work"),
        "requires_visa_sponsorship": ask("Requires visa sponsorship? (yes/no)", "no")
        .lower().startswith("y"),
        "willing_to_relocate": ask("Willing to relocate? (yes/no)", "yes")
        .lower().startswith("y"),
        "notice_period_days": int(ask("Notice period (days)", "14") or 14),
    }
    compensation = {
        "expected_salary": int(ask("Expected salary (number)", "0") or 0),
        "currency": ask("Currency", "USD"),
    }

    data = {
        "personal": personal,
        "links": links,
        "eligibility": eligibility,
        "compensation": compensation,
        "experience": experience,
        "screening_answers": {
            "years of experience": str(experience["total_years"]),
            "authorized to work": "Yes",
            "require sponsorship": "No",
            "willing to relocate": "Yes",
            "notice period": "2 weeks",
        },
        "eeo": {
            "gender": "Decline to self identify",
            "race": "Decline to self identify",
            "veteran_status": "Decline to self identify",
            "disability_status": "Decline to self identify",
        },
    }

    out = PROJECT_ROOT / "profile" / "intake.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    console.print(f"\n[green]Saved[/] {out}")


@app.command()
def login(headless: bool = typer.Option(False, help="Run browser headless (not recommended).")):
    """Open the browser so you can log in to LinkedIn once; the session is saved."""
    from agent.browser.session import open_session

    settings = _settings(None, None, None, None, headless, None)
    with open_session(settings) as session:
        ok = session.ensure_login(interactive=True)
        console.print("[green]Logged in.[/]" if ok else "[red]Login not detected.[/]")


@app.command()
def find(
    submit_mode: Optional[str] = typer.Option(None),
    match_threshold: Optional[int] = typer.Option(None),
    headless: Optional[bool] = typer.Option(None),
    model: Optional[str] = typer.Option(None),
):
    """Discover + score jobs and write them to the spreadsheet (no applying)."""
    from agent.browser.session import open_session
    from agent.pipeline import discover, match
    from agent import sheet

    settings = _settings(submit_mode, None, None, match_threshold, headless, model)
    db = _db(settings)
    lock = _acquire_lock(settings)
    try:
        with open_session(settings) as session:
            if not session.ensure_login(interactive=True):
                console.print("[red]Not logged in. Run `linkedin-apply login` first.[/]")
                raise typer.Exit(1)
            discover.discover(session, settings, db)
        match.score_jobs(settings, db, _llm(settings))
        path = sheet.export(db, settings.sheet_file)
        console.print(f"[green]Sheet updated:[/] {path}")
    except RUN_ERRORS as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    finally:
        lock.__exit__(None, None, None)


@app.command()
def apply(
    submit_mode: Optional[str] = typer.Option(None, help="auto | easy_only | review"),
    dry_run: Optional[bool] = typer.Option(None),
    max_applies: Optional[int] = typer.Option(None),
    match_threshold: Optional[int] = typer.Option(None),
    headless: Optional[bool] = typer.Option(None),
    model: Optional[str] = typer.Option(None),
    only_approved: bool = typer.Option(
        False, "--only-approved",
        help="Only submit jobs you've approved (review-gated flow)."),
    regenerate: bool = typer.Option(
        False, "--regenerate",
        help="Re-draft documents even if they already exist (discards edits)."),
    no_generate: bool = typer.Option(
        False, "--no-generate",
        help="Do not draft tailored documents; apply with master resume (or existing drafts)."),
):
    """Apply to queued jobs. Reuses already-generated (and possibly hand-edited)
    documents; only drafts missing ones unless --no-generate. Use --only-approved for the review flow."""
    from agent.browser.session import open_session
    from agent.pipeline import apply as apply_phase
    from agent import sheet

    settings = _settings(submit_mode, dry_run, max_applies, match_threshold, headless, model)
    db = _db(settings)
    lock = _acquire_lock(settings)
    try:
        with open_session(settings) as session:
            if not session.ensure_login(interactive=True):
                console.print("[red]Not logged in. Run `linkedin-apply login` first.[/]")
                raise typer.Exit(1)
            stats = apply_phase.run_apply(
                session, settings, db, _llm(settings),
                only_approved=only_approved, regenerate=regenerate,
                skip_generate=no_generate)
        sheet.export(db, settings.sheet_file)
        console.print(f"[bold]Apply summary:[/] {stats}")
    except RUN_ERRORS as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    finally:
        lock.__exit__(None, None, None)


@app.command()
def generate(
    regenerate: bool = typer.Option(
        False, "--regenerate", help="Re-draft even jobs that already have documents."),
    match_threshold: Optional[int] = typer.Option(None),
    model: Optional[str] = typer.Option(None),
):
    """Draft tailored resume + cover letter for every queued job (no applying).

    This is the 'review' step: it generates editable .docx files under
    output/resumes and output/cover_letters so you can read and fix them before
    approving and applying. It never opens the browser or submits anything.
    """
    from agent.pipeline.generate import generate_all
    from agent import sheet

    settings = _settings(None, None, None, match_threshold, None, model)
    if not _llm_ready(settings):
        raise typer.Exit(1)
    db = _db(settings)
    lock = _acquire_lock(settings)
    try:
        stats = generate_all(settings, db, _llm(settings), regenerate=regenerate)
        sheet.export(db, settings.sheet_file)
    finally:
        lock.__exit__(None, None, None)
    console.print(f"[bold]Draft summary:[/] {stats}")
    console.print(f"[green]Review the documents in[/] {settings.output_path / 'resumes'}")
    console.print("Then approve the ones you want: "
                  "[cyan]linkedin-apply approve --all[/] (or by job id), "
                  "and submit with [cyan]linkedin-apply apply --only-approved[/].")


@app.command()
def resumes(
    pending_only: bool = typer.Option(
        False, "--pending", help="Only show jobs not yet approved."),
):
    """List generated documents and their approval state (the review queue)."""
    settings = load_settings()
    settings.ensure_dirs()
    db = _db(settings)
    approved_filter = False if pending_only else None
    jobs = db.generated_jobs(approved=approved_filter)
    if not jobs:
        console.print("[green]No generated documents awaiting review.[/] "
                      "Run `linkedin-apply generate` first.")
        return
    for job in jobs:
        mark = "[green]approved[/]" if job.approved else "[yellow]not approved[/]"
        console.print(f"\n[bold]{job.title}[/] @ {job.company}  "
                      f"[dim](score {job.match_score}, {mark})[/]")
        console.print(f"  job_id: [cyan]{job.job_id}[/]")
        console.print(f"  resume: {job.resume_path or '(none)'}")
        console.print(f"  cover:  {job.cover_letter_path or '(none)'}")


@app.command()
def approve(
    job_ids: list[str] = typer.Argument(None, help="Job ids to approve."),
    all_: bool = typer.Option(False, "--all", help="Approve every generated job."),
    reject: bool = typer.Option(False, "--reject", help="Un-approve instead of approve."),
):
    """Approve (or --reject) generated jobs so `apply --only-approved` submits them."""
    settings = load_settings()
    settings.ensure_dirs()
    db = _db(settings)

    if all_ and not reject:
        n = db.approve_all_generated()
        console.print(f"[green]Approved {n} job(s).[/]")
        return
    if not job_ids:
        console.print("[yellow]Pass one or more job ids, or use --all.[/]")
        raise typer.Exit(1)
    target = not reject
    done = 0
    for jid in job_ids:
        if db.set_approved(jid, target):
            done += 1
        else:
            console.print(f"[yellow]Unknown job id:[/] {jid}")
    verb = "Approved" if target else "Un-approved"
    console.print(f"[green]{verb} {done} job(s).[/]")


@app.command("resume-check")
def resume_check():
    """Verify the master resume can be read — especially PDF text on Windows."""
    from agent.resume.parser import (
        MIN_RESUME_CHARS,
        clear_resume_cache,
        extract_text,
        resume_text_looks_valid,
    )

    settings = load_settings()
    path = settings.resolve_master_resume()
    console.print(f"[bold]Master resume path:[/] {path}")
    if not path.exists():
        console.print("[red]File not found.[/] Upload via the UI or set master_resume_path.")
        raise typer.Exit(1)

    clear_resume_cache()
    try:
        text = extract_text(path)
    except Exception as exc:
        console.print(f"[red]Could not read resume:[/] {exc}")
        raise typer.Exit(1) from exc

    n = len(text.strip())
    console.print(f"[bold]Extracted text:[/] {n} characters")
    if resume_text_looks_valid(text):
        console.print(f"[green]OK[/] — at least {MIN_RESUME_CHARS} characters (scoring/tailoring should work).")
    else:
        console.print(
            f"[red]Too little text[/] — need at least {MIN_RESUME_CHARS} characters. "
            "If this is a PDF, it may be image-only (scanned). "
            "Re-save as a text PDF, upload a .docx, or paste into a .txt file."
        )
    preview = text.strip()[:400]
    if preview:
        console.print("\n[dim]Preview (first ~400 chars):[/]")
        console.print(preview)
    elif path.suffix.lower() == ".pdf":
        console.print(
            "\n[yellow]PDF returned no text.[/] On Windows this often means the PDF "
            "has no selectable text layer. Export from Word/Google Docs as .docx instead."
        )
    raise typer.Exit(0 if resume_text_looks_valid(text) else 1)


@app.command()
def profile():
    """Show the parsed intake profile and what the resume parser can extract.

    Handy before applying (and as the data source for a future UI)."""
    settings = load_settings()
    intake = settings.intake or {}
    if not intake:
        console.print("[yellow]No intake found.[/] Run `linkedin-apply intake` first.")
    else:
        console.print("[bold]Application profile (profile/intake.yaml):[/]")
        console.print(yaml.safe_dump(intake, sort_keys=False, allow_unicode=True))

    console.print("[bold]Fields extractable from your master resume now:[/]")
    extracted = _extract_from_resume(settings)
    if extracted:
        for k, v in extracted.items():
            console.print(f"  [cyan]{k}[/]: {v}")
    else:
        console.print("  [dim](nothing extracted)[/]")


@app.command("schedule-run")
def schedule_run(
    submit_mode: Optional[str] = typer.Option(None),
    dry_run: Optional[bool] = typer.Option(None),
    max_applies: Optional[int] = typer.Option(None),
    match_threshold: Optional[int] = typer.Option(None),
    headless: Optional[bool] = typer.Option(None),
    model: Optional[str] = typer.Option(None),
    no_generate: bool = typer.Option(
        False, "--no-generate",
        help="Do not draft tailored documents during apply; use master resume instead."),
):
    """Full scheduled pipeline: find new jobs, score, then apply. Wire this to cron/launchd."""
    from agent.browser.session import open_session
    from agent.pipeline import apply as apply_phase, discover, match
    from agent import sheet

    settings = _settings(submit_mode, dry_run, max_applies, match_threshold, headless, model)
    db = _db(settings)
    llm = _llm(settings)
    lock = _acquire_lock(settings)
    try:
        with open_session(settings) as session:
            if not session.ensure_login(interactive=False):
                console.print(
                    "[red]Not logged in. Run `linkedin-apply login` in a terminal first.[/]"
                )
                raise typer.Exit(1)
            console.print("[bold]Schedule run:[/] discover…")
            discover.discover(session, settings, db)
            console.print("[bold]Schedule run:[/] score…")
            match.score_jobs(settings, db, llm)
            sheet.export(db, settings.sheet_file)
            console.print("[bold]Schedule run:[/] apply…")
            stats = apply_phase.run_apply(session, settings, db, llm, skip_generate=no_generate)
        sheet.export(db, settings.sheet_file)
        console.print(f"[bold]Schedule run complete:[/] {stats}")
    except RUN_ERRORS as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    finally:
        lock.__exit__(None, None, None)


@app.command()
def ui(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8765, help="Port."),
):
    """Launch the local web UI (review the grid, approve, and apply in the browser).

    Reads/approvals are instant; Fetch/Generate/Apply run in the background. You
    must `linkedin-apply login` once in a terminal first so the session exists.
    """
    try:
        from agent.web.server import serve
    except ModuleNotFoundError as exc:
        console.print(f"[red]Web UI deps missing ({exc}).[/] Install with: "
                      "[cyan]pip install -e \".[ui]\"[/]")
        raise typer.Exit(1)
    console.print(f"[green]UI at[/] http://{host}:{port}")
    serve(host=host, port=port)


@app.command()
def export():
    """Regenerate the spreadsheet from the database."""
    from agent import sheet

    settings = load_settings()
    settings.ensure_dirs()
    db = _db(settings)
    path = sheet.export(db, settings.sheet_file)
    console.print(f"[green]Exported:[/] {path}")


@app.command()
def review(
    list_only: bool = typer.Option(False, "--list", "-l", help="Just list jobs awaiting review."),
    applied: Optional[str] = typer.Option(
        None, "--applied", help="Mark JOB_ID as applied (you finished it by hand)."),
    retry: Optional[str] = typer.Option(
        None, "--retry", help="Mark JOB_ID resolved so it's retried next run (no new answers)."),
):
    """Resolve jobs parked in human_review.

    With no flags this walks each blocked job, asks you for the missing answers,
    saves them to profile/learned_answers.yaml (so the agent learns them for all
    future jobs), and marks the job resolved so the next run retries it.
    """
    import json

    from agent.models import Status

    settings = load_settings()
    settings.ensure_dirs()
    db = _db(settings)

    if applied:
        db.update(applied, status=Status.applied.value, review_resolved=False, needs_input="")
        console.print(f"[green]Marked applied:[/] {applied}")
        return
    if retry:
        db.update(retry, review_resolved=True)
        console.print(f"[green]Marked for retry next run:[/] {retry}")
        return

    jobs = db.human_review_jobs(unresolved_only=True)
    if not jobs:
        console.print("[green]Nothing awaiting review.[/]")
        return

    learned = _load_learned(settings.learned_answers_file)

    for job in jobs:
        console.print(f"\n[bold]{job.title}[/] @ {job.company}  "
                      f"[dim]({job.apply_type}, score {job.match_score})[/]")
        console.print(f"  {job.url}")
        console.print(f"  [yellow]Reason:[/] {job.notes}")

        questions = json.loads(job.needs_input) if job.needs_input else []
        if list_only:
            for q in questions:
                console.print(f"    - {q['question']}")
            continue
        if not questions:
            console.print("  [dim]No structured questions captured (likely a captcha/login "
                          "or external page). Finish it manually, then run "
                          "`review --applied <job_id>`, or `--retry <job_id>` to re-attempt.[/]")
            continue

        answered_any = False
        for q in questions:
            opts = q.get("options") or []
            hint = f" options: {opts}" if opts else ""
            ans = typer.prompt(f"  Answer for '{q['question']}'{hint}", default="", show_default=False)
            if ans.strip():
                learned[q["question"].strip().lower()] = ans.strip()
                answered_any = True

        if answered_any:
            _save_learned(settings.learned_answers_file, learned)
            db.update(job.job_id, review_resolved=True)
            console.print("  [green]Saved answers and queued for retry next run.[/]")
        else:
            console.print("  [dim]No answers entered; left as-is.[/]")

    console.print(f"\n[green]Learned answers file:[/] {settings.learned_answers_file}")


def _load_learned(path: Path) -> dict:
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def _save_learned(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=True, allow_unicode=True)


# ---- schedule --------------------------------------------------------------

schedule_app = typer.Typer(help="Configure automated search/apply at a set time.")
app.add_typer(schedule_app, name="schedule")


@schedule_app.command("show")
def schedule_show():
    """Show the configured schedule and whether launchd has it installed."""
    from agent.schedule import schedule_status

    settings = load_settings()
    settings.ensure_dirs()
    status = schedule_status(settings)
    console.print("[bold]Schedule configuration[/]")
    console.print(f"  enabled:       {status['enabled']}")
    console.print(f"  schedule:      {status['description']}")
    console.print(f"  mode:          {status['mode']}")
    if status["mode"] == "interval":
        console.print(f"  interval:      every {status['interval_hours']}h")
    else:
        console.print(f"  time:          {status['time']} (local)")
        console.print(f"  days:          {', '.join(status['days'])}")
    console.print(f"  workflow:      {status['workflow']}")
    console.print(f"  only_approved: {status['only_approved']}")
    console.print(f"  skip_generate: {status['skip_generate']}")
    schedules = status.get("schedules") or []
    if schedules:
        console.print(f"\n[bold]Saved schedule[/] (max {status.get('max_schedules', 1)} active)")
        for s in schedules:
            mark = "[green]RUNNING[/]" if s["status"] == "active" else s["status_label"]
            console.print(f"  [{mark}] {s['description']} — {s['workflow_label']}")
            if s.get("can_delete"):
                console.print("    [dim]Delete: linkedin-apply schedule delete[/]")
    console.print(f"  config:      {status['config_path']}")
    console.print(f"  command:     {' '.join(status['command'])}")
    console.print(f"  log:         {status['log_path']}")
    if status["supports_install"]:
        plat = status.get("install_platform") or "os"
        state = "[green]active[/]" if status["loaded"] else (
            "[yellow]installed but not active[/]" if status["installed"] else "[dim]not installed[/]")
        console.print(f"  scheduler:   {state}  ({plat}: {status.get('task_name', '')})")
        if status.get("agent_path"):
            console.print(f"  agent:       {status['agent_path']}")
    else:
        console.print("\n[bold]Manual install (Linux/other):[/]")
        console.print(f"  {status.get('manual_install') or status['cron_line']}")


@schedule_app.command("set")
def schedule_set(
    enable: Optional[bool] = typer.Option(None, "--enable/--disable"),
    mode: Optional[str] = typer.Option(
        None, "--mode", help="interval (every N hours) | daily (once at time on weekdays)."),
    interval_hours: Optional[int] = typer.Option(
        None, "--interval", "-i", help="Run every N hours: 1, 2, 4, 6, 12, or 24."),
    time: Optional[str] = typer.Option(None, "--time", "-t", help="Local time HH:MM (daily mode)."),
    days: Optional[str] = typer.Option(
        None, "--days", help="Comma-separated weekdays (daily mode): mon,tue,..."),
    workflow: Optional[str] = typer.Option(
        None, "--workflow", help="schedule-run (search+apply) | find | apply"),
    only_approved: Optional[bool] = typer.Option(
        None, "--only-approved/--all-generated",
        help="For workflow=apply: only submit approved jobs."),
    install: bool = typer.Option(
        False, "--install", help="Install (or refresh) the launchd agent after saving."),
):
    """Save schedule settings to config.yaml."""
    from agent.schedule import ScheduleError, install_schedule, merge_schedule_update, save_schedule_config

    settings = load_settings()
    try:
        updates: dict = {
            "enabled": enable,
            "time": time,
            "days": days,
            "workflow": workflow,
            "only_approved": only_approved,
        }
        if mode is not None:
            updates["mode"] = mode
        if interval_hours is not None:
            updates["interval_hours"] = interval_hours
            updates["mode"] = "interval"
        updated = merge_schedule_update(settings.schedule, **updates)
    except ScheduleError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)

    path = save_schedule_config(updated)
    console.print(f"[green]Saved schedule to[/] {path}")
    from agent.schedule import schedule_description
    console.print(f"  {schedule_description(updated)} — workflow={updated.workflow.value}"
                  f"{' (enabled)' if updated.enabled else ' (disabled)'}")

    if install:
        settings = load_settings()
        result = install_schedule(settings)
        if result.ok:
            console.print(f"[green]{result.message}[/]")
        else:
            console.print(f"[yellow]{result.message}[/]")
            raise typer.Exit(1)


@schedule_app.command("install")
def schedule_install():
    """Install the OS scheduler from the current config.yaml schedule (macOS/Windows)."""
    from agent.schedule import install_schedule

    settings = load_settings()
    settings.ensure_dirs()
    result = install_schedule(settings)
    if result.ok:
        console.print(f"[green]{result.message}[/]")
    else:
        console.print(f"[red]{result.message}[/]")
        raise typer.Exit(1)


@schedule_app.command("delete")
def schedule_delete():
    """Remove the active OS schedule and disable it in config."""
    from agent.schedule import delete_active_schedule, schedule_status

    settings = load_settings()
    status = schedule_status(settings)
    if not any(s.get("can_delete") for s in status.get("schedules", [])):
        console.print("[yellow]No active or installed schedule to delete.[/]")
        raise typer.Exit(1)
    result = delete_active_schedule(settings)
    if result.ok:
        console.print(f"[green]{result.message}[/]")
    else:
        console.print(f"[red]{result.message}[/]")
        raise typer.Exit(1)


@schedule_app.command("uninstall")
def schedule_uninstall():
    """Remove the installed schedule (launchd agent or Windows task)."""
    from agent.schedule import uninstall_schedule

    settings = load_settings()
    result = uninstall_schedule(settings)
    if result.ok:
        console.print(f"[green]{result.message}[/]")
    else:
        console.print(f"[red]{result.message}[/]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
