# LinkedIn AI Apply — auto-apply to LinkedIn jobs with AI

<p>
  <a href="https://github.com/your-org/linkedin-ai-apply/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/your-org/linkedin-ai-apply/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: AGPL-3.0-or-later" src="https://img.shields.io/badge/license-AGPL--3.0--or--later-blue.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-blue.svg">
  <a href="https://github.com/astral-sh/ruff"><img alt="Ruff" src="https://img.shields.io/badge/lint-ruff-261230.svg"></a>
  <img alt="LLMs" src="https://img.shields.io/badge/LLM-OpenAI%20%7C%20Claude%20%7C%20Gemini%20%7C%20Ollama-555.svg">
</p>

**Stop hand-applying to hundreds of jobs.** This is an AI agent that finds LinkedIn
jobs matching your resume, writes a **tailored resume + cover letter for every single
one**, and applies for you — Easy Apply *and* external portals — while you do
literally anything else.

Job hunting is a numbers game, and doing it by hand is soul-crushing: the same form,
the same screening questions, the same "upload your resume" — fifty times a day. This
tool does the grind so you only spend time on the part that matters: the interviews.

If you searched for an **AI LinkedIn auto-apply bot**, an **automatic LinkedIn job
application tool**, or a way to **mass-apply to LinkedIn jobs with a tailored resume
per job** — this is it. Bring your own LLM (OpenAI, Anthropic Claude, Google Gemini,
or a local model via Ollama) and your own resume; everything runs locally on your
machine.

### What it does

- 🔎 **Finds the right jobs** — searches your titles/locations and scans `#hiring`
  posts, then scores each role against your resume with an LLM (skips the junk).
- ✍️ **Tailors every application** — a unique, impact-based, non-generic resume and
  cover letter per job. No more one-size-fits-all PDF.
- 🚀 **Actually applies** — auto-submits **Easy Apply**, and an AI agent attempts
  **external portals / posts** too. Captchas, login walls, or weird required fields →
  parked for `human_review` with a screenshot (never silently mis-submitted).
- 🧑‍⚖️ **You stay in control** — review-before-apply flow + a local **web UI**: read
  and edit every draft, approve the ones you like, submit only those.
- 🧠 **Learns your answers** — answer a screening question once; it reuses it forever.
- 🛟 **Robust by design** — single-instance run-lock, mid-run **logout detection**,
  a **Stop** button, loud LLM-failure alerts, and a **run history** so you always
  know what happened.

> [!WARNING]
> Automating LinkedIn login and applications violates LinkedIn's User Agreement and
> can get your account restricted or banned. Use conservative volume settings, prefer
> the persistent-login mode, and consider `SUBMIT_MODE=review` or `DRY_RUN=true` first.
> You are responsible for how you use this.

> [!NOTE]
> Architecture is adapted from
> [GodsScion/Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn)
> (AGPL-3.0). This project is therefore licensed **AGPL-3.0-or-later**.

## Quick start

Five steps from zero to applying. (Details for each are in the sections below.)

```bash
# 1. Install
python -m venv .venv && source .venv/bin/activate
pip install -e ".[pdf,ui]"
playwright install chromium

# 2. Configure — set your LLM model + API key, then your job titles/locations
cp .env.example .env && $EDITOR .env
$EDITOR config.yaml

# 3. Add your resume, then build your profile
#    drop your resume at profile/master_resume.docx (.docx/.pdf/.txt), then:
linkedin-apply intake

# 4. Log into LinkedIn once (handle 2FA in the window, press Enter)
linkedin-apply login

# 5. Go. Either let it run end-to-end:
linkedin-apply daily
#    ...or open the web UI to review & approve before anything is submitted:
linkedin-apply ui      # http://127.0.0.1:8765
```

New here? Start with the **web UI** (`linkedin-apply ui`) and the review-before-apply
flow — nothing is submitted until you approve it.

## Table of contents

