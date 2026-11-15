// Project WATS — sources view
// Extracted source detections from loaded frames (threshold-based).

import { fetchJson } from "/api.js";
import { escapeHtml, fmt } from "/state.js";

let abortCtrl = null;
let lastSources = [];

export function render(container) {
  const q = new URLSearchParams(location.search);
  const name = q.get("name") || "Custom field";
  const ra = q.get("ra"), dec = q.get("dec");

  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Source extraction: ${escapeHtml(name)}</h2>
        <div class="coords-meta">${escapeHtml(ra ? `RA ${Number(ra).toFixed(5)}° · Dec ${Number(dec).toFixed(5)}°` : "—")}</div>
      </div>
      <p class="muted" style="margin:0 0 10px;font-size:12px;">Detections are identified as pixels &gt; median + 3σ in each rendered gray frame (server-side threshold extraction). Use as a candidate list; verify against catalog overlays in the Sky view for scientific use.</p>
      <div class="sources-toolbar" style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
        <button class="primary" id="src-load-btn">Extract sources</button>
        <button class="ghost" id="src-csv-btn" title="Download CSV">CSV</button>
        <label class="muted" style="font-size:12px;">Min flux <input id="src-minflux" type="number" step="0.01" value="0" style="width:80px"></label>
        <span id="src-info" class="muted" style="margin-left:auto;font-size:12px;"></span>
      </div>
      <div id="src-wrap"></div>
    </div>
  `;

  const loadBtn = container.querySelector("#src-load-btn");
  const csvBtn = container.querySelector("#src-csv-btn");
  const minFluxEl = container.querySelector("#src-minflux");
  const infoEl = container.querySelector("#src-info");
  const wrap = container.querySelector("#src-wrap");
  csvBtn.disabled = true;

  const rerender = () => {
    const thr = Number(minFluxEl.value) || 0;
    renderTable(wrap, infoEl, csvBtn, thr);
  };
  minFluxEl.addEventListener("input", rerender);

  loadBtn.addEventListener("click", () => {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    loadSources({ signal: abortCtrl.signal }, { wrap, infoEl, csvBtn });
  });

  csvBtn.addEventListener("click", () => {
    const thr = Number(minFluxEl.value) || 0;
    const rows = lastSources.filter(s => Number(s.flux||0) >= thr);
    if (!rows.length) return;
    const header = "frame_index,x,y,flux_norm,mjd";
    const csv = [header, ...rows.map(s => [s.frame_index??"", s.x??"", s.y??"", Number(s.flux||0).toFixed(6), Number(s.mjd||0).toFixed(6)].join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const name = (new URLSearchParams(location.search).get("name") || "target").replace(/\s+/g, "_");
    a.href = url; a.download = `sources_${name}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  });

  if (ra && dec) loadBtn.click();
}

function renderTable(wrap, infoEl, csvBtn, minFlux) {
  const rows = lastSources.filter(s => Number(s.flux||0) >= (Number(minFlux)||0));
  if (!rows.length) {
    wrap.innerHTML = `<div class="panel muted">No detected sources above the current threshold. Try lowering the minimum flux or load a different target.</div>`;
    infoEl.textContent = lastSources.length ? `0 shown (${lastSources.length} detected total)` : "No sources detected";
    csvBtn.disabled = true;
    return;
  }
  infoEl.textContent = `${rows.length} shown of ${lastSources.length} detected (sorted by flux desc)`;
  csvBtn.disabled = false;
  wrap.innerHTML = `
    <table class="visit-table">
      <thead><tr><th>#</th><th>Frame</th><th>X (px)</th><th>Y (px)</th><th>Flux (norm.)</th><th>MJD</th></tr></thead>
      <tbody>${rows.map((s, i) => `
        <tr>
          <td>${i + 1}</td>
          <td>${s.frame_index ?? "—"}</td>
          <td>${s.x ?? "—"}</td>
          <td>${s.y ?? "—"}</td>
          <td>${Number(s.flux ?? 0).toFixed(4)}</td>
          <td>${Number.isFinite(Number(s.mjd)) ? Number(s.mjd).toFixed(4) : "—"}</td>
        </tr>`).join("")}
      </tbody>
    </table>`;
}

async function loadSources(opts = {}, refs = {}) {
  const wrap = refs.wrap || document.querySelector("#src-wrap");
  const infoEl = refs.infoEl || document.querySelector("#src-info");
  const csvBtn = refs.csvBtn || document.querySelector("#src-csv-btn");
  wrap.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Extracting sources from rendered frames…</div>`;
  csvBtn.disabled = true;
  try {
    const key = new URLSearchParams(location.search).get("key");
    if (!key) {
      wrap.innerHTML = `<div class="panel muted">No target loaded. Open <strong>Sky / Blink</strong>, load a target, then return here to extract source candidates.</div>`;
      infoEl.textContent = "";
      lastSources = [];
      return;
    }
    const res = await fetchJson(`/api/targets/${encodeURIComponent(key)}/sources`, opts.signal);
    if (!res.ok || !res.data) throw new Error(res.data?.error || "Source extraction failed");
    const data = res.data;
    lastSources = (data.sources || []).slice().sort((a,b)=>Number(b.flux||0)-Number(a.flux||0));
    const minFluxEl = document.querySelector("#src-minflux");
    renderTable(wrap, infoEl, csvBtn, Number(minFluxEl && minFluxEl.value) || 0);
  } catch (err) {
    if (err && err.name === "AbortError") return;
    wrap.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
    infoEl.textContent = "";
    lastSources = [];
  }
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
  lastSources = [];
}
