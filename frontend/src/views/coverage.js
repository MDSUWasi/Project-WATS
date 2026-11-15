// Project WATS — target / coverage view
// Shows coverage info, completeness breakdown, and job controls.

import { fetchJson } from "/api.js";
import { fmt, escapeHtml } from "/state.js";
import { renderCompleteness, renderJobProgress, bindJobActions, sectionHead, statGrid } from "/ui/components.js";

let abortCtrl = null;

export function render(container) {
  const q = new URLSearchParams(location.search);
  const ra = q.get("ra");
  const dec = q.get("dec");
  const fov = q.get("fov") || "0.15";
  const maxFrames = q.get("max_frames") || "6";
  const name = q.get("name") || "Custom field";

  container.innerHTML = "";

  // Header
  const head = document.createElement("div");
  head.className = "card-head";
  head.innerHTML = `<h2>Target: ${escapeHtml(name)}</h2><div class="coords-meta">${escapeHtml(ra ? `RA ${Number(ra).toFixed(5)}° · Dec ${Number(dec).toFixed(5)}°` : "—")}</div>`;
  container.appendChild(head);

  // Query form (compact)
  const formCard = document.createElement("div");
  formCard.className = "card";
  formCard.innerHTML = `
    <div class="card-head"><h2>Field</h2></div>
    <form id="coverage-form" class="field-row">
      <div class="field"><label>RA</label><input type="text" name="ra" value="${escapeHtml(ra || "")}" placeholder="decimal or 08h55m10s"></div>
      <div class="field"><label>Dec</label><input type="text" name="dec" value="${escapeHtml(dec || "")}" placeholder="decimal or -7d14m42s"></div>
      <div class="field"><label>FoV (°)</label><input type="number" name="fov" value="${escapeHtml(fov)}" step="0.01" min="0.01" max="10"></div>
      <div class="field"><label>Max frames</label><select name="max_frames"><option value="6">6 (quick)</option><option value="12">12</option><option value="24">24</option><option value="48">48</option><option value="96">96 (complete)</option></select></div>
      <div class="field"><label>Name</label><input type="text" name="name" value="${escapeHtml(name)}"></div>
      <button type="submit" class="primary">Load coverage</button>
    </form>
  `;
  container.appendChild(formCard);

  // Results area
  const results = document.createElement("div");
  results.id = "coverage-results";
  container.appendChild(results);

  const form = formCard.querySelector("#coverage-form");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const params = {
      ra: fd.get("ra"),
      dec: fd.get("dec"),
      fov: fd.get("fov"),
      max_frames: fd.get("max_frames"),
      name: fd.get("name"),
    };
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    loadCoverage(params, results, { signal: abortCtrl.signal });
  });

  if (ra && dec) {
    loadCoverage({ ra, dec, fov, max_frames: maxFrames, name }, results);
  }
}

