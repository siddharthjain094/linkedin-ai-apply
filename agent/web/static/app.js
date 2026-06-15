"use strict";

const state = {
  jobs: [],
  selected: new Set(),
  sortKey: "match_score",
  sortDir: -1,
  pollTimer: null,
  masterResume: { available: false, name: "", url: "" },
  pendingJobUpload: null,
};

const $ = (id) => document.getElementById(id);
const api = async (url, opts) => {
  const res = await fetch(url, opts);
  let body = null;
  try { body = await res.json(); } catch (e) { /* no body */ }
  if (!res.ok) throw new Error((body && body.detail) || res.statusText);
  return body;
};

function toast(msg, isErr) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isErr ? " err" : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), 3500);
}

// ---- data ----------------------------------------------------------------

async function loadJobs() {
  const data = await api("/api/jobs");
  state.jobs = data.jobs;
  state.masterResume = data.master_resume || state.masterResume;
  updateMasterHint();
  // Drop selections for jobs that no longer exist.
  const ids = new Set(state.jobs.map((j) => j.job_id));
  state.selected = new Set([...state.selected].filter((id) => ids.has(id)));
  render();
  loadStats();
}

function updateMasterHint() {
  const el = $("master-hint");
  const m = state.masterResume;
  const mineBtn = $("btn-resume-mine");
  if (!m.available) {
    el.innerHTML = '<span class="dim">none yet</span>';
    if (mineBtn) mineBtn.disabled = true;
    return;
  }
  el.innerHTML = `<a href="${esc(m.url)}" target="_blank" title="View master resume">${esc(m.name)}</a>`;
  if (mineBtn) mineBtn.disabled = false;
}

async function loadStats() {
  try {
    const s = await api("/api/stats");
    const parts = Object.entries(s.by_status)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `<span class="pill">${k} <b>${v}</b></span>`);
    parts.push(`<span class="pill">approved <b>${s.approved}</b></span>`);
    parts.push(`<span class="pill">total <b>${state.jobs.length}</b></span>`);
    $("stats").innerHTML = parts.join("");
  } catch (e) { /* ignore */ }
}

async function loadRuns() {
  const panel = $("runs-panel");
  if (panel.hidden) return;
  const data = await api("/api/runs");
  if (!data.runs.length) {
    panel.innerHTML = '<div style="padding:10px;color:var(--muted)">No runs yet.</div>';
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
  panel.innerHTML = `<table><thead>${head}</thead><tbody>${rows}</tbody></table>`;
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
      panel.innerHTML = `<div class="intake-empty">No intake yet. Use <b>Replace master</b> in the bar above, add <code>profile/intake.yaml</code>, or run <code>linkedin-apply intake</code> in a terminal.</div>`;
      return;
    }
    const path = data.intake_path ? `<code>${esc(data.intake_path)}</code>` : "";
    const meta = path
      ? `<div class="intake-meta">Loaded from ${path}. Edit that file or re-upload resume, then click <b>Intake &amp; resume</b> to refresh.</div>`
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
  document.querySelectorAll(".pipeline-step").forEach((el) => {
    const on = stepId && el.id === stepId;
    el.classList.toggle("active", on);
    el.classList.remove("running");
  });
}

function setPipelineRunning(action) {
  const map = { find: "btn-find", generate: "btn-generate", apply: "btn-apply" };
  const runningId = map[action];
  document.querySelectorAll(".pipeline-step").forEach((el) => {
    el.classList.remove("running");
    if (runningId && el.id === runningId) el.classList.add("running");
  });
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
  const status = $("f-status").value;
  const minScore = Number($("f-score").value) || 0;
  const approvedOnly = $("f-approved").checked;
  const hasResume = $("f-hasresume").checked;

  let rows = state.jobs.filter((j) => {
    if (status && j.status !== status) return false;
    if ((j.match_score || 0) < minScore) return false;
    if (approvedOnly && !j.approved) return false;
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

function scoreClass(s) {
  if (s == null) return "lo";
  if (s >= 75) return "hi";
  if (s >= 50) return "mid";
  return "lo";
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

  return `<div class="doc-cell">
    <div class="doc-row">
      <span class="doc-label">Tailored resume</span>
      <span class="doc-actions">${tailoredView} ${tailoredBtn}</span>
    </div>
    <div class="doc-row">
      <span class="doc-label">Cover letter</span>
      <span class="doc-actions">${coverView}</span>
    </div>
  </div>`;
}

function rowHtml(j) {
  const checked = state.selected.has(j.job_id) ? "checked" : "";
  const score = j.match_score == null ? "&ndash;" : Math.round(j.match_score);

  return `<tr data-id="${j.job_id}">
    <td class="c-check"><input type="checkbox" class="rowcheck" ${checked} /></td>
    <td class="c-title">
      <div class="title">${esc(j.title) || "(untitled)"}</div>
      ${j.url ? `<a class="url" href="${esc(j.url)}" target="_blank">${esc(j.url)}</a>` : ""}
    </td>
    <td>${esc(j.company)}</td>
    <td>${esc(j.location)}</td>
    <td class="score ${scoreClass(j.match_score)}">${score}</td>
    <td>${esc(j.apply_type)}</td>
    <td><span class="badge ${esc(j.status)}">${esc(j.status)}</span></td>
    <td>${j.approved ? '<span class="yes">yes</span>' : '<span class="no">&mdash;</span>'}</td>
    <td class="c-upload">${uploadToggleHtml(j)}</td>
    <td class="c-files">${docsHtml(j)}</td>
  </tr>`;
}

function esc(s) {
  return (s == null ? "" : String(s)).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function render() {
  const rows = filtered();
  $("rows").innerHTML = rows.map(rowHtml).join("");
  $("empty").hidden = rows.length > 0;
  $("selcount").textContent = `${state.selected.size} selected`;
  const allChecked = rows.length > 0 && rows.every((j) => state.selected.has(j.job_id));
  $("check-all").checked = allChecked;
}

// ---- selection -----------------------------------------------------------

function selectedIds() { return [...state.selected]; }

// ---- actions -------------------------------------------------------------

async function approveSelected(approved) {
  const ids = selectedIds();
  if (!ids.length) return toast("Select some jobs first.", true);
  const r = await api("/api/jobs/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_ids: ids, approved }),
  });
  toast(`${approved ? "Approved" : "Rejected"} ${r.changed} job(s).`);
  await loadJobs();
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
}

async function replaceJobResume(jobId, file) {
  const data = await uploadFile(
    `/api/jobs/${encodeURIComponent(jobId)}/resume/upload`, file);
  toast(`Tailored resume saved as ${data.name}. Re-approve before applying.`);
  await loadJobs();
}

async function setResumeSource(useMaster, jobIds) {
  const ids = jobIds || selectedIds();
  if (!ids.length) return toast("Select some jobs first.", true);
  if (useMaster && !state.masterResume.available) {
    return toast("Add profile/master_resume.docx first.", true);
  }
  const r = await api("/api/jobs/resume-source", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_ids: ids, use_master: useMaster }),
  });
  toast(`Set ${r.changed} job(s) to apply with ${useMaster ? "your master resume" : "each job's tailored draft"}.`);
  await loadJobs();
}

