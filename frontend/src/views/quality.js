// Project WATS — quality view
// FLAGS/VARIANCE/ZODI, bad-pixel fraction, coverage gaps, failed-detector report.

import { fetchJson } from "/api.js";
import { fmt, escapeHtml } from "/state.js";

let abortCtrl = null;

export function render(container) {
  const q = new URLSearchParams(location.search);
  const ra = q.get("ra"); const dec = q.get("dec");
  const fov = q.get("fov") || "0.15";
  const maxFrames = q.get("max_frames") || "6";
  const name = q.get("name") || "Custom field";

  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Quality: ${escapeHtml(name)}</h2>
        <div class="coords-meta">${escapeHtml(ra ? `RA ${Number(ra).toFixed(5)}° · Dec ${Number(dec).toFixed(5)}°` : "—")}</div>
      </div>
      <div class="qual-toolbar" style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
        <button class="primary" id="qual-load-btn">Load quality</button>
        <span id="qual-info" class="muted" style="margin-left:auto;font-size:12px;"></span>
      </div>
      <div id="qual-wrap"></div>
    </div>
  `;

  const loadBtn = container.querySelector("#qual-load-btn");
  const infoEl = container.querySelector("#qual-info");
  const wrap = container.querySelector("#qual-wrap");

  loadBtn.addEventListener("click", () => {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    loadQuality({ ra, dec, fov, max_frames: maxFrames, name }, { signal: abortCtrl.signal }, { wrap, infoEl });
  });

  if (ra && dec) loadBtn.click();
}

async function loadQuality(params, opts = {}, refs = {}) {
  const wrap = refs.wrap || document.querySelector("#qual-wrap");
  const infoEl = refs.infoEl || document.querySelector("#qual-info");
  wrap.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Loading quality data…</div>`;
  try {
    const key = new URLSearchParams(location.search).get("key");
    if (!key) throw new Error("Load a target in the Sky view first.");
    const res = await fetchJson(`/api/targets/${encodeURIComponent(key)}/quality`, opts.signal);
    if (!res.ok || !res.data) throw new Error(res.data?.error || "Quality data unavailable");
    const data = res.data;
    const frames = data.frames || [];
    infoEl.textContent = `${data.n_frames || frames.length} frames · bad-pixel fraction ${(100 * (data.flags_bad_fraction || 0)).toFixed(1)}%`;
    if (!frames.length) {
      wrap.innerHTML = `<div class="panel muted">No frames available.</div>`;
      return;
    }
    wrap.innerHTML = `
      <table class="visit-table">
        <thead><tr><th>Frame</th><th>MJD</th><th>Bad flags</th><th>Detectors</th><th>Channels</th></tr></thead>
        <tbody>${frames.map((f) => `
          <tr>
            <td>${f.index ?? "—"}</td>
            <td>${Number(f.mjd || 0).toFixed(3)}</td>
            <td>${(100 * Number(f.flags_bad_fraction || 0)).toFixed(1)}%</td>
            <td>${f.n_detectors ?? "—"}</td>
            <td>${f.n_channels ?? "—"}</td>
          </tr>`).join("")}
        </tbody>
      </table>`;
  } catch (err) {
    if (err.name === "AbortError") return;
    wrap.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
    infoEl.textContent = "";
  }
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
}
