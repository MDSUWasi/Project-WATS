// Project WATS — shared UI components
// Uses design tokens from tokens.css; no CSS framework.

import { fmt, fmtSci, clamp, escapeHtml } from "/state.js";

// Render a completeness breakdown card with per-component bars.
export function renderCompleteness(container, data) {
  const comps = data.components || {};
  const score = Number(data.completeness_score || 0);
  const html = `
    <div class="card completeness-card">
      <div class="card-head"><h2>Completeness</h2><span class="score-badge">${(100 * score).toFixed(0)}%</span></div>
      <div class="completeness-grid">
        ${Object.entries(comps).map(([key, val]) => {
    const v = Number(val || 0);
    const label = key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, " ");
    return `
          <div class="comp-item">
            <div class="comp-label">${escapeHtml(label)}</div>
            <div class="comp-bar"><div class="comp-fill" style="width:${Math.round(100 * v)}%"></div></div>
            <div class="comp-val">${(100 * v).toFixed(0)}%</div>
          </div>
        `;
  }).join("")}
      </div>
      <div class="completeness-note" style="margin-top:8px;font-size:12px;color:var(--muted-2);">
        Weights: visits 0.40 · detectors 0.20 · bands 0.20 · pixel quality 0.10 · catalog 0.10
      </div>
    </div>
  `;
  container.insertAdjacentHTML("beforeend", html);
}

// Render a job progress card with cancel + stale-resume.
export function renderJobProgress(container, job) {
  const status = job.status || "unknown";
  const progress = Number(job.progress || 0);
  const total = Number(job.total || 0);
  const stage = job.stage || "";
  const message = job.message || "";
  const isStale = status === "stale";
  const isCancellable = status === "queued" || status === "running";
  const html = `
    <div class="card job-progress-card" data-job-id="${escapeHtml(String(job.id || ""))}">
      <div class="card-head"><h2>Job ${escapeHtml(String(job.type || ""))}</h2><span class="job-status status-${escapeHtml(String(status))}">${escapeHtml(String(status))}</span></div>
      <div class="job-stage" style="margin:8px 0;font-size:12px;color:var(--muted);">${escapeHtml(String(stage))}</div>
      <div class="job-progress">
        <div class="job-bar"><div class="job-fill" style="width:${Math.round((progress / Math.max(1, total)) * 100)}%"></div></div>
        <div class="job-num">${progress} / ${total}</div>
      </div>
      <div class="job-msg" style="margin-top:6px;font-size:12px;color:var(--muted-2);">${escapeHtml(String(message))}</div>
      <div class="job-actions" style="margin-top:10px;display:flex;gap:8px;">
        ${isCancellable ? `<button class="ghost btn-cancel-job" data-job-id="${escapeHtml(String(job.id || ""))}">Cancel</button>` : ""}
        ${isStale ? `<button class="primary btn-resume-job" data-job-id="${escapeHtml(String(job.id || ""))}">Resume</button>` : ""}
      </div>
      ${isStale ? `<div class="stale-hint" style="margin-top:6px;font-size:11px;color:var(--warn);">Job is stale — cached frames exist. Resume to continue.</div>` : ""}
    </div>
  `;
  container.insertAdjacentHTML("beforeend", html);
  return container;
}

// Attach job action listeners.
export function bindJobActions(container, onCancel, onResume) {
  container.onclick = (e) => {
    const cancelBtn = e.target.closest(".btn-cancel-job");
    if (cancelBtn) { onCancel(cancelBtn.dataset.jobId); return; }
    const resumeBtn = e.target.closest(".btn-resume-job");
    if (resumeBtn) { onResume(resumeBtn.dataset.jobId); return; }
  };
}

// Create a section header.
export function sectionHead(container, title, subtitle) {
  const html = `<div class="card-head"><h2>${escapeHtml(title)}</h2>${subtitle ? `<span class="muted">${escapeHtml(subtitle)}</span>` : ""}</div>`;
  container.insertAdjacentHTML("beforeend", html);
}

// Create a stat grid.
export function statGrid(container, stats) {
  const html = `<div class="stat-grid">${stats.map((s) => `
    <div class="stat-item"><div class="stat-val">${escapeHtml(String(s.value))}</div><div class="stat-label">${escapeHtml(String(s.label))}</div></div>
  `).join("")}</div>`;
  container.insertAdjacentHTML("beforeend", html);
}
