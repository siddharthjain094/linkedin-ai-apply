# Web UI review log

**Scope:** `agent/web/static/` (index.html, app.js, styles.css) + `agent/web/server.py`  
**Reviewed:** 2026-06-21  
**Method:** Code audit, API behavior, live browser pass (port 8765 stale server noted; 8766 with current code OK)

---

## Summary

The UI is functional but the **layout stacks too much chrome above the job grid**, **pipeline steps mislead users about flow**, and **bulk actions are far from the data they affect**. Several bugs cause silent failures, repeated toasts on refresh, and accidental apply/approve of hidden selections.

**Task count:** 47 tracked items below (P0–P3).

---

## Layout & arrangement

How the page is structured today (top → bottom):

```
┌─ FIXED: site-header ─────────────────────────────────────────────┐
│  topbar: brand | pipeline (4 steps) | Schedule | Stop | status   │
│  resume-bar: Master resume | hint | Replace master               │
│  filters: search | status | score | checkboxes | bulk actions    │
└──────────────────────────────────────────────────────────────────┘
┌─ SCROLL: app-scroll ──────────────────────────────────────────────┐
│  intake-panel (open by default)                                  │
│  subbar: Recent runs | Reset all data | [runs-panel expands]     │
│  stats pills                                                     │
│  main: 10-column job table                                       │
└──────────────────────────────────────────────────────────────────┘
  toast (fixed bottom, z-index 20)
  schedule-modal (z-index 100)
```

### Arrangement issues

| ID | Issue | Why it hurts |
|----|--------|--------------|
| **LAY-01** | **~3 fixed rows before any job data** (topbar + resume bar + filter bar) | Job grid is not the hero; first-time users see chrome, not work to do. |
| **LAY-02** | **Bulk actions (Approve, Apply with) live in the filter bar** | Select rows → scroll up to act; breaks review workflow. |
| **LAY-03** | **Intake panel sits above stats + table when open** | Default-open intake pushes the grid down ~40vh. |
| **LAY-04** | **Recent runs expands inline between subbar and stats** | Opening run history shifts the grid vertically (layout jump). |
| **LAY-05** | **Reset all data adjacent to casual “Recent runs” toggle** | Destructive control visually grouped with low-risk control. |
| **LAY-06** | **Stop button appears/disappears in topbar** | Header reflows when runs start/stop. |
| **LAY-07** | **Two resume contexts in header only** (master bar + filter “Apply with”) | Per-row resume toggles are in the table; three resume touchpoints with no grouping. |
| **LAY-08** | **10-column table, no horizontal scroll affordance** | Files + Apply with + Title(min 280px) overflow on laptop/mobile. |
| **LAY-09** | **Sticky table header inside scroll region** | Good for rows, but combined with fixed site-header → double sticky chrome while scrolling. |
| **LAY-10** | **Schedule is a modal; everything else is inline** | Automation feels bolted on; no persistent “next run” indicator in header. |
| **LAY-11** | **Stats pills between runs subbar and table** | Stats relate to the grid but sit above a collapsible runs block—not aligned with either. |
| **LAY-12** | **Pipeline step 4 always styled as primary CTA (solid blue)** | Visually dominates topbar even when user should fetch jobs first. |
| **LAY-13** | **Empty state below table headers** | User sees column headers + blank body before data loads or when empty—looks broken. |
| **LAY-14** | **Filter bar mixes discovery filters with selection actions** | Search/status/score vs “0 selected” / Approve / Reject / Apply with in one row. |
| **LAY-15** | **Master resume bar separate from step 1 “Intake & resume”** | Same domain (profile/resume) split across fixed bar + collapsible panel + table column. |

### Suggested layout direction (for tasks)

1. **Hero zone:** job table + selection toolbar attached to table (sticky above tbody or floating bar on selection).
2. **Secondary zone:** intake as drawer/collapsed by default; schedule as modal (keep) but show installed-state chip in topbar.
3. **Tertiary zone:** stats as compact row tied to table; runs as slide-over or bottom sheet instead of inline expand.
4. **Split filter bar:** left = filters; right = selection count + actions (or move actions to selection bar only when `selected.size > 0`).

