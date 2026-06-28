"use strict";

const state = {
  jobs: [],
  selected: new Set(),
  sortKey: "match_score",
  sortDir: -1,
  pollTimer: null,
  masterResume: { available: false, name: "", url: "" },
  pendingJobUpload: null,
  pendingCoverUpload: null,
  scheduleBackdropArmed: false,
  runsBackdropArmed: false,
  lastAckedFinishedAt: null,
  mutating: 0,
  busy: false,
  loadError: null,
  uiConfig: null,
  scheduleSummary: null,
  renderTimer: null,
  flashApproved: false,
  stats: null,
  statusFilter: "",
  approvedOnly: false,
  generateDialogArmed: false,
  scoreDialogArmed: false,
};

const STATUS_LABELS = {
  new: "New",
  generated: "Draft ready",
  applied: "Applied",
  human_review: "Needs review",
  skipped: "Skipped",
  error: "Error",
};

const ACTION_LABELS = {
  find: "Fetch jobs",
  score: "Score jobs",
  generate: "Generate drafts",
  apply: "Apply",
};

const $ = (id) => document.getElementById(id);
const api = async (url, opts) => {
  const res = await fetch(url, opts);
  let body = null;
  try { body = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) throw new Error((body && body.detail) || res.statusText);
  return body;
};

function statusLabel(s) {
  return STATUS_LABELS[s] || s;
}

function actionLabel(a) {
  return ACTION_LABELS[a] || a;
}

function setMutating(delta) {
  state.mutating = Math.max(0, state.mutating + delta);
  updateMutationButtons();
}

function updateMutationButtons() {
  const locked = state.busy || state.mutating > 0;
  [
    "btn-approve", "btn-reject", "btn-resume-tailored", "btn-resume-mine",
    "btn-rescore", "btn-schedule", "btn-clear-sel",
  ].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = locked;
  });
  document.querySelectorAll(".seg-btn, .doc-action").forEach((el) => {
    el.disabled = locked;
  });
}

const toastQueue = [];
let toastShowing = false;

function showNextToast() {
  if (toastShowing || !toastQueue.length) return;
  toastShowing = true;
  const { msg, isErr } = toastQueue.shift();
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  const duration = isErr ? 8000 : 3500;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    t.hidden = true;
    toastShowing = false;
    showNextToast();
  }, duration);
}

function toast(msg, isErr) {
  toastQueue.push({ msg, isErr: !!isErr });
  showNextToast();
}

