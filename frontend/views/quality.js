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
  const infoEl = document.querySelector("#qual-info");
  const wrap = document.querySelector("#qual-wrap");

  loadBtn.addEventListener("click", () => {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    loadQuality({ ra, dec, fov, max_frames, name }, { signal: abortCtrl.signal });
  });

  if (ra && dec) loadBtn.click();
}

async function loadQuality(params, opts = {}) {
  const wrap = document.querySelector("#qual-wrap");
  const infoEl = document.querySelector("#qual-info");
  wrap.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Loading quality data…</div>`;
  try {
    const q = new URLSearchParams({ ra: params.ra, dec: params.dec, fov: params.fov, max_frames: params.max_frames, name: params.name });
    const res = await fetchJson(`/api/targets/placeholder/quality`, opts.signal);
    infoEl.textContent = "Quality details require a loaded target. Use the Sky view to load frames first.";
    wrap.innerHTML = `<div class="panel muted">Load a target in the Sky view to see per-frame flags, variance, ZODI, and coverage gaps.</div>`;
  } catch (err) {
    if (err.name === "AbortError") return;
    wrap.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
  }
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
}