- [Install](#install)
- [Configure](#configure)
- [First login (once)](#first-login-once)
- [Daily use](#daily-use)
- [Review-before-apply flow](#review-before-apply-flow-recommended-you-stay-in-control)
- [Web UI](#web-ui-review--approve--apply-in-the-browser)
- [Configuration reference](#configuration-reference-everything-is-a-toggle)
- [The spreadsheet](#the-spreadsheet-datajobsxlsx)
- [Resolving `human_review` jobs](#resolving-human_review-jobs)
- [Schedule it daily](#schedule-it-daily)
- [Contributing](#contributing)
- [License](#license)

## Install

The `pdf` extra enables PDF output; `ui` enables the local web UI; `dev` adds the
test/lint tools. Pick the extras you want:

```bash
cd linkedin-ai-apply
python -m venv .venv && source .venv/bin/activate
pip install -e ".[pdf,ui]"     # add ,dev if you plan to run tests / contribute
playwright install chromium
```

## Configure

```bash
cp .env.example .env          # set LLM_MODEL + your provider API key, tweak toggles
$EDITOR .env
$EDITOR config.yaml           # set your job titles, locations, filters, blacklists
```

Drop your master resume at `profile/master_resume.docx` (`.docx`, `.pdf`, or `.txt`)
**first** — the intake step parses it.

### PDF resumes (optional)

Tailored documents are authored as editable `.docx`. If you set
`RESUME_OUTPUT_FORMAT=pdf`, they're converted to PDF at apply time. Conversion uses
**LibreOffice** (preferred, headless, cross-platform) and falls back to Microsoft
Word via `docx2pdf`. Install LibreOffice once:

```bash
# macOS
brew install --cask libreoffice
# Windows
winget install TheDocumentFoundation.LibreOffice
# Debian/Ubuntu
sudo apt install libreoffice
```

It's auto-detected on PATH and at the standard install locations (incl. Windows
`Program Files`, which is not on PATH). Set `LIBREOFFICE_PATH` to point at the
`soffice` binary if you installed it somewhere custom. No converter? The tool falls
back to uploading the `.docx` (which LinkedIn accepts), or set
`RESUME_OUTPUT_FORMAT=docx` to skip conversion entirely.

Build your application profile (used to answer screening questions and tailor docs):

```bash
linkedin-apply intake         # interactive; writes profile/intake.yaml
# or: cp profile/intake.example.yaml profile/intake.yaml && $EDITOR profile/intake.yaml
```

`intake` reads your resume and auto-fills everything it can (name, email, phone,
location, LinkedIn/GitHub/portfolio links, current title/company, years of
experience). Fields recovered from the resume are shown with a `✓ from resume`
note and **skipped** — you're only prompted for what it couldn't find, plus the
things a resume can't tell us (visa sponsorship, salary, notice period, etc.).
Contact details are extracted deterministically; the rest uses your configured LLM
when available and degrades gracefully without one.

## First login (once)

```bash
linkedin-apply login          # log in + handle 2FA in the opened window, then press Enter
```

The session is stored in `browser_profile/` and reused — no password is stored.

### Or use your installed Chrome instead of the bundled Chromium

To run inside the Chrome you already use, set in `.env`:

```bash
USE_SYSTEM_CHROME=true
BROWSER_CHANNEL=chrome
CHROME_USER_DATA_DIR=~/Library/Application Support/Google/Chrome   # macOS default
CHROME_PROFILE_DIRECTORY=Default                                   # or "Profile 1", etc.
```

Quit Chrome before the first run. The tool launches your real Chrome against a
**dedicated copy** of your profile (in `browser_profile_chrome/`, seeded once from
the profile above) and drives it over CDP.

> Why a copy? Chrome 136+ refuses remote debugging on your live/default profile
> dir (anti–cookie-theft), and Playwright can't decrypt your existing macOS
> cookies when it launches the profile directly (`--use-mock-keychain`). Running
> a copy with the real Chrome binary avoids both problems.

**Important:** this only reuses a login if you're **already signed into LinkedIn
in that Chrome profile.** If you sign into LinkedIn in Safari (or a different
browser), there's nothing to import — the first run will open the Chrome window
logged out and you just sign in once there; it persists for all future runs.

Advanced: to attach to a Chrome you start yourself, launch it with
`--remote-debugging-port=9222 --user-data-dir=<some NON-default dir>` and set
`CDP_URL=http://localhost:9222` (takes precedence over `USE_SYSTEM_CHROME`).

Either way you can skip `linkedin-apply login`. To find your profile folder name,
open `chrome://version` and look at "Profile Path".

## Daily use

```bash
linkedin-apply daily          # discover -> score -> generate -> apply (the cron target)

# Or run the phases separately:
linkedin-apply find           # discover + score + write spreadsheet only
linkedin-apply apply          # generate docs + apply to queued jobs
linkedin-apply review         # resolve jobs the bot couldn't finish (see below)
linkedin-apply export         # rebuild the spreadsheet from the DB
```

### Review-before-apply flow (recommended; you stay in control)

Instead of letting the bot draft-and-submit in one shot, draft everything first,
read/edit the documents, approve the ones you want, then submit only those:

```bash
linkedin-apply intake          # 1. build your profile (one time)
linkedin-apply profile         # ...inspect the parsed profile + resume fields
linkedin-apply find            # 2. discover + score jobs
linkedin-apply generate        # 3. draft a tailored resume + cover letter per job
                               #    (editable .docx in output/resumes/, no browser)
linkedin-apply resumes         # 4. list what was drafted + open/edit the files
linkedin-apply approve <id>... # 5. sign off on the ones you want (or --all)
linkedin-apply apply --only-approved   # 6. submit ONLY the approved jobs
```

Key guarantees for step 4→6:

- Documents are saved as **editable `.docx`** (the source of truth). Edit them in
  Word/Docs; your edits are what gets submitted.
- `apply` **never regenerates** a job that already has documents, so it can't
  overwrite your edits. Pass `--regenerate` only if you want a fresh draft.
- If the output format is PDF, the PDF is rendered from your (possibly edited)
  docx at submit time, so edits flow through.
- `apply --only-approved` touches **only** jobs you approved; everything else is
  left untouched for a later run.

### Web UI (review + approve + apply in the browser)

```bash
pip install -e ".[ui]"        # one-time: installs fastapi + uvicorn
linkedin-apply login          # one-time: establishes the LinkedIn session
linkedin-apply ui             # serves http://127.0.0.1:8765
```

The UI is a thin layer over the same pipeline:

- A filterable **grid** of every job (search, status, min score, "approved only",
  "has resume"), sortable columns, and a stats bar.
- **Fetch jobs / Generate drafts / Apply approved** buttons run the heavy
  browser/LLM work in a background thread (one at a time; it shares the same
  run-lock as the CLI). A status pill shows progress, and a **Stop** button
  cancels a long-running action cleanly.
- **Resume / cover** links open the generated documents.
- Checkbox selection with **Approve selected / Reject selected**, then
  **Apply approved** submits only the jobs you signed off on.
- A **Recent runs** panel shows the history of every find/generate/apply run
  (discovered, applied, review, errors) so you can see what happened at a glance.
  If your LinkedIn session drops mid-run, the action stops with a clear message.

It binds to localhost only and performs no interactive login, so run
`linkedin-apply login` once in a terminal first.

### Useful flags (override .env/config.yaml per run)

```bash
linkedin-apply daily --submit-mode review --dry-run        # draft everything, submit nothing
linkedin-apply apply --submit-mode easy_only --max-applies 10
linkedin-apply find --match-threshold 80 --model anthropic/claude-3-5-sonnet-latest
```

## Configuration reference (everything is a toggle)

| Setting | Values | Meaning |
| --- | --- | --- |
| `SUBMIT_MODE` | `auto` / `easy_only` / `review` | `auto`: submit Easy Apply + AI-attempt external/posts. `easy_only`: Easy Apply auto, external → human_review. `review`: never submit, draft + queue. |
| `DRY_RUN` | `true` / `false` | Fill forms but never click final submit. |
| `MATCH_THRESHOLD` | `0`–`100` | Minimum LLM match score to enter the apply queue. |
| `MAX_APPLIES_PER_RUN` | int | Cap submissions per run. |
| `MIN_DELAY_MS` / `MAX_DELAY_MS` | int | Human-like pacing between actions. |
| `HEADLESS` | `true` / `false` | Headful vs headless browser. |
| `LOGIN_MODE` | `persistent` / `credentials` | Saved session vs auto-login from `.env`. |
| `LLM_MODEL` (+ keys) | litellm model string | `openai/...`, `anthropic/...`, `gemini/...`, `ollama/...`. |
| `ENABLE_HIRING_POSTS` | `true` / `false` | Toggle the `#hiring` post scan. |
| `RESUME_OUTPUT_FORMAT` | `pdf` / `docx` | Tailored document format. |

Search filters, blacklists, and per-person `match_threshold` live in `config.yaml`
(or override via `search:` in `profile/intake.yaml`).

## The spreadsheet (`data/jobs.xlsx`)

`job_id, title, company, location, url, source, match_score, match_reasons,
apply_type, status, needs_input, review_resolved, resume_path, cover_letter_path,
applied_at, last_seen, notes`.

Statuses: `new → generated → applied | human_review | skipped | error`.
`applied` and `skipped` are terminal. `human_review` is **not** terminal — see below.

## Resolving `human_review` jobs

When the bot can't finish an application on its own (an unmappable required
screening field, an external page it can't drive, captcha/login/OTP), it leaves
the job **as-is** in `human_review`, saves a screenshot under `output/screenshots/`,
and records the exact blocking questions in the `needs_input` column. Unresolved
`human_review` jobs are **skipped on every run** until you resolve them — so the
bot never re-touches them automatically.

To resolve them and let the agent pick them back up:

```bash
linkedin-apply review            # walk each blocked job, type the missing answers
linkedin-apply review --list     # just list what's waiting and why
linkedin-apply review --applied <job_id>   # you finished it by hand; mark it done
linkedin-apply review --retry <job_id>     # re-attempt next run without new answers
```

Answering via `review` does two things:

1. Saves your answer to `profile/learned_answers.yaml` (a simple `question: answer`
   map). On the next run these are merged into your screening answers, so the agent
   can answer the same question **for every future job**, not just this one.
2. Sets `review_resolved` on the job, which makes it eligible again — the next
   `apply`/`daily` run retries it. If it gets blocked by a *new* question, it goes
   back to `human_review` (resolved flag cleared) and waits for you again.

You can also resolve manually by editing `profile/learned_answers.yaml` directly
and then running `linkedin-apply review --retry <job_id>`. (The spreadsheet is a
read-only export of the database, so editing its cells does not feed back in —
use the `review` command to change job state.)

## Schedule it daily

macOS `launchd` or cron, e.g. cron at 9am:

```cron
0 9 * * *  cd /path/to/linkedin-ai-apply && /path/to/.venv/bin/linkedin-apply daily >> data/daily.log 2>&1
```

(Persistent login + a non-headless run works best; for headless, make sure the saved
session is still valid.)

## Distribute it to other people

Everything personal lives in two files: `profile/intake.yaml` and `profile/master_resume.docx`
(plus their own `.env` with their LLM key). Hand someone the repo, have them run
`linkedin-apply intake`, drop in their resume, `linkedin-apply login`, then
`linkedin-apply daily`.

## Project layout

```
agent/
  config.py            # layered config (config.yaml + intake.yaml + .env + CLI)
  db.py / models.py    # SQLite state + status lifecycle + dedup + run history
  sheet.py             # DB -> xlsx/csv
  llm/                 # pluggable provider (litellm) + prompts
  browser/             # session, finders, search, hiring_posts, easy_apply, external_apply, forms
  resume/              # parser + tailored docx/pdf builder
  pipeline/            # discover, match, generate, apply
  web/                 # optional local FastAPI UI (server, runner, static/)
  cli.py               # typer entrypoint
tests/                 # fast unit tests (no browser/network required)
```

## Contributing

Contributions are welcome! Install the dev extras and run the checks CI runs:

```bash
pip install -e ".[dev,ui,pdf]"
ruff check agent tests      # lint
pytest -q                   # tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for details, and
[SECURITY.md](SECURITY.md) to report a vulnerability. Notable changes are tracked in
[CHANGELOG.md](CHANGELOG.md).

## License

Licensed under **AGPL-3.0-or-later** — see [LICENSE](LICENSE). Architecture is adapted
from [GodsScion/Auto_job_applier_linkedIn](https://github.com/GodsScion/Auto_job_applier_linkedIn)
(AGPL-3.0). If you run a modified version as a network service, the AGPL requires you
to offer users its source code.
