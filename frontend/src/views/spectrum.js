// Project WATS — spectrum view (native canvas, no external deps)
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
        <h2>Spectrum: ${escapeHtml(name)}</h2>
        <div class="coords-meta">${escapeHtml(ra ? `RA ${Number(ra).toFixed(5)}° · Dec ${Number(dec).toFixed(5)}°` : "—")}</div>
      </div>
      <div class="spectrum-toolbar" style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
        <button class="primary" id="spec-load-btn">Load spectrum</button>
        <button class="ghost" id="spec-csv-btn" title="Download CSV">CSV</button>
        <span id="spec-info" class="muted" style="margin-left:auto;font-size:12px;"></span>
        <span id="spec-count" class="muted" style="font-size:12px;"></span>
      </div>
      <div class="spectrum-wrap" id="spec-wrap"></div>
      <div id="spec-msg" style="margin-top:8px;font-size:12px;color:var(--muted-2);"></div>
    </div>
  `;

  const loadBtn = container.querySelector("#spec-load-btn");
  const csvBtn = container.querySelector("#spec-csv-btn");
  const infoEl = container.querySelector("#spec-info");
  const countEl = container.querySelector("#spec-count");
  const msgEl = container.querySelector("#spec-msg");
  const wrap = container.querySelector("#spec-wrap");

  csvBtn.disabled = true;

  loadBtn.addEventListener("click", () => {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    loadSpectrum({ ra, dec, fov, max_frames: maxFrames, name }, { signal: abortCtrl.signal });
  });

  csvBtn.addEventListener("click", () => {
    if (!lastPoints || !lastPoints.length) return;
    const rows = lastPoints.map((p) => [p.wavelength_um, p.bandwidth_um, p.flux_mjy_sr, p.error_mjy_sr, p.mjd, p.detector]);
    const csv = ["wavelength_um,bandwidth_um,flux_mjy_sr,error_mjy_sr,mjd,detector", ...rows.map((r) => r.join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `spectrum_${name.replace(/\s+/g, "_")}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  });

  if (ra && dec) loadBtn.click();
}

async function loadSpectrum(params, opts = {}) {
  const wrap = document.querySelector("#spec-wrap");
  const infoEl = document.querySelector("#spec-info");
  const countEl = document.querySelector("#spec-count");
  const msgEl = document.querySelector("#spec-msg");
  const csvBtn = document.querySelector("#spec-csv-btn");
  wrap.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Extracting spectrum…</div>`;
  csvBtn.disabled = true;
  try {
    const q = new URLSearchParams({ ra: params.ra, dec: params.dec, fov: params.fov, max_frames: params.max_frames, name: params.name });
    const res = await fetchJson(`/api/spectrum?${q.toString()}`, opts.signal);
    if (res.status === 202) {
      msgEl.textContent = "Spectrum is being computed, retrying…";
      setTimeout(() => loadSpectrum(params, opts), 2500);
      return;
    }
    if (!res.ok || !res.data) throw new Error(res.data?.error || "Spectrum unavailable");
    const data = res.data;
    infoEl.textContent = `${data.n_points} points · ${data.wavelength_range_um ? data.wavelength_range_um.map((v) => v.toFixed(2)).join("–") + " µm" : ""}`;
    countEl.textContent = "";
    msgEl.textContent = `Method: ${data.method || "wcs_wave"} · ${data.status}`;
    lastPoints = data.points || [];
    renderSpectrum(data);
    csvBtn.disabled = false;
  } catch (err) {
    if (err.name === "AbortError") return;
    wrap.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
    msgEl.textContent = "";
  }
}

function renderSpectrum(data) {
  const wrap = document.querySelector("#spec-wrap");
  wrap.innerHTML = `<canvas class="spectrum-canvas" width="700" height="300"></canvas>`;
  const canvas = wrap.querySelector("canvas");
  const ctx = canvas.getContext("2d");

  const points = data.points || [];
  if (!points.length) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "rgba(150, 180, 210, 0.55)";
    ctx.font = "400 14px ui-sans-serif, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No spectrum points available.", canvas.width / 2, canvas.height / 2);
    return;
  }

  const wl = points.map((p) => Number(p.wavelength_um) || 0);
  const flux = points.map((p) => (Number(p.flux_jy) || 0) * 1000); // mJy
  const err = points.map((p) => (Number(p.error_jy) || 0) * 1000);

  const wMin = Math.min(...wl) - 0.05;
  const wMax = Math.max(...wl) + 0.05;
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

  const mapX = (v) => x0 + ((v - wMin) / (wMax - wMin)) * (x1 - x0);
  const mapY = (v) => y1 - ((v - fMin) / (fMax - fMin)) * (y1 - y0);

  // Grid
  ctx.strokeStyle = "rgba(80, 115, 150, 0.22)";
  ctx.lineWidth = 1;
  ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.fillStyle = "rgba(140, 170, 200, 0.75)";
  for (let w = 1; w <= 5; w++) {
    if (w < wMin || w > wMax) continue;
    const x = mapX(w);
    ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1); ctx.stroke();
    ctx.fillText(`${w}`, x - 4, y1 + 16);
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

  // Labels
  ctx.fillStyle = "rgba(160, 190, 220, 0.8)";
  ctx.font = "12px ui-sans-serif, system-ui, sans-serif";
  ctx.fillText("Wavelength (µm)", (x0 + x1) / 2 - 42, canvas.height - 8);
  ctx.save();
  ctx.translate(14, (y0 + y1) / 2 + 40);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("Flux (mJy)", 0, 0);
  ctx.restore();

  // Error bars + points
  const mjds = points.map((p) => Number(p.mjd) || 0);
  const mMin = Math.min(...mjds), mMax = Math.max(...mjds);
  const colorFor = (mjd) => {
    const t = mMax > mMin ? (mjd - mMin) / (mMax - mMin) : 0.5;
    const r = Math.round(90 + (255 - 90) * t);
    const g = Math.round(167 + (138 - 167) * t);
    const b = Math.round(255 + (90 - 255) * t);
    return `rgb(${r},${g},${b})`;
  };

  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    const x = mapX(p.wavelength_um);
    const y = mapY(Number(p.flux_jy || 0) * 1000);
    const e = (Number(p.error_jy) || 0) * 1000;
    const yTop = mapY((Number(p.flux_jy || 0) * 1000) + e);
    const yBot = mapY((Number(p.flux_jy || 0) * 1000) - e);
    ctx.strokeStyle = colorFor(Number(p.mjd) || 0);
    ctx.globalAlpha = 0.7;
    ctx.beginPath(); ctx.moveTo(x, yTop); ctx.lineTo(x, yBot); ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.beginPath(); ctx.arc(x, y, 3.2, 0, Math.PI * 2);
    ctx.fillStyle = colorFor(Number(p.mjd) || 0);
    ctx.fill();
  }

  const minWl = Math.min(...wl).toFixed(2);
  const maxWl = Math.max(...wl).toFixed(2);
  document.querySelector("#spec-info").textContent = `${minWl}–${maxWl} µm · ${points.length} points`;
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
  lastPoints = null;
}
