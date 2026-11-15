// Project WATS — light curve view (native canvas, no external deps)
import { fetchJson } from "/api.js";
import { fmt, escapeHtml } from "/state.js";

let abortCtrl = null;
let lastPoints = null;
let uplotInstance = null;
let _retryCount = 0;
const MAX_RETRIES = 20;

export function render(container) {
  const q = new URLSearchParams(location.search);
  const ra = q.get("ra"); const dec = q.get("dec");
  const fov = q.get("fov") || "0.15";
  const maxFrames = q.get("max_frames") || "6";
  const name = q.get("name") || "Custom field";

  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Light curve: ${escapeHtml(name)}</h2>
        <div class="coords-meta">${escapeHtml(ra ? `RA ${Number(ra).toFixed(5)}° · Dec ${Number(dec).toFixed(5)}°` : "—")}</div>
      </div>
      <div class="lc-toolbar" style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
        <button class="primary" id="lc-load-btn">Load light curve</button>
        <button class="ghost" id="lc-csv-btn" title="Download CSV">CSV</button>
        <span id="lc-info" class="muted" style="margin-left:auto;font-size:12px;"></span>
      </div>
      <div class="lc-wrap" id="lc-wrap"></div>
      <div id="lc-stats" style="margin-top:8px;font-size:12px;color:var(--muted-2);"></div>
    </div>
  `;

  const loadBtn = container.querySelector("#lc-load-btn");
  const csvBtn = container.querySelector("#lc-csv-btn");
  const infoEl = container.querySelector("#lc-info");
  const statsEl = container.querySelector("#lc-stats");
  const wrap = container.querySelector("#lc-wrap");

  csvBtn.disabled = true;

  loadBtn.addEventListener("click", () => {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    loadLightCurve({ ra, dec, fov, max_frames: maxFrames, name }, { signal: abortCtrl.signal });
  });

  csvBtn.addEventListener("click", () => {
    if (!lastPoints || !lastPoints.length) return;
    const rows = lastPoints.map((p) => [p.mjd, p.flux_mjy_sr, p.error_mjy_sr, p.detector]);
    const csv = ["mjd,flux_mjy_sr,error_mjy_sr,detector", ...rows.map((r) => r.join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `lightcurve_${name.replace(/\s+/g, "_")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  });

  if (ra && dec) loadBtn.click();
}

function buildLightCurvePoints(raw) {
  // Spectrum points contain multi-wavelength samples per epoch. For a scientific
  // light curve (flux vs time) aggregate by epoch/MJD: mean flux and propagated
  // error per MJD, then sort chronologically to avoid zig-zag/spiral artifacts.
  const by = new Map();
  for (const p of (raw || [])) {
    const mjd = Number(p.mjd);
    const flux = Number(p.flux_jy);
    if (!Number.isFinite(mjd) || !Number.isFinite(flux)) continue;
    const key = Number.isFinite(Number(p.frame)) ? `f:${Number(p.frame)}` : `m:${mjd.toFixed(6)}`;
    let g = by.get(key);
    if (!g) { g = { mjd: 0, fluxSum: 0, err2Sum: 0, n: 0, time: p.time || null, detectors: new Set(), wavelengths: [] }; by.set(key, g); }
    g.mjd += mjd; g.fluxSum += flux;
    const e = Number(p.error_jy); if (Number.isFinite(e)) g.err2Sum += e * e;
    g.n += 1;
    if (p.detector) g.detectors.add(p.detector);
    if (Number.isFinite(Number(p.wavelength_um))) g.wavelengths.push(Number(p.wavelength_um));
  }
  const out = [];
  for (const g of by.values()) {
    const mjd = g.mjd / g.n;
    const flux_jy = g.fluxSum / g.n;
    const error_jy = g.err2Sum > 0 ? Math.sqrt(g.err2Sum) / g.n : 0;
    const wl = g.wavelengths.length ? g.wavelengths.reduce((a,b)=>a+b,0)/g.wavelengths.length : null;
    out.push({ mjd, flux_jy, error_jy, time: g.time, n_samples: g.n, detector: Array.from(g.detectors).join(","), wavelength_um: wl });
  }
  out.sort((a,b) => a.mjd - b.mjd);
  return out;
}