function esc(s) {
  return (s == null ? "" : String(s)).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ---- data ----------------------------------------------------------------

async function loadConfig() {
  try {
    state.uiConfig = await api("/api/config");
    renderConfigBadges();
  } catch (e) { /* optional */ }
}

function renderConfigBadges() {
  const el = $("config-badges");
  if (!el || !state.uiConfig) return;
  const c = state.uiConfig;
  const parts = [
    `<span class="config-badge" title="SUBMIT_MODE from config">${esc(c.submit_mode)}</span>`,
  ];
  if (c.dry_run) parts.push('<span class="config-badge warn" title="DRY_RUN is on">DRY RUN</span>');
  if (c.auto_approve_on_score) {
    parts.push(`<span class="config-badge" title="Jobs scored at or above this are auto-approved">auto-approve ≥${c.match_threshold}</span>`);
  }
  el.innerHTML = parts.join("");
}

async function loadJobs() {
  const loading = $("grid-loading");
  if (loading) loading.hidden = false;
  state.loadError = null;
  try {
    const data = await api("/api/jobs");
    state.jobs = data.jobs;
    state.masterResume = data.master_resume || state.masterResume;
    updateMasterHint();
    const ids = new Set(state.jobs.map((j) => j.job_id));
    state.selected = new Set([...state.selected].filter((id) => ids.has(id)));
    render();
    await loadStats();
  } catch (e) {
    state.loadError = e.message;
    state.jobs = [];
    render();
    toast(`Could not load jobs: ${e.message}`, true);
  } finally {
    if (loading) loading.hidden = true;
  }
}

function updateMasterHint() {
  const el = $("master-hint");
  const m = state.masterResume;
  const mineBtn = $("btn-resume-mine");
  if (!m.available) {
    el.innerHTML = '<span class="dim">Upload a master resume to enable &ldquo;My master&rdquo;</span>';
    if (mineBtn) {
      mineBtn.disabled = true;
      mineBtn.title = "Add profile/master_resume first";
    }
    return;
  }
  el.innerHTML = `<a href="${esc(m.url)}" target="_blank" title="View master resume">${esc(m.name)}</a>`;
  if (mineBtn) {
    mineBtn.disabled = state.busy || state.mutating > 0;
    mineBtn.title = "Use your master resume when applying";
  }
}

function statPillHtml(kind, label, count, active) {
  const cls = active ? "pill active" : "pill";
  return `<button type="button" class="${cls}" data-stat-filter="${esc(kind)}"` +
    ` aria-pressed="${active ? "true" : "false"}">${esc(label)} <b>${count}</b></button>`;
}

function renderStatsBar() {
  const el = $("stats-pills");
  if (!el) return;
  const s = state.stats;
  if (!s) {
    el.innerHTML = '<span class="pill dim">Loading stats&hellip;</span>';
    return;
  }
  const parts = Object.entries(s.by_status)
    .sort((a, b) => b[1] - a[1])
    .filter(([, v]) => v > 0)
    .map(([k, v]) => statPillHtml(k, statusLabel(k), v, state.statusFilter === k));
  parts.push(statPillHtml("approved", "Approved", s.approved, state.approvedOnly));
  if (s.unscored > 0) {
    parts.push(`<span class="pill dim" title="Jobs without a match score yet">${s.unscored} unscored</span>`);
  }
  parts.push(statPillHtml("", "All jobs", state.jobs.length, !state.statusFilter && !state.approvedOnly));
  parts.push('<span class="pill dim" title="Green ≥75, yellow ≥50, gray below">score legend</span>');
  el.innerHTML = parts.join("");
}

async function loadStats() {
  try {
    state.stats = await api("/api/stats");
    renderStatsBar();
    updateApplyButton();
    updateScoreButton();
  } catch (e) {
    state.stats = null;
    const pills = $("stats-pills");
    if (pills) pills.innerHTML = '<span class="pill dim">Stats unavailable</span>';
  }
}

function applyStatusFilter(kind) {
  if (kind === "approved") {
    state.approvedOnly = !state.approvedOnly;
    if (state.approvedOnly) state.statusFilter = "";
  } else if (kind === "") {
    state.statusFilter = "";
    state.approvedOnly = false;
  } else {
    state.statusFilter = state.statusFilter === kind ? "" : kind;
    state.approvedOnly = false;
  }
  renderStatsBar();
  render();
}

async function loadRuns() {
  const panel = $("runs-panel");
  if (!panel) return;
  panel.innerHTML = '<p class="schedule-loading">Loading runs&hellip;</p>';
  try {
    const data = await api("/api/runs");
    if (!data.runs.length) {
      panel.innerHTML = '<div class="runs-empty">No runs yet.</div>';
      return;
    }
    const head = "<tr><th>When</th><th>Phase</th><th>Discovered</th><th>Applied</th>" +
      "<th>Review</th><th>Skipped</th><th>Errors</th><th>Notes</th></tr>";
    const rows = data.runs.map((r) => {
      const when = r.started_at ? new Date(r.started_at).toLocaleString() : "";
      const errCls = r.errors > 0 ? ' class="err"' : "";
      return `<tr><td>${esc(when)}</td><td>${esc(r.phase)}</td><td>${r.discovered}</td>` +
        `<td>${r.applied}</td><td>${r.review}</td><td>${r.skipped}</td>` +
        `<td${errCls}>${r.errors}</td><td>${esc(r.notes)}</td></tr>`;
    }).join("");
    panel.innerHTML = `<div class="runs-table-wrap"><table><thead>${head}</thead><tbody>${rows}</tbody></table></div>`;
  } catch (e) {
    panel.innerHTML = `<p class="schedule-loading err">Could not load runs: ${esc(e.message)}</p>
      <button id="btn-runs-retry" type="button" class="btn btn-sm">Retry</button>`;
    $("btn-runs-retry")?.addEventListener("click", () => loadRuns());
  }
}

async function loadScheduleChip() {
  try {
    const data = await api("/api/schedule");
    state.scheduleSummary = data;
    const chip = $("schedule-chip");
    if (!chip) return;
    const active = data.active_schedule || (data.schedules || []).find((s) => s.status === "active");
    if (active && data.enabled) {
      chip.hidden = false;
      chip.textContent = active.description || data.description || "Scheduled";
      chip.title = active.status_label || "Schedule active";
      chip.className = "schedule-chip active";
    } else if (data.enabled) {
      chip.hidden = false;
      chip.textContent = data.description || "Schedule saved";
      chip.title = "Schedule enabled but not installed";
      chip.className = "schedule-chip saved";
    } else {
      chip.hidden = true;
    }
  } catch (e) {
    $("schedule-chip").hidden = true;
  }
}

const SCHEDULE_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const SCHEDULE_INTERVALS = [1, 2, 4, 6, 12, 24];

function scheduleConfigHtml(data) {
  const days = data.days || [];
  const dayBoxes = SCHEDULE_DAYS.map((d) => {
    const checked = days.includes(d) ? "checked" : "";
    return `<label><input type="checkbox" class="sched-day" value="${d}" ${checked} /> ${d}</label>`;
  }).join("");
  const mode = data.mode || "interval";
  const isInterval = mode === "interval";
  const intervalOpts = (data.interval_options || SCHEDULE_INTERVALS).map((h) => {
    const sel = Number(data.interval_hours) === h ? "selected" : "";
    const label = h === 1 ? "Every 1 hour" : `Every ${h} hours`;
    return `<option value="${h}" ${sel}>${label}</option>`;
  }).join("");
  const installLabel = esc(data.install_label || "Install schedule");
  const installDisabled = data.supports_install ? "" : " disabled";
  const platHint = data.install_platform === "windows" ? "Windows Task Scheduler"
    : data.install_platform === "macos" ? "launchd" : "cron / manual";
  const osActive = !!(data.installed || data.loaded || (data.schedules || []).some(
    (s) => s.status === "active" || s.status === "installed"));
  const timingHint = isInterval
    ? "Install runs once immediately, then repeats on the interval."
    : "Install registers the task; first run is at the next scheduled time.";
  return `<p class="schedule-lead"><b>Save</b> stores settings only.
    <b>${installLabel}</b> turns on automation (${platHint}). ${timingHint}
    Requires <code>linkedin-apply login</code> once.</p>
    <div class="schedule-form">
      <div class="schedule-row">
        <label class="chk"><input id="sched-enabled" type="checkbox" ${data.enabled ? "checked" : ""} /> Enabled</label>
      </div>
      <div class="schedule-row">
        <label for="sched-mode">Repeat</label>
        <select id="sched-mode">
          <option value="interval" ${isInterval ? "selected" : ""}>Every N hours</option>
          <option value="daily" ${!isInterval ? "selected" : ""}>Once daily at a time</option>
        </select>
      </div>
      <div class="schedule-row" id="sched-interval-row"${isInterval ? "" : " hidden"}>
        <label for="sched-interval">Interval</label>
        <select id="sched-interval">${intervalOpts}</select>
      </div>
      <div id="sched-daily-block"${isInterval ? " hidden" : ""}>
        <div class="schedule-row">
          <label for="sched-time">Time</label>
          <input id="sched-time" type="time" value="${esc(data.time || "09:00")}" />
          <span class="dim">local time</span>
        </div>
        <div class="schedule-row">
          <label>Days</label>
          <div class="schedule-days">${dayBoxes}</div>
        </div>
      </div>
      <div class="schedule-row">
        <label for="sched-workflow">Workflow</label>
        <select id="sched-workflow">
          <option value="schedule-run" ${data.workflow === "schedule-run" || data.workflow === "daily" ? "selected" : ""}>Search + apply</option>
          <option value="find" ${data.workflow === "find" ? "selected" : ""}>Search only</option>
          <option value="apply" ${data.workflow === "apply" ? "selected" : ""}>Apply queued jobs</option>
        </select>
        <label class="chk schedule-only-approved" id="sched-only-wrap">
          <input id="sched-only-approved" type="checkbox" ${data.only_approved ? "checked" : ""} /> Approved only
        </label>
      </div>
      <div class="schedule-row">
        <label class="chk" title="When off, scheduled apply uses your master resume and skips LLM draft generation">
          <input id="sched-skip-generate" type="checkbox" ${data.skip_generate !== false ? "" : "checked"} />
          Generate tailored drafts before apply
        </label>
      </div>
      <div id="sched-inline-msg" class="schedule-status" hidden></div>
      <div class="schedule-actions">
        <button id="btn-sched-save" class="btn btn-sm" type="button">Save settings</button>
        <button id="btn-sched-install" class="btn btn-primary btn-sm" type="button"${installDisabled}>${osActive ? "Update schedule" : installLabel}</button>
        <button id="btn-sched-uninstall" class="btn btn-sm btn-warn" type="button"${osActive ? "" : " hidden"}>Remove schedule</button>
      </div>
    </div>`;
}

function scheduleBadgeClass(status) {
  if (status === "active") return "running";
  if (status === "installed") return "installed";
  if (status === "saved") return "saved";
  return "";
}

function scheduleCardHtml(entry) {
  const badgeCls = scheduleBadgeClass(entry.status);
  const cardCls = entry.status === "active" ? "schedule-card active-card" : "schedule-card";
  const days = (entry.days || []).join(", ");
  const repeat = entry.mode === "interval"
    ? `Every ${entry.interval_hours}h`
    : `Daily at ${entry.time} (${days})`;
  const deleteBtn = entry.can_delete
    ? `<button type="button" class="btn btn-sm btn-danger btn-sched-delete" data-id="${esc(entry.id)}">Delete</button>`
    : "";
  return `<article class="${cardCls}">
    <div class="schedule-card-head">
      <span class="schedule-badge ${badgeCls}">${esc(entry.status_label)}</span>
      ${deleteBtn}
    </div>
    <p class="schedule-card-title">${esc(entry.description)}</p>
    <dl class="schedule-kv">
      <dt>Workflow</dt><dd>${esc(entry.workflow_label || entry.workflow)}</dd>
      <dt>Repeat</dt><dd>${esc(repeat)}</dd>
    </dl>
  </article>`;
}

function scheduleSavedPanelHtml(data) {
  const schedules = data.schedules || [];
  const max = data.max_schedules || 1;
  let listHtml;
  if (!schedules.length) {
    listHtml = '<div class="schedule-empty">No saved schedule yet.<br/>Configure on the left and click Save.</div>';
  } else {
    listHtml = schedules.map(scheduleCardHtml).join("");
  }
  return `<h3>Saved schedule</h3>
    <p class="schedule-saved-hint">Only ${max} schedule can run at a time. Installing a new one replaces the active task.</p>
    <div id="schedule-list">${listHtml}</div>`;
}

function scheduleModalHtml(data) {
  return `<div class="schedule-layout">
    <div class="schedule-config-col">${scheduleConfigHtml(data)}</div>
    <div class="schedule-saved-col">${scheduleSavedPanelHtml(data)}</div>
  </div>`;
}

function readScheduleForm() {
  const mode = $("sched-mode").value;
  const body = {
    enabled: $("sched-enabled").checked,
    mode,
    interval_hours: Number($("sched-interval").value),
    workflow: $("sched-workflow").value,
    only_approved: $("sched-only-approved").checked,
    skip_generate: !$("sched-skip-generate").checked,
  };
  if (mode === "daily") {
    body.time = $("sched-time").value;
    body.days = [...document.querySelectorAll(".sched-day")]
      .filter((el) => el.checked)
      .map((el) => el.value);
  }
  return body;
}

function showScheduleInlineMsg(msg, ok) {
  const el = $("sched-inline-msg");
  if (!el) return;
  el.hidden = false;
  el.textContent = msg;
  el.className = "schedule-status" + (ok ? " ok" : " warn");
}

function wireSchedulePanel(data) {
  const wf = $("sched-workflow");
  const wrap = $("sched-only-wrap");
  const modeEl = $("sched-mode");
  const intervalRow = $("sched-interval-row");
  const dailyBlock = $("sched-daily-block");

  const syncApproved = () => {
    const w = wf.value;
    wrap.hidden = w === "find";
  };

  const syncMode = () => {
    const interval = modeEl.value === "interval";
    intervalRow.hidden = !interval;
    dailyBlock.hidden = interval;
  };
  modeEl.addEventListener("change", syncMode);
  syncMode();

  wf.addEventListener("change", syncApproved);
  syncApproved();

  $("btn-sched-save").addEventListener("click", () =>
    saveSchedule(false).catch((e) => toast(e.message, true)));
  $("btn-sched-install").addEventListener("click", () =>
    saveSchedule(true).catch((e) => toast(e.message, true)));
  $("btn-sched-uninstall")?.addEventListener("click", () =>
    uninstallSchedule().catch((e) => toast(e.message, true)));
  document.querySelectorAll(".btn-sched-delete").forEach((btn) => {
    btn.addEventListener("click", () =>
      deleteActiveSchedule().catch((e) => toast(e.message, true)));
  });
}

async function deleteActiveSchedule() {
  if (!confirm("Delete the active schedule? This removes the OS task and disables automation.")) return;
  setMutating(1);
  try {
    const data = await api("/api/schedule/active", { method: "DELETE" });
    toast(data.message || "Schedule deleted.");
    $("schedule-panel").innerHTML = scheduleModalHtml(data);
    wireSchedulePanel(data);
    loadScheduleChip();
  } finally {
    setMutating(-1);
  }
}

async function loadSchedule() {
  const panel = $("schedule-panel");
  if (!panel) return;
  const data = await api("/api/schedule");
  panel.innerHTML = scheduleModalHtml(data);
  wireSchedulePanel(data);
}

function trapFocus(modal) {
  const dialog = modal.querySelector(".modal-dialog");
  const focusable = dialog.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const handler = (e) => {
    if (e.key !== "Tab") return;
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };
  modal._focusTrap = handler;
  document.addEventListener("keydown", handler);
  (dialog.querySelector(".modal-close") || first)?.focus();
}

function releaseFocus(modal) {
  if (modal._focusTrap) {
    document.removeEventListener("keydown", modal._focusTrap);
    modal._focusTrap = null;
  }
}

function scheduleModalOpen() {
  const modal = $("schedule-modal");
  const panel = $("schedule-panel");
  if (!modal || !panel) return;

  modal.hidden = false;
  modal.classList.add("is-open");
  modal.setAttribute("aria-hidden", "false");
  $("btn-schedule")?.classList.add("active");
  panel.innerHTML = '<p class="schedule-loading">Loading schedule&hellip;</p>';
  trapFocus(modal);

  loadSchedule().catch((e) => {
    panel.innerHTML = `<p class="schedule-loading err">Could not load schedule: ${esc(e.message)}</p>
      <button id="btn-sched-retry" type="button" class="btn btn-sm">Retry</button>`;
    $("btn-sched-retry")?.addEventListener("click", () => {
      panel.innerHTML = '<p class="schedule-loading">Loading schedule&hellip;</p>';
      loadSchedule().catch((err) => {
        panel.innerHTML = `<p class="schedule-loading err">${esc(err.message)}</p>
          <button id="btn-sched-retry" type="button" class="btn btn-sm">Retry</button>`;
        $("btn-sched-retry")?.addEventListener("click", () => scheduleModalOpen());
      });
    });
    toast(e.message, true);
  });

  state.scheduleBackdropArmed = false;
  requestAnimationFrame(() => { state.scheduleBackdropArmed = true; });
}

function scheduleModalClose() {
  const modal = $("schedule-modal");
  if (!modal) return;
  releaseFocus(modal);
  modal.classList.remove("is-open");
  modal.hidden = true;
  modal.setAttribute("aria-hidden", "true");
  $("btn-schedule")?.classList.remove("active");
  state.scheduleBackdropArmed = false;
  $("btn-schedule")?.focus();
}

function toggleScheduleModal() {
  const modal = $("schedule-modal");
  if (!modal) return;
  if (modal.classList.contains("is-open")) scheduleModalClose();
  else scheduleModalOpen();
}

function runsModalOpen() {
  const modal = $("runs-modal");
  if (!modal) return;
  modal.hidden = false;
  modal.classList.add("is-open");
  modal.setAttribute("aria-hidden", "false");
  trapFocus(modal);
  loadRuns();
  state.runsBackdropArmed = false;
  requestAnimationFrame(() => { state.runsBackdropArmed = true; });
}

function runsModalClose() {
  const modal = $("runs-modal");
  if (!modal) return;
  releaseFocus(modal);
  modal.classList.remove("is-open");
  modal.hidden = true;
  modal.setAttribute("aria-hidden", "true");
  $("btn-runs")?.focus();
  state.runsBackdropArmed = false;
}

function scheduleBackdropClick(e) {
  if (!state.scheduleBackdropArmed) return;
  if (e.target.id === "schedule-modal-backdrop") scheduleModalClose();
}

function runsBackdropClick(e) {
  if (!state.runsBackdropArmed) return;
  if (e.target.id === "runs-modal-backdrop") runsModalClose();
}

async function saveSchedule(install) {
  const body = readScheduleForm();
  if (body.mode === "daily" && (!body.days || !body.days.length)) {
    return toast("Pick at least one weekday for daily mode.", true);
  }
  body.install = install;
  setMutating(1);
  try {
    const data = await api("/api/schedule", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const msg = install
      ? (data.install && data.install.message) || "Schedule saved and installed. First run starts now for interval schedules."
      : "Schedule saved.";
    showScheduleInlineMsg(msg, true);
    toast(msg);
    $("schedule-panel").innerHTML = scheduleModalHtml(data);
    wireSchedulePanel(data);
    loadScheduleChip();
  } finally {
    setMutating(-1);
  }
}

async function uninstallSchedule() {
  if (!confirm("Remove the schedule? This stops automation and disables it in config.")) return;
  setMutating(1);
  try {
    const data = await api("/api/schedule/active", { method: "DELETE" });
    toast(data.message || "Schedule removed.");
    $("schedule-panel").innerHTML = scheduleModalHtml(data);
    wireSchedulePanel(data);
    loadScheduleChip();
  } finally {
    setMutating(-1);
  }
}

function intakeSection(title, entries) {
  const items = Object.entries(entries || {});
  if (!items.length) {
    return `<div class="intake-section"><h3>${esc(title)}</h3><div class="intake-empty">(empty)</div></div>`;
  }
  const rows = items.map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${esc(v == null ? "" : v)}</dd>`).join("");
  return `<div class="intake-section"><h3>${esc(title)}</h3><dl class="intake-kv">${rows}</dl></div>`;
}

async function loadIntake() {
  const panel = $("intake-content");
  if (!panel) return;
  try {
    const data = await api("/api/profile");
    const intake = data.intake || {};
    if (!Object.keys(intake).length) {
      panel.innerHTML = `<div class="intake-empty">No intake yet. Use <b>Replace master</b> below, add <code>profile/intake.yaml</code>, or run <code>linkedin-apply intake</code> in a terminal.</div>`;
      return;
    }
    const path = data.intake_path ? `<code>${esc(data.intake_path)}</code>` : "";
    const meta = path
      ? `<div class="intake-meta">Loaded from ${path}. Edit that file or re-upload resume, then reopen <b>Profile &amp; resume</b> to refresh.</div>`
      : "";
    const sections = [
      intakeSection("Personal", intake.personal),
      intakeSection("Links", intake.links),
      intakeSection("Eligibility", intake.eligibility),
      intakeSection("Compensation", intake.compensation),
      intakeSection("Experience", intake.experience),
      intakeSection("Screening answers", intake.screening_answers),
      intakeSection("EEO / diversity", intake.eeo),
    ];
    if (intake.search) sections.push(intakeSection("Search overrides", intake.search));
    panel.innerHTML = meta + sections.join("");
  } catch (e) {
    panel.innerHTML = `<div class="intake-empty">Could not load intake: ${esc(e.message)}</div>`;
  }
}

function setPipelineActive(stepId) {
  document.querySelectorAll(".workflow-btn").forEach((el) => {
    const on = stepId && el.id === stepId;
    el.classList.toggle("active", on);
    el.classList.remove("running");
  });
}

function setPipelineRunning(action) {
  const map = { find: "btn-find", score: "btn-score", generate: "btn-generate", apply: "btn-apply" };
  const runningId = map[action];
  document.querySelectorAll(".workflow-btn").forEach((el) => {
    el.classList.remove("running");
    if (runningId && el.id === runningId) el.classList.add("running");
  });
}

function updateApplyButton() {
  const btn = $("btn-apply");
  if (!btn) return;
  const visible = visibleSelectedIds();
  const selectedApproved = visible.filter((id) => {
    const j = state.jobs.find((x) => x.job_id === id);
    return j && j.approved;
  }).length;
  const queue = state.stats?.approved || 0;

  let label = "Apply selected";
  if (selectedApproved > 0) label = `Apply selected (${selectedApproved})`;
  btn.textContent = label;
  btn.title = queue
    ? `${queue} approved in queue — select rows in the grid, then apply.`
    : "Select approved jobs in the grid first.";
  btn.classList.toggle("workflow-ready", selectedApproved > 0 && !state.busy);
}

function toggleIntakePanel(forceOpen) {
  const panel = $("intake-panel");
  const btn = $("btn-intake");
  const open = forceOpen === true ? true : forceOpen === false ? false : panel.hidden;
  panel.hidden = !open;
  btn.classList.toggle("active", open);
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) loadIntake();
}

// ---- filtering + rendering ------------------------------------------------

function filtered() {
  const q = $("f-search").value.trim().toLowerCase();
  const status = state.statusFilter;
  const minScore = Number($("f-score").value) || 0;
  const hasResume = $("f-hasresume").checked;

  let rows = state.jobs.filter((j) => {
    if (status && j.status !== status) return false;
    if ((j.match_score || 0) < minScore) return false;
    if (state.approvedOnly && !j.approved) return false;
    if (hasResume && !j.resume_exists) return false;
    if (q) {
      const hay = `${j.title} ${j.company} ${j.location}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const k = state.sortKey, dir = state.sortDir;
  rows.sort((a, b) => {
    let va = a[k], vb = b[k];
    if (k === "match_score") { va = va || 0; vb = vb || 0; }
    else { va = (va || "").toString().toLowerCase(); vb = (vb || "").toString().toLowerCase(); }
    if (va < vb) return -1 * dir;
    if (va > vb) return 1 * dir;
    return 0;
  });
  return rows;
}

function visibleSelectedIds() {
  const visible = new Set(filtered().map((j) => j.job_id));
  return [...state.selected].filter((id) => visible.has(id));
}

function hiddenSelectedCount() {
  const visible = new Set(filtered().map((j) => j.job_id));
  return [...state.selected].filter((id) => !visible.has(id)).length;
}

function scoreClass(s) {
  if (s == null) return "lo";
  if (s >= 75) return "hi";
  if (s >= 50) return "mid";
  return "lo";
}

function scoreTipText(j) {
  const reasons = (j.match_reasons || "").trim();
  if (reasons) return reasons;
  if (j.match_score == null) return "Not scored yet — use Score jobs or Fetch jobs.";
  return "No match details recorded.";
}

function scoreTipAttr(j) {
  return esc(scoreTipText(j)).replace(/\s+/g, " ").trim();
}

function scoreCellHtml(j) {
  const score = j.match_score == null ? null : Math.round(j.match_score);
  const display = score == null ? "\u2013" : String(score);
  const reasons = (j.match_reasons || "").trim();
  const isError = reasons.startsWith("scoring error:");
  const cls = ["score", scoreClass(j.match_score)];
  if (isError) cls.push("err");
  const tipAttr = scoreTipAttr(j);
  return `<td class="${cls.join(" ")}"><span class="score-val has-tip" tabindex="0" data-tip="${tipAttr}" aria-describedby="score-tooltip">${display}</span></td>`;
}

let scoreTooltipAnchor = null;

function hideScoreTooltip() {
  const tip = $("score-tooltip");
  if (tip) tip.hidden = true;
  scoreTooltipAnchor = null;
}

function positionScoreTooltip(anchor) {
  const tip = $("score-tooltip");
  if (!tip || !anchor) return;
  const margin = 10;
  const rect = anchor.getBoundingClientRect();
  const tipRect = tip.getBoundingClientRect();
  let left = rect.left + rect.width / 2 - tipRect.width / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - tipRect.width - margin));
  let top = rect.top - tipRect.height - margin;
  if (top < margin) top = rect.bottom + margin;
  top = Math.max(margin, Math.min(top, window.innerHeight - tipRect.height - margin));
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
}