---

## Functional bugs

| ID | Severity | Issue | Location |
|----|----------|--------|----------|
| **BUG-01** | P0 | **Stale run status re-toasts on every page load** — `pollStatus()` treats persisted `error`/`result` as new completion | `app.js` `pollStatus()`, `runner.py` never clears `action`/`error` |
| **BUG-02** | P0 | **`loadJobs()` uncaught on init** — API failure = silent blank grid, intake may not load | `app.js` lines 793–797 |
| **BUG-03** | P0 | **Apply selected bypasses approval gate** — sends `job_ids` without `only_approved`; backend applies unapproved jobs | `app.js` `applySelected()`, `apply.py` |
| **BUG-04** | P1 | **Hidden selections still used for Apply / Approve / resume-source** | `app.js` `selectedIds()`, `render()` |
| **BUG-05** | P1 | **Check-all partial state wrong** — some selected shows header unchecked (not indeterminate) | `app.js` `render()` |
| **BUG-06** | P1 | **Run status never returns to idle** after first action | `runner.py`, `pollStatus()` |
| **BUG-07** | P1 | **Toasts hidden behind schedule modal** (z-index 20 vs 100) | `styles.css` |
| **BUG-08** | P2 | **`loadStats()` errors swallowed** | `app.js` `loadStats()` |
| **BUG-09** | P2 | **`uninstallSchedule()` dead code** — never wired in UI | `app.js` |
| **BUG-10** | P2 | **Generate has no regenerate option** (CLI supports `--regenerate`) | `app.js`, `server.py` |
| **BUG-11** | P2 | **Bulk approve/reject/schedule not disabled during background runs** | `app.js` `pollStatus()` |
| **BUG-12** | P2 | **Seg toggle / approve can race** — no in-flight guard on mutations | `app.js` |
| **BUG-13** | P3 | **Stale UI server** — old process on :8765 returned 500 for `/api/jobs` and `/api/schedule` | ops / dev experience |

---

## UX bugs

### Mental model & copy

| ID | Issue |
|----|--------|
| **UX-01** | Pipeline looks like wizard navigation; steps 2–4 fire immediate actions |
| **UX-02** | Two different “Apply with” labels (filter bulk vs row column) |
| **UX-03** | Three resume touchpoints (master bar, intake, row) without onboarding |
| **UX-04** | “Reject selected” sounds like dismiss/skip; actually clears approval |
| **UX-05** | Developer jargon: `idle`, `find done`, `human_review`, raw action names in toasts |
| **UX-06** | README says “Apply approved”; UI says “Apply selected” |
| **UX-07** | Schedule “Search + apply” vs pipeline “Fetch jobs” — inconsistent terms |
| **UX-08** | No visibility of `SUBMIT_MODE` / `DRY_RUN` in UI |

### Feedback & loading

| ID | Issue |
|----|--------|
| **UX-09** | No loading state on initial grid load |
| **UX-10** | No pending state on approve/upload/seg/resume-source clicks |
| **UX-11** | Toasts auto-dismiss in 3.5s; new toast replaces previous |
| **UX-12** | Run progress truncates in pill; only full text in `title` tooltip |
| **UX-13** | Apply confirm doesn’t list titles, companies, or unapproved count |

### Empty & error states

| ID | Issue |
|----|--------|
| **UX-14** | Same empty copy for “no jobs in DB” and “filters too strict” |
| **UX-15** | API load failure looks like empty database |
| **UX-16** | Schedule modal shows raw errors with no Retry |
| **UX-17** | Stats bar empty then pops in (layout shift) |

### Selection & grid

| ID | Issue |
|----|--------|
| **UX-18** | No “N hidden by filters” or “Clear selection” |
| **UX-19** | Full table re-render on filter input loses checkbox focus |
| **UX-20** | Row selection only via tiny checkbox; no row click |
| **UX-21** | No sort direction / active column indicator |
| **UX-22** | Full LinkedIn URLs under every title |
| **UX-23** | Score colors (hi/mid/lo) with no legend |

