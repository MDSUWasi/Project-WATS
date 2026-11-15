// Project WATS — quality view
// Frame quality flags, completeness breakdown, and gaps.

import { fetchJson } from "/api.js";
import { fmt, escapeHtml } from "/state.js";

let abortCtrl = null;

export function render(container) {
  const q = new URLSearchParams(location.search);
  const name = q.get("name") || "Custom field";
  const ra = q.get("ra"), dec = q.get("dec");

  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Quality & completeness: ${escapeHtml(name)}</h2>
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
  loadBtn.addEventListener("click", () => {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    loadQuality({ signal: abortCtrl.signal }, { wrap: container.querySelector("#qual-wrap"), infoEl: container.querySelector("#qual-info") });
  });

  if (ra && dec) loadBtn.click();
}

function bar(label, value, detail) {
  const pct = Math.max(0, Math.min(100, Math.round((Number(value)||0)*100)));
  return `<div class="comp-item"><div class="comp-label">${escapeHtml(label)}</div>
    <div class="comp-bar"><div class="comp-fill" style="width:${pct}%"></div></div>
    <div class="comp-val">${pct}%${detail ? ` <span class="muted" style="font-weight:400">(${escapeHtml(detail)})</span>` : ""}</div></div>`;
}

function renderCompletenessCard(comp) {
  if (!comp) return "";
  const c = comp.components || {};
  const w = comp.weights || {};
  const rows = [
    bar("Visits", c.visits, `${comp.fetched_visits ?? 0}/${comp.available_visits ?? "?"}`),
    bar("Detectors", c.detectors, `${comp.fetched_detectors ?? 0}/${comp.available_detectors ?? 6}`),
    bar("Band/wavelength sampling", c.bands, `${comp.bands_covered ?? 0}/102`),
    bar("Pixel quality", c.pixel_quality, comp.flags_bad_fraction != null ? `${(100*comp.flags_bad_fraction).toFixed(1)}% bad` : ""),
    bar("Catalog match", c.catalog, c.catalog ? "" : "not computed"),
  ].join("");
  const scorePct = Math.round((Number(comp.completeness_score)||0)*100);
  return `<div class="card" style="margin-bottom:12px;"><div class="card-head"><h3>Completeness score: ${scorePct}%</h3></div>
    <div class="completeness-grid">${rows}</div>
    <p class="muted" style="margin-top:8px;font-size:12px;">Weighted score (visits ${Math.round((w.visits||0)*100)}%, detectors ${Math.round((w.detectors||0)*100)}%, bands ${Math.round((w.bands||0)*100)}%, pixel quality ${Math.round((w.pixel_quality||0)*100)}%, catalog ${Math.round((w.catalog||0)*100)}%).</p></div>`;
}

async function loadQuality(opts = {}, refs = {}) {
  const wrap = refs.wrap || document.querySelector("#qual-wrap");
  const infoEl = refs.infoEl || document.querySelector("#qual-info");
  wrap.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Loading quality data…</div>`;
  try {
    const key = new URLSearchParams(location.search).get("key");
    if (!key) {
      wrap.innerHTML = `<div class="panel muted">No target loaded. Open <strong>Sky / Blink</strong>, load a target, then return here to inspect per-frame quality and completeness.</div>`;
      infoEl.textContent = "";
      return;
    }
    const [qRes, cRes] = await Promise.all([
      fetchJson(`/api/targets/${encodeURIComponent(key)}/quality`, opts.signal),
      fetchJson(`/api/targets/${encodeURIComponent(key)}/completeness`, opts.signal),
    ]);
    const q = (qRes.ok && qRes.data) ? qRes.data : null;
    const comp = (cRes.ok && cRes.data) ? cRes.data : null;
    if (!q && !comp) throw new Error((qRes.data && qRes.data.error) || (cRes.data && cRes.data.error) || "Quality data unavailable");

    const frames = (q && q.frames) || [];
    const badFrac = Number((q && q.flags_bad_fraction) || 0);
    const totalBad = Number((q && q.flags_bad_total) || 0);
    const totalFlags = Number((q && q.flags_total) || 0);
    infoEl.textContent = `${(q && q.n_frames) || frames.length} frames · bad-flag fraction ${(100 * badFrac).toFixed(2)}% (${totalBad.toLocaleString()} / ${totalFlags.toLocaleString()} pixels)`;

    // flag-gap detection: frames with unusually high bad fraction or few detectors
    const meanBad = frames.length ? frames.reduce((a,f)=>a+Number(f.flags_bad_fraction||0),0)/frames.length : 0;
    const gaps = frames.filter(f => Number(f.flags_bad_fraction||0) > Math.max(0.2, meanBad*3 + 0.05) || Number(f.n_detectors||0) === 0);
    const gapNote = gaps.length
      ? `<div class="panel" style="margin-bottom:12px;border-color:var(--terracotta,#d07a4b);"><strong>${gaps.length} frame(s) flagged for review</strong> (high bad-pixel fraction or missing detectors): ${gaps.map(f=>`#${f.index}`).join(", ")}.</div>`
      : `<div class="panel muted" style="margin-bottom:12px;">No significant per-frame quality gaps detected (threshold: &gt;20% bad or &gt;3× mean).</div>`;

    const table = frames.length ? `
      <div class="card"><div class="card-head"><h3>Per-frame quality</h3></div>
      <table class="visit-table">
        <thead><tr><th>Frame</th><th>MJD</th><th>Bad fraction</th><th>Bad/total pixels</th><th>Detectors</th><th>Channels</th></tr></thead>
        <tbody>${frames.map((f) => {
          const bf = Number(f.flags_bad_fraction || 0);
          const warn = bf > Math.max(0.2, meanBad*3 + 0.05) ? ' style="color:var(--terracotta,#d07a4b);font-weight:600"' : "";
          return `<tr>
            <td>${f.index ?? "—"}</td>
            <td>${Number(f.mjd || 0).toFixed(4)}</td>
            <td${warn}>${(100 * bf).toFixed(2)}%</td>
            <td>${Number(f.flags_bad ?? 0).toLocaleString()} / ${Number(f.flags_total ?? 0).toLocaleString()}</td>
            <td>${f.n_detectors ?? "—"}</td>
            <td>${f.n_channels ?? "—"}</td>
          </tr>`;
        }).join("")}
        </tbody>
      </table></div>` : `<div class="panel muted">No frame quality records available.</div>`;

    wrap.innerHTML = `${renderCompletenessCard(comp)}${gapNote}${table}`;
  } catch (err) {
    if (err && err.name === "AbortError") return;
    wrap.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
    infoEl.textContent = "";
  }
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
}