function showScoreTooltip(anchor) {
  const tip = $("score-tooltip");
  if (!tip || !anchor?.dataset.tip) return;
  scoreTooltipAnchor = anchor;
  tip.textContent = anchor.dataset.tip;
  tip.hidden = false;
  tip.style.left = "-9999px";
  tip.style.top = "-9999px";
  requestAnimationFrame(() => positionScoreTooltip(anchor));
}

function wireScoreTooltips() {
  const rows = $("rows");
  if (!rows) return;

  rows.addEventListener("mouseover", (e) => {
    const val = e.target.closest(".score-val.has-tip");
    if (val) showScoreTooltip(val);
  });

  rows.addEventListener("mouseout", (e) => {
    const val = e.target.closest(".score-val.has-tip");
    if (val && !val.contains(e.relatedTarget)) hideScoreTooltip();
  });

  rows.addEventListener("focusin", (e) => {
    const val = e.target.closest(".score-val.has-tip");
    if (val) showScoreTooltip(val);
  });

  rows.addEventListener("focusout", (e) => {
    const val = e.target.closest(".score-val.has-tip");
    if (val && !val.contains(e.relatedTarget)) hideScoreTooltip();
  });

  const reposition = () => {
    if (scoreTooltipAnchor && !$("score-tooltip")?.hidden) {
      positionScoreTooltip(scoreTooltipAnchor);
    }
  };
  window.addEventListener("scroll", reposition, true);
  window.addEventListener("resize", reposition);
}