async function loadCoverage(params, results, opts = {}) {
  results.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Querying IRSA coverage…</div>`;
  try {
    const q = new URLSearchParams({
      ra: params.ra, dec: params.dec, fov: params.fov, max_frames: params.max_frames, name: params.name,
    });
    const covRes = await fetchJson(`/api/coverage?${q.toString()}`, opts.signal);
    // coverage endpoint
    if (!covRes.ok || !covRes.data) throw new Error(covRes.data?.error || "Coverage query failed");
    const cov = covRes.data;
    renderCoverage(results, cov);
  } catch (err) {
    if (err.name === "AbortError") return;
    let msg = String(err);
    if (/TAP|IRSA|unavailable|unreachable|network|refused|timed out/i.test(msg)) {
      msg = "Coverage requires an IRSA TAP query, which is unavailable from this host. Use a locally cached preset in the Sky view, or run on a host with internet access.";
    }
    results.innerHTML = `<div class="panel error">${escapeHtml(msg)}</div>`;
  }
}

function renderCoverage(container, cov) {
  container.innerHTML = "";

  // Stats
  statGrid(container, [
    { value: cov.available_visits, label: "Available visits" },
    { value: cov.available_detectors, label: "Detectors" },
    { value: cov.bands_covered, label: "SPHEREx bands covered" },
    { value: cov.query_rows, label: "MEF rows" },
  ]);

  // Visit breakdown
  const visitsCard = document.createElement("div");
  visitsCard.className = "card";
  visitsCard.innerHTML = `<div class="card-head"><h2>Visits (${cov.available_visits})</h2></div>`;
  const vlist = document.createElement("div");
  vlist.className = "visit-list";
  const visits = Array.isArray(cov.visits) ? cov.visits : [];
  if (visits.length) {
    vlist.innerHTML = `<table class="visit-table"><thead><tr><th>Obs ID</th><th>MJD</th><th>Detectors</th><th>Release</th></tr></thead><tbody>${visits.map((v) => `
      <tr><td>${escapeHtml(String(v.obs_id || v.obs_base || "—"))}</td><td>${Number(v.mjd || 0).toFixed(2)}</td><td>${(v.detectors || []).join(", ")}</td><td>${escapeHtml(String(v.release || "—"))}</td></tr>
    `).join("")}</tbody></table>`;
  } else {
    vlist.innerHTML = `<div class="muted">No visits found at this position.</div>`;
  }
  visitsCard.appendChild(vlist);
  container.appendChild(visitsCard);

  // Band ranges
  const bandsCard = document.createElement("div");
  bandsCard.className = "card";
  bandsCard.innerHTML = `<div class="card-head"><h2>Detector band coverage</h2></div>`;
  const br = Array.isArray(cov.band_ranges) ? cov.band_ranges : [];
  bandsCard.innerHTML += `<div class="band-grid">${br.map((b) => `
    <div class="band-item"><div class="band-name">${escapeHtml(String(b.detector || "D?"))}</div><div class="band-range">${Number(b.wavelength_min_um || 0).toFixed(2)}–${Number(b.wavelength_max_um || 0).toFixed(2)} µm</div></div>
  `).join("")}</div>`;
  container.appendChild(bandsCard);

  // Completeness: prefer server manifest completeness for the loaded target; fall back to coverage-based estimate.
  const compCard = document.createElement("div");
  compCard.className = "card";
  compCard.innerHTML = `<div class="card-head"><h2>Completeness</h2></div><div id="comp-body" class="muted">Loading…</div>`;
  container.appendChild(compCard);
  const compBody = compCard.querySelector("#comp-body");
  (async () => {
    const key = new URLSearchParams(location.search).get("key");
    let data = null;
    if (key) {
      const r = await fetchJson(`/api/targets/${encodeURIComponent(key)}/completeness`);
      if (r.ok && r.data) data = r.data;
    }
    if (data && data.components) {
      const c = data.components || {};
      const row = (label, val, detail) => {
        const pct = Math.max(0, Math.min(100, Math.round((Number(val)||0)*100)));
        return `<div class="comp-item"><div class="comp-label">${label}</div><div class="comp-bar"><div class="comp-fill" style="width:${pct}%"></div></div><div class="comp-val">${pct}%${detail?` <span class="muted" style="font-weight:400">(${detail})</span>`:""}</div></div>`;
      };
      const scorePct = Math.round((Number(data.completeness_score)||0)*100);
      compBody.innerHTML = `<div class="completeness-grid">
        ${row("Visits", c.visits, `${data.fetched_visits ?? 0}/${data.available_visits ?? "?"}`)}
        ${row("Detectors", c.detectors, `${data.fetched_detectors ?? 0}/${data.available_detectors ?? 6}`)}
        ${row("Band sampling", c.bands, `${data.bands_covered ?? 0}/102`)}
        ${row("Pixel quality", c.pixel_quality, data.flags_bad_fraction!=null?`${(100*data.flags_bad_fraction).toFixed(1)}% bad`:"")}
        ${row("Catalog match", c.catalog, c.catalog ? "" : "not computed")}
      </div>
      <div class="score-summary" style="margin-top:10px;font-weight:600;">Completeness score (loaded target): ${scorePct}%</div>
      <p class="muted" style="margin-top:6px;font-size:12px;">Computed from the manifest of the currently loaded target.</p>`;
    } else {
      const visitsScore = Math.min(1, (cov.available_visits||0) / 6);
      const detScore = Math.min(1, (cov.available_detectors||0) / 6);
      const bandScore = Math.min(1, (cov.bands_covered||0) / 6);
      const score = 0.40 * visitsScore + 0.20 * detScore + 0.20 * bandScore;
      const row = (label, val) => `<div class="comp-item"><div class="comp-label">${label}</div><div class="comp-bar"><div class="comp-fill" style="width:${Math.round(100*val)}%"></div></div><div class="comp-val">${(100*val).toFixed(0)}%</div></div>`;
      compBody.innerHTML = `<div class="completeness-grid">${row("Visits (available)", visitsScore)}${row("Detectors (available)", detScore)}${row("Detector bands (available)", bandScore)}</div>
      <div class="score-summary" style="margin-top:10px;font-weight:600;">Coverage-based estimate: ${(100*score/0.8).toFixed(0)}%</div>
      <p class="muted" style="margin-top:6px;font-size:12px;">Estimate from IRSA coverage for this field (pixel-quality/catalog components require a loaded target manifest).</p>`;
    }
  })();

  // Job controls
  const jobCard = document.createElement("div");
  jobCard.className = "card";
  jobCard.innerHTML = `
    <div class="card-head"><h2>Start fetch job</h2></div>
    <form id="start-job-form" class="field-row">
      <div class="field"><label>Mode</label><select name="mode"><option value="quick">Quick (6 frames)</option><option value="complete">Complete (all available)</option></select></div>
      <button type="submit" class="primary" id="start-job-btn">Start job</button>
    </form>
    <div id="job-progress" style="margin-top:12px;"></div>
  `;
  container.appendChild(jobCard);

  const jobForm = jobCard.querySelector("#start-job-form");
  const jobProgress = jobCard.querySelector("#job-progress");
  const jobBtn = jobCard.querySelector("#start-job-btn");

  jobForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(jobForm);
    const mode = fd.get("mode");
    const q = new URLSearchParams({
      ra: params.ra, dec: params.dec, fov: params.fov,
      max_frames: mode === "complete" ? "96" : params.max_frames,
      name: params.name,
    });
    jobBtn.disabled = true;
    jobBtn.textContent = "Starting…";
    try {
      const res = await fetchJson(`/api/targets/jobs?${q.toString()}`, { method: "POST" });
      if (res.ok && res.data && res.data.job) {
        const job = res.data.job;
        const jobId = job.id;
        jobProgress.innerHTML = "";
        renderJobProgress(jobProgress, job);
        bindJobActions(jobProgress,
          async (id) => { await fetchJson(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }); },
          async (id) => { await fetchJson(`/api/jobs/${encodeURIComponent(id)}/resume`, { method: "POST" }); }
        );
        pollJob(jobId, jobProgress);
      } else {
        jobBtn.disabled = false;
        jobBtn.textContent = "Start job";
        jobProgress.innerHTML = `<div class="panel error">${escapeHtml(String(res.data?.error || "Failed to start job"))}</div>`;
      }
    } catch (err) {
      jobBtn.disabled = false;
      jobBtn.textContent = "Start job";
      jobProgress.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
    }
  });
}

async function pollJob(jobId, container) {
    let polling = true;
    async function tick() {
        if (!polling) return;
        const res = await fetchJson(`/api/jobs/${encodeURIComponent(jobId)}`);
        if (res.ok && res.data) {
            const job = res.data.job || res.data;
            // Clear previous listeners before re-rendering so detached
            // handlers and DOM nodes don't accumulate on every poll tick.
            container.innerHTML = "";
            renderJobProgress(container, job);
            bindJobActions(container,
                async (jid) => { await fetchJson(`/api/jobs/${encodeURIComponent(jid)}/cancel`, { method: "POST" }); polling = false; },
                async (jid) => { await fetchJson(`/api/jobs/${encodeURIComponent(jid)}/resume`, { method: "POST" }); }
            );
            if (job.status === "done" || job.status === "error" || job.status === "cancelled") {
                polling = false;
                return;
            }
        }
        setTimeout(tick, 2000);
    }
    tick();
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
}
