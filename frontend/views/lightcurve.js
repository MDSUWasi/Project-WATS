// Project WATS — light curve view (native canvas, no external deps)
import { fetchJson } from "/api.js";
import { fmt, escapeHtml } from "/state.js";

let abortCtrl = null;
let lastPoints = null;

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
    loadLightCurve({ ra, dec, fov, max_frames, name }, { signal: abortCtrl.signal });
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

async function loadLightCurve(params, opts = {}) {
  const wrap = document.querySelector("#lc-wrap");
  const infoEl = document.querySelector("#lc-info");
  const statsEl = document.querySelector("#lc-stats");
  const csvBtn = document.querySelector("#lc-csv-btn");
  wrap.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Building light curve…</div>`;
  csvBtn.disabled = true;
  try {
    const q = new URLSearchParams({ ra: params.ra, dec: params.dec, fov: params.fov, max_frames: params.max_frames, name: params.name });
    const res = await fetchJson(`/api/spectrum?${q.toString()}`, opts.signal);
    if (res.status === 202) {
      setTimeout(() => loadLightCurve(params, opts), 2500);
      return;
    }
    if (!res.ok || !res.data) throw new Error(res.data?.error || "Light curve unavailable");
    const data = res.data;
    infoEl.textContent = `${data.n_points} points`;
    lastPoints = data.points || [];
    renderLightCurve(data, statsEl);
    csvBtn.disabled = false;
  } catch (err) {
    if (err.name === "AbortError") return;
    wrap.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
    statsEl.textContent = "";
  }
}

function renderLightCurve(data, statsEl) {
  const wrap = document.querySelector("#lc-wrap");
  wrap.innerHTML = `<canvas class="lc-canvas" width="700" height="300"></canvas>`;
  const canvas = wrap.querySelector("canvas");
  const ctx = canvas.getContext("2d");

  const points = data.points || [];
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
    ctx.beginPath(); ctx.arc(x, y, 3.2, 0, Math.PI * 2);
    ctx.fillStyle = "#4f9dff";
    ctx.fill();
  }

  statsEl.innerHTML = `
    <strong>Variability:</strong> N=${finiteFlux.length} · mean=${fmt(mean, 3)} mJy · RMS=${fmt(rms, 3)} mJy · amplitude=${fmt(amplitude, 3)} mJy
  `;
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
  lastPoints = null;
}