async function applySelected() {
  const ids = selectedIds();
  if (!ids.length) return toast("Select one or more jobs in the grid first.", true);
  if (!confirm(`Submit applications to ${ids.length} selected job(s)?`)) return;
  await trigger("apply", { job_ids: ids });
}

async function trigger(name, body) {
  try {
    await api(`/api/actions/${name}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    toast(`Started: ${name}`);
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
  try {
    const r = await api("/api/reset", { method: "POST" });
    state.selected.clear();
    toast(`Reset complete (${r.jobs_deleted} jobs, ${r.runs_deleted} runs removed).`);
    await loadJobs();
    loadRuns();
  } catch (e) {
    toast(e.message, true);
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

async function pollStatus() {
  const s = await api("/api/actions/status");
  const el = $("run-status");
  const busy = s.running;
  ["btn-find", "btn-generate", "btn-apply", "btn-reset", "btn-apply-bar"].forEach((id) => {
    const el = $(id);
    if (el) el.disabled = busy;
  });

  if (busy) setPipelineRunning(s.action);
  else setPipelineActive($("intake-panel").hidden ? null : "btn-intake");

  const stopBtn = $("btn-stop");
  stopBtn.hidden = !busy;
  if (busy) {
    stopBtn.disabled = s.stop_requested;
    stopBtn.textContent = s.stop_requested ? "Stopping\u2026" : "Stop";
  }

  if (busy) {
    el.className = "run-status running";
    const detail = s.progress ? ` — ${s.progress}` : "";
    el.textContent = s.stop_requested
      ? `stopping: ${s.action}\u2026${detail}`
      : `running: ${s.action}\u2026${detail}`;
    el.title = s.progress || "";
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(pollStatus, 2000);
  } else if (s.error) {
    el.className = "run-status error";
    el.textContent = `${s.action} failed`;
    toast(`${s.action} failed: ${s.error}`, true);
    loadJobs();
    loadRuns();
  } else if (s.action) {
    el.className = "run-status done";
    el.textContent = `${s.action} done`;
    el.title = "";
    if (s.result) {
      const msg = s.result.message
        ? s.result.message
        : JSON.stringify(s.result);
      toast(`${s.action}: ${msg}`, !!s.result.message && s.result.applied === 0);
    }
    loadJobs();
    loadRuns();
  } else {
    el.className = "run-status idle";
    el.textContent = "idle";
  }
}

// ---- wiring --------------------------------------------------------------

function wire() {
  ["f-search", "f-status", "f-score", "f-approved", "f-hasresume"].forEach((id) =>
    $(id).addEventListener("input", render));

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
    const rep = e.target.closest("[data-replace]");
    if (rep) {
      e.preventDefault();
      state.pendingJobUpload = rep.dataset.replace;
      $("upload-job-resume").click();
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

  $("btn-approve").addEventListener("click", () => approveSelected(true));
  $("btn-reject").addEventListener("click", () => approveSelected(false));
  $("btn-resume-tailored").addEventListener("click", () => setResumeSource(false));
  $("btn-resume-mine").addEventListener("click", () => setResumeSource(true));
  $("btn-find").addEventListener("click", () => trigger("find"));
  $("btn-generate").addEventListener("click", () => trigger("generate"));
  $("btn-apply").addEventListener("click", () => applySelected().catch((e) => toast(e.message, true)));
  $("btn-apply-bar").addEventListener("click", () => applySelected().catch((e) => toast(e.message, true)));
  $("btn-stop").addEventListener("click", stopAction);
  $("btn-reset").addEventListener("click", () =>
    resetAllData().catch((e) => toast(e.message, true)));
  $("btn-intake").addEventListener("click", () => toggleIntakePanel());
  $("btn-runs").addEventListener("click", () => {
    const panel = $("runs-panel");
    panel.hidden = !panel.hidden;
    $("btn-runs").innerHTML = panel.hidden ? "Recent runs \u25BE" : "Recent runs \u25B4";
    if (!panel.hidden) loadRuns();
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
}

wire();
loadJobs().then(() => {
  loadIntake();
});
pollStatus();