async function loadLightCurve(params, opts = {}) {
  const wrap = document.querySelector("#lc-wrap");
  const infoEl = document.querySelector("#lc-info");
  const statsEl = document.querySelector("#lc-stats");
  const csvBtn = document.querySelector("#lc-csv-btn");
  wrap.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Building light curve…</div>`;
  csvBtn.disabled = true;
  try {
    const q = new URLSearchParams({ ra: params.ra, dec: params.dec, fov: params.fov, max_frames: params.max_frames, name: params.name });
    let res = await fetchJson(`/api/spectrum?${q.toString()}`, opts.signal);
    if (res.status === 202) {
      if (_retryCount++ >= MAX_RETRIES) {
        wrap.innerHTML = `<div class="panel error">Light curve timed out after ${MAX_RETRIES} retries.</div>`;
        return;
      }
      setTimeout(() => loadLightCurve(params, opts), 2500);
      return;
    }
    _retryCount = 0;
    if (!res.ok || !res.data) throw new Error(res.data?.error || "Light curve unavailable");
    const data = res.data;
    const raw = data.points || [];
    const points = buildLightCurvePoints(raw);
    infoEl.textContent = `${points.length} epochs (${raw.length} samples)`;
    lastPoints = points;
    renderLightCurve({ ...data, points }, statsEl);
    csvBtn.disabled = !points.length;
  } catch (err) {
    if (err.name === "AbortError") return;
    wrap.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
    statsEl.textContent = "";
  }
}

function renderLightCurve(data, statsEl) {
  const wrap = document.querySelector("#lc-wrap");
  const points = (data.points || []).slice().sort((a,b) => (Number(a.mjd)||0) - (Number(b.mjd)||0));
  if (typeof uPlot !== "undefined" && points.length) {
    renderLightCurveUPlot(data, statsEl);
    return;
  }
  wrap.innerHTML = `<canvas class="lc-canvas" width="700" height="300"></canvas>`;
  const canvas = wrap.querySelector("canvas");
  const ctx = canvas.getContext("2d");

  if (!points.length) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "rgba(150, 180, 210, 0.55)";
    ctx.font = "400 14px ui-sans-serif, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No light curve points available.", canvas.width / 2, canvas.height / 2);
    statsEl.textContent = "";
    return;
  }

  const mjd = points.map((p) => Number(p.mjd) || 0);
  const flux = points.map((p) => (Number(p.flux_jy) || 0) * 1000);
  const err = points.map((p) => (Number(p.error_jy) || 0) * 1000);

  const mMin = Math.min(...mjd) - 1;
  const mMax = Math.max(...mjd) + 1;
  const vals = flux.map((v, i) => v - err[i]);
  const tops = flux.map((v, i) => v + err[i]);
  let fMin = Math.min(0, ...vals);
  let fMax = Math.max(0, ...tops);
  if (fMax - fMin < 1e-9) { fMin = -1; fMax = 1; }
  const pad = (fMax - fMin) * 0.12;
  fMin -= pad; fMax += pad;

  const ml = 62, mr = 18, mt = 20, mb = 42;
  const x0 = ml, x1 = canvas.width - mr, y0 = mt, y1 = canvas.height - mb;

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#080D13";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const mapX = (v) => x0 + ((v - mMin) / (mMax - mMin)) * (x1 - x0);
  const mapY = (v) => y1 - ((v - fMin) / (fMax - fMin)) * (y1 - y0);

  // Grid
  ctx.strokeStyle = "rgba(80, 115, 150, 0.22)";
  ctx.lineWidth = 1;
  ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.fillStyle = "rgba(140, 170, 200, 0.75)";
  const tickCount = 6;
  for (let i = 0; i <= tickCount; i++) {
    const v = mMin + (mMax - mMin) * (i / tickCount);
    const x = mapX(v);
    ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1); ctx.stroke();
    ctx.fillText(v.toFixed(1), x - 4, y1 + 16);
  }
  const yTicks = 5;
  for (let i = 0; i <= yTicks; i++) {
    const v = fMin + (fMax - fMin) * (i / yTicks);
    const y = mapY(v);
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    ctx.fillText(fmt(v, 2), 6, y + 4);
  }
  ctx.strokeStyle = "rgba(140, 175, 210, 0.5)";
  ctx.beginPath(); ctx.moveTo(x0, y1); ctx.lineTo(x1, y1); ctx.moveTo(x0, y0); ctx.lineTo(x0, y1); ctx.stroke();

  ctx.fillStyle = "rgba(160, 190, 220, 0.8)";
  ctx.font = "12px ui-sans-serif, system-ui, sans-serif";
  ctx.fillText("MJD", (x0 + x1) / 2 - 10, canvas.height - 8);
  ctx.save();
  ctx.translate(14, (y0 + y1) / 2 + 40);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("Flux (mJy)", 0, 0);
  ctx.restore();

  // Connecting line in chronological order (epoch means)
  if (points.length > 1) {
    ctx.strokeStyle = "rgba(79,157,255,0.7)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    for (let i = 0; i < points.length; i++) {
      const x = mapX(Number(points[i].mjd) || 0);
      const y = mapY((Number(points[i].flux_jy) || 0) * 1000);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  // Points + error bars
  const finiteFlux = flux.filter((v) => Number.isFinite(v));
  const mean = finiteFlux.length ? finiteFlux.reduce((a, b) => a + b, 0) / finiteFlux.length : 0;
  const rms = finiteFlux.length ? Math.sqrt(finiteFlux.reduce((a, v) => a + (v - mean) ** 2, 0) / finiteFlux.length) : 0;
  const amplitude = finiteFlux.length ? Math.max(...finiteFlux) - Math.min(...finiteFlux) : 0;

  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    const x = mapX(Number(p.mjd) || 0);
    const y = mapY((Number(p.flux_jy) || 0) * 1000);
    const e = (Number(p.error_jy) || 0) * 1000;
    const yTop = mapY((Number(p.flux_jy) || 0) * 1000 + e);
    const yBot = mapY((Number(p.flux_jy) || 0) * 1000 - e);
    ctx.strokeStyle = "#4f9dff";
    ctx.globalAlpha = 0.7;
    ctx.beginPath(); ctx.moveTo(x, yTop); ctx.lineTo(x, yBot); ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = "#4f9dff"; ctx.fill();
    ctx.strokeStyle = "#081018"; ctx.lineWidth = 1; ctx.stroke();
  }

  statsEl.innerHTML = `<strong>Epoch photometry (per-MJD mean of spectrum samples):</strong> N=${finiteFlux.length} · mean=${fmt(mean, 3)} mJy · RMS=${fmt(rms, 3)} mJy · Δ=${fmt(amplitude, 3)} mJy`;
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
  lastPoints = null;
  if (uplotInstance) {
    try { uplotInstance.destroy(); } catch (_) {}
    uplotInstance = null;
  }
}

function renderLightCurveUPlot(data, statsEl) {
  const wrap = document.querySelector("#lc-wrap");
  const points = (data.points || []).slice().sort((a,b) => (Number(a.mjd)||0) - (Number(b.mjd)||0));
  wrap.innerHTML = `<div class="lc-uplot-wrap"></div>`;
  const el = wrap.querySelector(".lc-uplot-wrap");

  const mjd = points.map((p) => Number(p.mjd) || 0);
  const flux = points.map((p) => (Number(p.flux_jy) || 0) * 1000);
  const err = points.map((p) => (Number(p.error_jy) || 0) * 1000);
  const lower = flux.map((v, i) => v - err[i]);
  const upper = flux.map((v, i) => v + err[i]);

  const allVals = [...lower, ...upper, 0].filter(Number.isFinite);
  let fMin = Math.min(...allVals), fMax = Math.max(...allVals);
  if (!(fMax > fMin)) { fMin -= 1; fMax += 1; }
  const pad = (fMax - fMin) * 0.12;

  if (uplotInstance) {
    try { uplotInstance.destroy(); } catch (_) {}
    uplotInstance = null;
  }

  const mMin = mjd.length ? Math.min(...mjd) : 0;
  const mMax = mjd.length ? Math.max(...mjd) : 1;
  const mPad = mMax > mMin ? (mMax - mMin) * 0.05 : 0.5;

  uplotInstance = new uPlot({
    width: 700,
    height: 300,
    series: [
      { label: "MJD" },
      { label: "Flux (mJy)", stroke: "#4f9dff", width: 2, points: { show: true, size: 6, fill: "#4f9dff" } },
      { label: "−1σ", stroke: "rgba(79,157,255,0.0)", width: 0 },
      { label: "+1σ", stroke: "rgba(79,157,255,0.0)", width: 0, fill: "rgba(79,157,255,0.15)", band: [2, 1] },
    ],
    axes: [
      { stroke: "rgba(140,170,200,0.75)", grid: { stroke: "rgba(80,115,150,0.2)" }, values: (self, vals) => vals.map(v => Number(v).toFixed(2)) },
      { stroke: "rgba(140,170,200,0.75)", grid: { stroke: "rgba(80,115,150,0.2)" }, values: (self, vals) => vals.map(v => Number(v).toFixed(2)) },
    ],
    scales: {
      x: { time: false, min: mMin - mPad, max: mMax + mPad },
      y: { min: fMin - pad, max: fMax + pad },
    },
  }, [mjd, flux, lower, upper], el);

  const finiteFlux = flux.filter((v) => Number.isFinite(v));
  const mean = finiteFlux.length ? finiteFlux.reduce((a, b) => a + b, 0) / finiteFlux.length : 0;
  const rms = finiteFlux.length ? Math.sqrt(finiteFlux.reduce((a, v) => a + (v - mean) ** 2, 0) / finiteFlux.length) : 0;
  const amp = finiteFlux.length ? Math.max(...finiteFlux) - Math.min(...finiteFlux) : 0;
  statsEl.innerHTML = `<strong>Epoch photometry (per-MJD mean of spectrum samples):</strong> N=${finiteFlux.length} · mean=${fmt(mean, 3)} mJy · RMS=${fmt(rms, 3)} mJy · Δ=${fmt(amp, 3)} mJy`;
}