function sortIndicator(key) {
  if (state.sortKey !== key) return "";
  return state.sortDir > 0 ? " \u2191" : " \u2193";
}

function uploadToggleHtml(j) {
  const masterOk = state.masterResume.available;
  const mineOn = j.use_master_resume ? " on" : "";
  const tailoredOn = j.use_master_resume ? "" : " on";
  const dis = masterOk ? "" : " disabled";
  return `<div class="seg" data-id="${esc(j.job_id)}" title="Pick which file is uploaded when applying">
    <button type="button" class="seg-btn tailored${tailoredOn}" data-val="tailored">Tailored</button>
    <button type="button" class="seg-btn master${mineOn}" data-val="master"${dis} title="Uses master resume from profile/">Master</button>
  </div>`;
}

function docsHtml(j) {
  const jid = encodeURIComponent(j.job_id);
  const tailoredView = j.resume_exists
    ? `<a class="doclink" href="/api/jobs/${jid}/resume" target="_blank">view</a>`
    : `<span class="dim">none</span>`;
  const tailoredAction = j.resume_exists ? "Replace" : "Upload";
  const tailoredBtn = `<button type="button" class="doc-action" data-replace="${esc(j.job_id)}" title="Upload a .docx or .pdf tailored resume for this job only">${tailoredAction}&hellip;</button>`;

  const coverView = j.cover_exists
    ? `<a class="doclink" href="/api/jobs/${jid}/cover" target="_blank">view</a>`
    : `<span class="dim">none</span>`;
  const coverAction = j.cover_exists ? "Replace" : "Upload";
  const coverBtn = `<button type="button" class="doc-action" data-cover="${esc(j.job_id)}" title="Upload a cover letter for this job">${coverAction}&hellip;</button>`;

  return `<div class="doc-cell">
    <div class="doc-row">
      <span class="doc-label">Tailored resume</span>
      <span class="doc-actions">${tailoredView} ${tailoredBtn}</span>
    </div>
    <div class="doc-row">
      <span class="doc-label">Cover letter</span>
      <span class="doc-actions">${coverView} ${coverBtn}</span>
    </div>
  </div>`;
}