### Intake & files

| ID | Issue |
|----|--------|
| **UX-24** | Intake panel open by default |
| **UX-25** | Intake read-only; edit path only via external YAML |
| **UX-26** | Cover letter view-only; no upload/replace in UI |
| **UX-27** | Upload tailored resume clears approval; easy to miss re-approve toast |

### Schedule modal

| ID | Issue |
|----|--------|
| **UX-28** | Save vs Install difference unclear |
| **UX-29** | “Enabled” vs “installed in OS” conflated |
| **UX-30** | “Approved only” hidden for “Search + apply” workflow |
| **UX-31** | Skip draft generation checked by default — surprises review-first users |
| **UX-32** | Saved schedule card shows paths/commands — intimidating |

### Safety & confirmations

| ID | Issue |
|----|--------|
| **UX-33** | Fetch jobs / Generate drafts — no confirmation (cost/time) |
| **UX-34** | Min score filter ≠ backend `MATCH_THRESHOLD` for apply eligibility |
| **UX-35** | `My master` / Master seg disabled without inline reason |

### Accessibility & mobile

| ID | Issue |
|----|--------|
| **UX-36** | Toasts not in `aria-live` region |
| **UX-37** | Schedule modal: no focus trap / initial focus |
| **UX-38** | Checkboxes lack accessible names |
| **UX-39** | Pipeline in `<nav>` mislabels action buttons as navigation |
| **UX-40** | Narrow viewport: filter wall + wide table |

---

## Task backlog

Status: `[ ]` todo · `[~]` in progress · `[x]` done

### P0 — Correctness & trust

- [x] **T-01** (BUG-01) Acknowledge run completion once — track `finished_at` client-side or add `POST /api/actions/ack` to clear consumed state
- [x] **T-02** (BUG-02, UX-15) Catch `loadJobs()` errors; show error empty state + toast
- [x] **T-03** (BUG-03, UX-13) Apply flow: warn/block unapproved selections OR add separate “Apply all approved” action
- [x] **T-04** (BUG-04, UX-18) Show `N selected (M hidden)` + “Clear selection” link

### P1 — Layout & arrangement

- [x] **T-05** (LAY-03, UX-24) Default `#intake-panel` to `hidden`; step 1 inactive on load
- [x] **T-06** (LAY-02, LAY-14) Move bulk Approve/Reject/Apply-with to selection toolbar above table (visible when selection > 0)
- [x] **T-07** (LAY-12) Remove permanent primary styling from step 4; use primary only when selection ready
- [x] **T-08** (LAY-04) Recent runs as slide-over or modal instead of inline expand
- [x] **T-09** (LAY-05) Move Reset all data to settings/danger zone (footer or confirm modal entry)
- [x] **T-10** (LAY-11) Move stats row directly above table header (below selection bar)
- [x] **T-11** (LAY-07, LAY-15) Group resume controls: master upload in intake drawer or single “Profile & resume” section
- [x] **T-12** (UX-02) Rename bulk control to “Set resume for selected:” vs column “Resume on apply”

### P1 — Interaction fixes

- [x] **T-13** (BUG-05) Set `check-all.indeterminate` when partial selection
- [x] **T-14** (BUG-06) Return status pill to idle after ack, or show “Last run: …” sublabel
- [x] **T-15** (BUG-07) Raise `.toast` z-index above modal (e.g. 200) + inline success in schedule panel
- [x] **T-16** (UX-09) Add grid loading skeleton/spinner on initial fetch
- [x] **T-17** (UX-10, BUG-12) Disable mutation buttons while API in flight
- [x] **T-18** (UX-14) Split empty states: `jobs.length === 0` vs `filtered().length === 0`

### P2 — Copy, safety & discoverability

