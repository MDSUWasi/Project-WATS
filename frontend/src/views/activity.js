// Project WATS — activity feed (public job history, no accounts)

import { fetchJson } from "/api.js";
import { escapeHtml } from "/state.js";

let abortCtrl = null;
let pollTimer = null;

export function render(container) {
  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Activity Feed</h2>
        <div class="coords-meta">Recent fetch jobs across all targets</div>
      </div>
      <div id="act-wrap"></div>
    </div>
  `;

  const wrap = container.querySelector("#act-wrap");

  async function load() {
    wrap.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Loading activity…</div>`;
    const res = await fetchJson("/api/public/activity?limit=100");
    if (!res.ok || !res.data) {
      wrap.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(res.data?.error))}</div>`;
      return;
    }
    const jobs = res.data.jobs || [];
    if (!jobs.length) {
      wrap.innerHTML = `<div class="muted">No recent jobs.</div>`;
      return;
    }
    wrap.innerHTML = `
      <table class="visit-table">
        <thead><tr><th>Job</th><th>Type</th><th>Status</th><th>Progress</th><th>Target</th><th>Created</th></tr></thead>
        <tbody>
          ${jobs.map((j) => `
            <tr>
              <td>#${j.id}</td>
              <td>${escapeHtml(String(j.type || ""))}</td>
              <td><span class="job-status status-${escapeHtml(String(j.status || ""))}">${escapeHtml(String(j.status || ""))}</span></td>
              <td>${j.progress !== undefined ? `${j.progress} / ${j.total || "?"}` : "—"}</td>
              <td>${escapeHtml(String(j.target_key || "—"))}</td>
              <td>${new Date(j.created || 0).toLocaleString()}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
  }

  load();
  // Refresh every 30s while visible
  pollTimer = setInterval(load, 30000);
}

export function destroy() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
}