function rowHtml(j) {
  const checked = state.selected.has(j.job_id) ? "checked" : "";
  const titleLink = j.url
    ? `<a class="title-link" href="${esc(j.url)}" target="_blank" rel="noopener">${esc(j.title) || "(untitled)"} <span class="ext-icon" aria-hidden="true">&#8599;</span></a>`
    : `<div class="title">${esc(j.title) || "(untitled)"}</div>`;
  const ariaLabel = `Select ${esc(j.title || "job")} at ${esc(j.company)}`;

  return `<tr data-id="${j.job_id}" class="${state.selected.has(j.job_id) ? "selected" : ""}">
    <td class="c-check"><input type="checkbox" class="rowcheck" ${checked} aria-label="${ariaLabel}" /></td>
    <td class="c-title">${titleLink}</td>
    <td>${esc(j.company)}</td>
    <td>${esc(j.location)}</td>
    ${scoreCellHtml(j)}
    <td>${esc(j.apply_type)}</td>
    <td><span class="badge ${esc(j.status)}">${statusLabel(j.status)}</span></td>
    <td class="c-approved">${j.approved ? '<span class="yes">yes</span>' : '<span class="no">&mdash;</span>'}</td>
    <td class="c-upload">${uploadToggleHtml(j)}</td>
    <td class="c-files">${docsHtml(j)}</td>
  </tr>`;
}