- [x] **T-19** (UX-05, UX-06) Human labels for status badges; rename “Apply selected” → “Apply approved” or clarify in UI
- [x] **T-20** (UX-08) Header badges: `SUBMIT_MODE`, `DRY_RUN`, match threshold
- [x] **T-21** (UX-33) Confirm dialog for Fetch jobs and Generate drafts
- [x] **T-22** (UX-34) Tooltip on min score: “Display filter only; apply uses MATCH_THRESHOLD from config”
- [x] **T-23** (UX-35) Inline hint when master resume missing + disabled Master buttons
- [x] **T-24** (UX-01) Restyle pipeline: “Actions” strip vs single “Profile” toggle — remove step arrows or disable until prerequisite met
- [x] **T-25** (BUG-11) Disable approve/reject/schedule during `busy`
- [x] **T-26** (UX-16) Schedule load error: Retry button + friendlier copy
- [x] **T-27** (UX-28, UX-29) Schedule: helper text “Save writes config; Install registers OS task”
- [x] **T-28** (UX-30) Show “Approved only” for schedule-run when submit path applies jobs

### P2 — Grid & table polish

- [x] **T-29** (UX-21) Sort indicator on active column
- [x] **T-30** (UX-22) Title links to job URL; drop raw URL line or icon-only external link
- [x] **T-31** (UX-23) Score legend in stats bar or column header tooltip
- [x] **T-32** (UX-20) Click row (except links/buttons) to toggle selection
- [x] **T-33** (LAY-08, UX-40) `overflow-x: auto` on main + min-width on table wrapper
- [x] **T-34** (BUG-10) Optional “Regenerate drafts” on generate (checkbox or second button)

### P3 — Accessibility & nice-to-have

- [x] **T-35** (UX-36) `aria-live="polite"` on toast container
- [x] **T-36** (UX-37) Focus trap + focus close button on schedule modal open
- [x] **T-37** (UX-38) `aria-label` on check-all and row checkboxes
- [x] **T-38** (UX-39) Change pipeline `nav` to `div role="group"` with `aria-label="Pipeline actions"`
- [x] **T-39** (UX-26) Cover letter upload endpoint + UI parity with resume
- [x] **T-40** (BUG-09) Wire uninstall or remove dead code
- [x] **T-41** (LAY-10) Topbar chip when schedule installed: “Every 2h · active”
- [x] **T-42** (UX-27) Flash Approved column or inline banner after resume upload clears approval
- [x] **T-43** (UX-11) Longer toast for errors; stack or queue toasts
- [x] **T-44** (LAY-06) Reserve fixed width for Stop button slot to prevent layout shift
- [x] **T-45** (UX-03) First-run empty state: numbered “1 Upload master → 2 Fetch jobs → 3 Review → 4 Apply”
- [x] **T-46** (BUG-13) Document “restart UI after code changes” in README web UI section
- [x] **T-47** (UX-19) Debounce search filter re-render or preserve focus across render

---

## File reference map

| Area | Files |
|------|--------|
| Structure / DOM order | `agent/web/static/index.html` |
| Behavior / API wiring | `agent/web/static/app.js` |
| Layout / z-index / sticky | `agent/web/static/styles.css` |
| Endpoints / apply logic | `agent/web/server.py`, `agent/pipeline/apply.py` |
| Run state persistence | `agent/web/runner.py` |

---

## Verification checklist (when fixing)

```bash
# Start UI
linkedin-apply ui

# Manual
# - Empty DB: correct empty state, intake closed by default
# - Select + filter: hidden count visible, apply blocked or warned
# - Complete find run, refresh page: no duplicate toast
# - Open schedule modal, Save: success visible above modal
# - Partial row selection: header checkbox indeterminate
# - Narrow window: table scrolls horizontally

# Automated (existing)
pytest tests/test_web_serialize.py tests/test_schedule.py -q
```

---

## Changelog

| Date | Notes |
|------|--------|
| 2026-06-21 | Initial review: layout audit, 13 arrangement issues, 13 functional bugs, 40 UX issues, 47 tasks |
| 2026-06-21 | All 47 tasks (T-01–T-47) implemented: layout restructure, ack endpoint, apply gate, a11y, schedule/runs modals |
