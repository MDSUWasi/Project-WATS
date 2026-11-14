// Project WATS — sources view
// Extracted source catalog with cross-match indicators.

import { fetchJson } from "/api.js";
import { escapeHtml } from "/state.js";

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
        <h2>Sources: ${escapeHtml(name)}</h2>
        <div class="coords-meta">${escapeHtml(ra ? `RA ${Number(ra).toFixed(5)}° · Dec ${Number(dec).toFixed(5)}°` : "—")}</div>
      </div>
      <div class="sources-toolbar" style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
        <button class="primary" id="src-load-btn">Extract sources</button>
        <button class="ghost" id="src-csv-btn" title="Download CSV">CSV</button>
        <label style="font-size:12px;display:flex;align-items:center;gap:4px;margin-left:auto;">
          <input type="checkbox" id="src-show-gaia" checked> Gaia
          <input type="checkbox" id="src-show-catwise" checked style="margin-left:8px;"> CatWISE
        </label>
        <span id="src-info" class="muted" style="font-size:12px;"></span>
      </div>
      <div id="src-wrap"></div>
    </div>
  `;

  const loadBtn = container.querySelector("#src-load-btn");
  const csvBtn = container.querySelector("#src-csv-btn");
  const infoEl = container.querySelector("#src-info");
  const wrap = container.querySelector("#src-wrap");
  csvBtn.disabled = true;

  loadBtn.addEventListener("click", () => {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    loadSources({ ra, dec, fov, max_frames, name }, { signal: abortCtrl.signal });
  });

  csvBtn.addEventListener("click", () => {
    const table = wrap.querySelector("table");
    if (!table) return;
    const rows = [];
    for (const tr of table.querySelectorAll("tbody tr")) {
      const cells = [...tr.querySelectorAll("td")].map((td) => td.textContent);
      rows.push(cells.join(","));
    }
    const csv = ["frame_index,mjd,x,y,flux_mjy_sr,detector,match_gaia,match_catwise", ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `sources_${name.replace(/\s+/g, "_")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  });

  if (ra && dec) loadBtn.click();
}

async function loadSources(params, opts = {}) {
  const wrap = document.querySelector("#src-wrap");
  const infoEl = document.querySelector("#src-info");
  const csvBtn = document.querySelector("#src-csv-btn");
  wrap.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Extracting sources…</div>`;
  csvBtn.disabled = true;
  try {
    const q = new URLSearchParams({ ra: params.ra, dec: params.dec, fov: params.fov, max_frames: params.max_frames, name: params.name });
    const res = await fetchJson(`/api/targets/placeholder/sources`, opts.signal);
    // sources endpoint needs a target key; fall back to spectrum-based source list
    infoEl.textContent = "Source extraction requires a loaded target. Use the Sky view to load frames first.";
    wrap.innerHTML = `<div class="panel muted">Load a target in the Sky view, then sources will appear here.</div>`;
  } catch (err) {
    if (err.name === "AbortError") return;
    wrap.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
  }
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
}