function renderEmptyStates(rows) {
  const empty = $("empty");
  const onboarding = $("empty-onboarding");
  empty.hidden = true;
  onboarding.hidden = true;

  if (state.loadError) {
    empty.hidden = false;
    empty.innerHTML = `Could not load jobs: <b>${esc(state.loadError)}</b>. Check that the UI server is running current code and try refreshing.`;
    return;
  }
  if (state.jobs.length === 0) {
    onboarding.hidden = false;
    onboarding.innerHTML = `<strong>No jobs yet.</strong> Get started:<br/>
      <span class="step">1.</span> Upload master resume (Profile &amp; resume)<br/>
      <span class="step">2.</span> Fetch jobs<br/>
      <span class="step">3.</span> Review &amp; approve drafts<br/>
      <span class="step">4.</span> Select approved rows and apply`;
    return;
  }
  if (rows.length === 0) {
    empty.hidden = false;
    const filterBits = [];
    if (state.statusFilter) filterBits.push(statusLabel(state.statusFilter));
    if (state.approvedOnly) filterBits.push("approved only");
    if ($("f-hasresume")?.checked) filterBits.push("has resume");
    if ($("f-search")?.value.trim()) filterBits.push("search");
    if (Number($("f-score")?.value) > 0) filterBits.push("min score");
    const hint = filterBits.length
      ? `Active filters: ${filterBits.join(", ")}.`
      : "Try relaxing search or score filters.";
    empty.textContent = `No jobs match the current filters. ${hint}`;
  }
}

function render() {
  const rows = filtered();
  const searchEl = $("f-search");
  const hadFocus = document.activeElement === searchEl;
  const selStart = hadFocus ? searchEl.selectionStart : null;
  const selEnd = hadFocus ? searchEl.selectionEnd : null;

  $("rows").innerHTML = rows.map(rowHtml).join("");
  renderEmptyStates(rows);
  hideScoreTooltip();

  const visibleSel = visibleSelectedIds();
  const hidden = hiddenSelectedCount();
  const selBar = $("selection-bar");
  if (selBar) selBar.hidden = state.selected.size === 0;
  let selText = `${visibleSel.length} selected`;
  if (hidden > 0) selText += ` (${hidden} hidden by filters)`;
  $("selcount").textContent = selText;

  const allChecked = rows.length > 0 && rows.every((j) => state.selected.has(j.job_id));
  const someChecked = rows.some((j) => state.selected.has(j.job_id));
  const checkAll = $("check-all");
  checkAll.checked = allChecked;
  checkAll.indeterminate = someChecked && !allChecked;

  document.querySelectorAll("th.sortable").forEach((th) => {
    const base = th.dataset.label || th.textContent.replace(/ [\u2191\u2193]$/, "").trim();
    th.textContent = base + sortIndicator(th.dataset.sort);
  });

  if (state.flashApproved) {
    $("col-approved")?.classList.add("flash");
    setTimeout(() => {
      $("col-approved")?.classList.remove("flash");
      state.flashApproved = false;
    }, 2500);
  }

  updateApplyButton();
  updateScoreButton();
  updateMutationButtons();

  if (hadFocus && searchEl) {
    searchEl.focus();
    if (selStart != null) searchEl.setSelectionRange(selStart, selEnd);
  }
}

function debouncedRender() {
  clearTimeout(state.renderTimer);
  state.renderTimer = setTimeout(render, 150);
}

function clearSelection() {
  state.selected.clear();
  render();
}

// ---- selection -----------------------------------------------------------

function selectedIds() {
  return visibleSelectedIds();
}

// ---- actions -------------------------------------------------------------

async function approveSelected(approved) {
  const ids = selectedIds();
  if (!ids.length) return toast("Select some visible jobs first.", true);
  setMutating(1);
  try {
    const r = await api("/api/jobs/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: ids, approved }),
    });
    toast(`${approved ? "Approved" : "Cleared approval on"} ${r.changed} job(s).`);
    await loadJobs();
  } finally {
    setMutating(-1);
  }
}

async function uploadFile(url, file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(url, { method: "POST", body: fd });
  let body = null;
  try { body = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) throw new Error((body && body.detail) || res.statusText);
  return body;
}

async function replaceMasterResume(file) {
  setMutating(1);
  try {
    const data = await uploadFile("/api/master-resume/upload", file);
    if (data.master_resume) state.masterResume = data.master_resume;
    updateMasterHint();
    const intakeMsg = data.intake && data.intake.ok
      ? ` Intake updated (${data.intake.fields_merged} fields from resume).`
      : "";
    toast(`Replaced master resume with ${data.name}.${intakeMsg}`);
    await loadJobs();
    loadIntake();
    toggleIntakePanel(true);
  } finally {
    setMutating(-1);
  }
}

async function replaceJobResume(jobId, file) {
  setMutating(1);
  try {
    const data = await uploadFile(
      `/api/jobs/${encodeURIComponent(jobId)}/resume/upload`, file);
    state.flashApproved = true;
    toast(`Tailored resume saved as ${data.name}. Approval cleared — re-approve before applying.`);
    await loadJobs();
  } finally {
    setMutating(-1);
  }
}

async function replaceJobCover(jobId, file) {
  setMutating(1);
  try {
    const data = await uploadFile(
      `/api/jobs/${encodeURIComponent(jobId)}/cover/upload`, file);
    toast(`Cover letter saved as ${data.name}.`);
    await loadJobs();
  } finally {
    setMutating(-1);
  }
}

async function setResumeSource(useMaster, jobIds) {
  const ids = jobIds || selectedIds();
  if (!ids.length) return toast("Select some visible jobs first.", true);
  if (useMaster && !state.masterResume.available) {
    return toast("Add profile/master_resume first.", true);
  }
  setMutating(1);
  try {
    const r = await api("/api/jobs/resume-source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_ids: ids, use_master: useMaster }),
    });
    toast(`Set ${r.changed} job(s) to apply with ${useMaster ? "your master resume" : "each job's tailored draft"}.`);
    await loadJobs();
  } finally {
    setMutating(-1);
  }
}

async function applySelected() {
  const ids = visibleSelectedIds();
  if (!ids.length) return toast("Select one or more visible jobs in the grid first.", true);

  const jobs = ids.map((id) => state.jobs.find((j) => j.job_id === id)).filter(Boolean);
  const unapproved = jobs.filter((j) => !j.approved);
  if (unapproved.length) {
    return toast(`${unapproved.length} selected job(s) are not approved. Approve them first.`, true);
  }

  const preview = jobs.slice(0, 5).map((j) => `\u2022 ${j.title} @ ${j.company}`).join("\n");
  const more = jobs.length > 5 ? `\n\u2026and ${jobs.length - 5} more` : "";
  if (!confirm(`Submit ${jobs.length} approved job(s)?\n\n${preview}${more}`)) return;
  await trigger("apply", { job_ids: ids });
}

function updateScoreButton() {
  const btn = $("btn-score");
  if (!btn) return;
  const unscored = state.stats?.unscored ?? 0;
  btn.textContent = unscored > 0 ? `Score jobs (${unscored})` : "Score jobs";
  btn.title = unscored > 0
    ? `${unscored} job(s) not scored yet — match against your master resume (no LinkedIn fetch)`
    : "Score unscored jobs against your master resume (no LinkedIn fetch)";
}

async function rescoreSelected() {
  const ids = selectedIds();
  if (!ids.length) return toast("Select some visible jobs first.", true);
  const msg = `Re-score ${ids.length} selected job(s)?\n\nThis clears their current scores and calls the LLM again.`;
  if (!confirm(msg)) return;
  await trigger("score", { job_ids: ids, rescore: true });
}

function scoreDialogOpen() {
  const dialog = $("score-dialog");
  if (!dialog || state.busy) return;
  const rescore = $("score-rescore");
  if (rescore) rescore.checked = false;
  dialog.hidden = false;
  dialog.classList.add("is-open");
  dialog.setAttribute("aria-hidden", "false");
  state.scoreDialogArmed = false;
  requestAnimationFrame(() => { state.scoreDialogArmed = true; });
  $("btn-score-confirm")?.focus();
}

function scoreDialogClose() {
  const dialog = $("score-dialog");
  if (!dialog) return;
  dialog.classList.remove("is-open");
  dialog.hidden = true;
  dialog.setAttribute("aria-hidden", "true");
  state.scoreDialogArmed = false;
  $("btn-score")?.focus();
}

async function triggerScore() {
  scoreDialogOpen();
}

async function confirmScore() {
  const rescore = !!$("score-rescore")?.checked;
  scoreDialogClose();
  await trigger("score", { rescore });
}

async function triggerFind() {
  if (!confirm("Fetch jobs from LinkedIn? This opens the browser and may take several minutes.")) return;
  await trigger("find");
}

function generateDialogOpen() {
  const dialog = $("generate-dialog");
  if (!dialog || state.busy) return;
  const regen = $("gen-regen");
  if (regen) regen.checked = false;
  dialog.hidden = false;
  dialog.classList.add("is-open");
  dialog.setAttribute("aria-hidden", "false");
  state.generateDialogArmed = false;
  requestAnimationFrame(() => { state.generateDialogArmed = true; });
  $("btn-gen-confirm")?.focus();
}

function generateDialogClose() {
  const dialog = $("generate-dialog");
  if (!dialog) return;
  dialog.classList.remove("is-open");
  dialog.hidden = true;
  dialog.setAttribute("aria-hidden", "true");
  state.generateDialogArmed = false;
  $("btn-generate")?.focus();
}

async function triggerGenerate() {
  generateDialogOpen();
}

async function confirmGenerate() {
  const regen = !!$("gen-regen")?.checked;
  generateDialogClose();
  await trigger("generate", { regenerate: regen });
}

async function trigger(name, body) {
  try {
    await api(`/api/actions/${name}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    toast(`Started: ${actionLabel(name)}`);
    pollStatus();
  } catch (e) {
    toast(e.message, true);
  }
}

async function resetAllData() {
  const n = state.jobs.length;
  const msg = n
    ? `Delete all ${n} job(s) and run history from the database?\n\nGenerated resumes in output/ are kept. You can fetch jobs again from scratch.`
    : "Delete all run history from the database and start fresh?";
  if (!confirm(msg)) return;
  setMutating(1);
  try {
    const r = await api("/api/reset", { method: "POST" });
    state.selected.clear();
    toast(`Reset complete (${r.jobs_deleted} jobs, ${r.runs_deleted} runs removed).`);
    await loadJobs();
  } finally {
    setMutating(-1);
  }
}

async function stopAction() {
  try {
    await api("/api/actions/stop", { method: "POST" });
    toast("Stopping after the current step\u2026");
    const btn = $("btn-stop");
    btn.disabled = true;
    btn.textContent = "Stopping\u2026";
    pollStatus();
  } catch (e) {
    toast(e.message, true);
  }
}

async function ackRunStatus() {
  try {
    await api("/api/actions/ack", { method: "POST" });
  } catch (e) { /* ignore */ }
  state.lastAckedFinishedAt = null;
}

async function pollStatus() {
  let s;
  try {
    s = await api("/api/actions/status");
  } catch (e) {
    return;
  }
  const el = $("run-status");
  const busy = s.running;
  state.busy = busy;
  ["btn-find", "btn-score", "btn-generate", "btn-apply", "btn-reset"].forEach((id) => {
    const node = $(id);
    if (node) node.disabled = busy;
  });
  updateMutationButtons();

  if (busy) setPipelineRunning(s.action);
  else if (!$("intake-panel").hidden) setPipelineActive("btn-intake");

  const stopBtn = $("btn-stop");
  stopBtn.hidden = !busy;
  if (busy) {
    stopBtn.disabled = s.stop_requested;
    stopBtn.textContent = s.stop_requested ? "Stopping\u2026" : "Stop";
  }

  if (busy) {
    el.className = "run-status running";
    const detail = s.progress ? ` \u2014 ${s.progress}` : "";
    el.textContent = `${actionLabel(s.action)} running${detail}`;
    el.title = s.progress || "";
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(pollStatus, 2000);
    return;
  }

  const finishedAt = s.finished_at;
  const alreadyAcked = finishedAt && finishedAt === state.lastAckedFinishedAt;

  if (s.error && !alreadyAcked) {
    el.className = "run-status error";
    el.textContent = `${actionLabel(s.action)} failed`;
    toast(`${actionLabel(s.action)} failed: ${s.error}`, true);
    state.lastAckedFinishedAt = finishedAt;
    await ackRunStatus();
    loadJobs();
    loadRuns();
  } else if (s.action && s.result && !alreadyAcked) {
    el.className = "run-status done";
    el.textContent = `${actionLabel(s.action)} finished`;
    el.title = "";
    const msg = s.result.message
      ? s.result.message
      : JSON.stringify(s.result);
    toast(`${actionLabel(s.action)}: ${msg}`, !!s.result.message && s.result.applied === 0);
    state.lastAckedFinishedAt = finishedAt;
    await ackRunStatus();
    loadJobs();
    loadRuns();
    loadScheduleChip();
  } else {
    el.className = "run-status idle";
    el.textContent = "Ready";
    el.title = "";
  }
  updateApplyButton();
}

// ---- wiring --------------------------------------------------------------

function wire() {
  $("f-search").addEventListener("input", debouncedRender);
  ["f-score", "f-hasresume"].forEach((id) =>
    $(id).addEventListener("input", () => {
      renderStatsBar();
      render();
    }));

  $("stats-pills")?.addEventListener("click", (e) => {
    const pill = e.target.closest("[data-stat-filter]");
    if (!pill) return;
    applyStatusFilter(pill.dataset.statFilter);
  });

  document.querySelectorAll("th.sortable").forEach((th) =>
    th.addEventListener("click", () => {
      const k = th.dataset.sort;
      if (state.sortKey === k) state.sortDir *= -1;
      else { state.sortKey = k; state.sortDir = k === "match_score" ? -1 : 1; }
      render();
    }));

  $("rows").addEventListener("change", (e) => {
    if (!e.target.classList.contains("rowcheck")) return;
    const id = e.target.closest("tr").dataset.id;
    if (e.target.checked) state.selected.add(id);
    else state.selected.delete(id);
    render();
  });

  $("rows").addEventListener("click", (e) => {
    if (e.target.closest("a, button, input, .seg, .score-val")) return;
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;
    const id = tr.dataset.id;
    if (state.selected.has(id)) state.selected.delete(id);
    else state.selected.add(id);
    render();
  });

  $("rows").addEventListener("click", (e) => {
    const rep = e.target.closest("[data-replace]");
    if (rep) {
      e.preventDefault();
      state.pendingJobUpload = rep.dataset.replace;
      $("upload-job-resume").click();
      return;
    }
    const cov = e.target.closest("[data-cover]");
    if (cov) {
      e.preventDefault();
      state.pendingCoverUpload = cov.dataset.cover;
      $("upload-job-cover").click();
      return;
    }
    const btn = e.target.closest(".seg-btn");
    if (!btn || btn.disabled) return;
    e.preventDefault();
    const seg = btn.closest(".seg");
    const jobId = seg && seg.dataset.id;
    if (!jobId) return;
    const useMaster = btn.dataset.val === "master";
    const job = state.jobs.find((j) => j.job_id === jobId);
    if (job && !!job.use_master_resume === useMaster) return;
    setResumeSource(useMaster, [jobId]);
  });

  $("check-all").addEventListener("change", (e) => {
    const rows = filtered();
    if (e.target.checked) rows.forEach((j) => state.selected.add(j.job_id));
    else rows.forEach((j) => state.selected.delete(j.job_id));
    render();
  });

  $("btn-clear-sel")?.addEventListener("click", clearSelection);
  $("btn-approve").addEventListener("click", () => approveSelected(true));
  $("btn-reject").addEventListener("click", () => approveSelected(false));
  $("btn-resume-tailored").addEventListener("click", () => setResumeSource(false));
  $("btn-resume-mine").addEventListener("click", () => setResumeSource(true));
  $("btn-find").addEventListener("click", () => triggerFind().catch((e) => toast(e.message, true)));
  $("btn-score")?.addEventListener("click", () => triggerScore());
  $("btn-score-cancel")?.addEventListener("click", scoreDialogClose);
  $("btn-score-confirm")?.addEventListener("click", () =>
    confirmScore().catch((e) => toast(e.message, true)));
  $("score-dialog-backdrop")?.addEventListener("click", (e) => {
    if (!state.scoreDialogArmed) return;
    if (e.target.id === "score-dialog-backdrop") scoreDialogClose();
  });
  $("btn-rescore")?.addEventListener("click", () =>
    rescoreSelected().catch((e) => toast(e.message, true)));
  $("btn-generate").addEventListener("click", () => triggerGenerate());
  $("btn-gen-cancel")?.addEventListener("click", generateDialogClose);
  $("btn-gen-confirm")?.addEventListener("click", () =>
    confirmGenerate().catch((e) => toast(e.message, true)));
  $("generate-dialog-backdrop")?.addEventListener("click", (e) => {
    if (!state.generateDialogArmed) return;
    if (e.target.id === "generate-dialog-backdrop") generateDialogClose();
  });
  $("btn-apply").addEventListener("click", () => applySelected().catch((e) => toast(e.message, true)));
  $("btn-stop").addEventListener("click", stopAction);
  $("btn-reset").addEventListener("click", () =>
    resetAllData().catch((e) => toast(e.message, true)));
  $("btn-intake").addEventListener("click", () => toggleIntakePanel());
  $("btn-schedule")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleScheduleModal();
  });
  $("btn-schedule-close")?.addEventListener("click", () => scheduleModalClose());
  $("schedule-modal-backdrop")?.addEventListener("click", scheduleBackdropClick);
  $("schedule-modal")?.querySelector(".modal-dialog")?.addEventListener("click", (e) => {
    e.stopPropagation();
  });
  $("btn-runs")?.addEventListener("click", () => runsModalOpen());
  $("btn-runs-close")?.addEventListener("click", () => runsModalClose());
  $("runs-modal-backdrop")?.addEventListener("click", runsBackdropClick);
  $("runs-modal")?.querySelector(".modal-dialog")?.addEventListener("click", (e) => {
    e.stopPropagation();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if ($("generate-dialog")?.classList.contains("is-open")) generateDialogClose();
      else if ($("schedule-modal")?.classList.contains("is-open")) scheduleModalClose();
      else if ($("score-dialog")?.classList.contains("is-open")) scoreDialogClose();
      else if ($("runs-modal")?.classList.contains("is-open")) runsModalClose();
    }
  });

  $("btn-replace-master").addEventListener("click", () => $("upload-master").click());
  $("upload-master").addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!file) return;
    replaceMasterResume(file).catch((err) => toast(err.message, true));
  });

  $("upload-job-resume").addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    const jobId = state.pendingJobUpload;
    e.target.value = "";
    state.pendingJobUpload = null;
    if (!file || !jobId) return;
    replaceJobResume(jobId, file).catch((err) => toast(err.message, true));
  });

  $("upload-job-cover").addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    const jobId = state.pendingCoverUpload;
    e.target.value = "";
    state.pendingCoverUpload = null;
    if (!file || !jobId) return;
    replaceJobCover(jobId, file).catch((err) => toast(err.message, true));
  });

  wireScoreTooltips();
}

wire();
toggleIntakePanel(false);
renderStatsBar();
loadConfig();
loadScheduleChip();
const gridLoading = $("grid-loading");
if (gridLoading) gridLoading.hidden = false;
loadJobs().catch((e) => toast(e.message, true));
pollStatus();
