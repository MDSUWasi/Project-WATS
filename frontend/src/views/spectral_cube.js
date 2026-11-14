// Project WATS — spectral cube view (native canvas, no external deps)
// Grid of detector wavelength slices with a slice slider.

import { fetchJson } from "/api.js";
import { escapeHtml } from "/state.js";

let abortCtrl = null;
let slices = [];
let sliceIdx = 0;
let sliceAxis = "wavelength";

export function render(container) {
  const q = new URLSearchParams(location.search);
  const ra = q.get("ra"); const dec = q.get("dec");
  const fov = q.get("fov") || "0.15";
  const maxFrames = q.get("max_frames") || "6";
  const name = q.get("name") || "Custom field";

  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Spectral cube: ${escapeHtml(name)}</h2>
        <div class="coords-meta">${escapeHtml(ra ? `RA ${Number(ra).toFixed(5)}° · Dec ${Number(dec).toFixed(5)}°` : "—")}</div>
      </div>
      <div class="cube-wrap" id="cube-wrap"></div>
      <div class="cube-controls" style="display:flex;gap:10px;align-items:center;margin-top:8px;flex-wrap:wrap;">
        <label>Axis
          <select id="cube-axis">
            <option value="wavelength">Wavelength</option>
            <option value="time">Time (MJD)</option>
          </select>
        </label>
        <label>Slice
          <input type="range" id="cube-slice" min="0" max="0" value="0">
          <span id="cube-slice-val">0</span>
        </label>
        <button class="ghost" id="cube-reset">Reset view</button>
        <span id="cube-info" class="muted" style="margin-left:auto;font-size:12px;"></span>
      </div>
      <div id="cube-msg" style="margin-top:6px;font-size:12px;color:var(--muted-2);"></div>
    </div>
  `;

  const wrap = container.querySelector("#cube-wrap");
  const axisSel = container.querySelector("#cube-axis");
  const sliceInp = container.querySelector("#cube-slice");
  const sliceVal = container.querySelector("#cube-slice-val");
  const resetBtn = container.querySelector("#cube-reset");
  const infoEl = container.querySelector("#cube-info");
  const msgEl = container.querySelector("#cube-msg");

  axisSel.addEventListener("change", (e) => {
    sliceAxis = e.target.value;
    rebuildSlices().then(() => updateSlice(0));
  });
  sliceInp.addEventListener("input", (e) => {
    sliceIdx = Number(e.target.value);
    sliceVal.textContent = String(sliceIdx);
    updateSlice(sliceIdx);
  });
  resetBtn.addEventListener("click", () => {
    updateSlice(0);
  });

  if (ra && dec) {
    loadCube({ ra, dec, fov, max_frames: maxFrames, name }).then(() => {
      rebuildSlices().then(() => updateSlice(0));
    });
  } else {
    msgEl.textContent = "Load a target in the Sky view to build the spectral cube.";
  }
}

async function loadCube(params) {
  const msgEl = document.querySelector("#cube-msg");
  msgEl.textContent = "Fetching frames and building spectral cube…";
  try {
    const q = new URLSearchParams({ ra: params.ra, dec: params.dec, fov: params.fov, max_frames: params.max_frames, name: params.name });
    const res = await fetchJson(`/api/spectrum?${q.toString()}`);
    if (res.status === 202) {
      setTimeout(() => loadCube(params), 2500);
      return;
    }
    if (!res.ok || !res.data) throw new Error(res.data?.error || "Cube data unavailable");
    const data = res.data;
    const points = data.points || [];
    const byDetector = new Map();
    for (const p of points) {
      const d = String(p.detector || 0);
      if (!byDetector.has(d)) byDetector.set(d, []);
      byDetector.get(d).push(p);
    }
    slices = [];
    for (const [d, pts] of byDetector) {
      pts.sort((a, b) => (Number(a.wavelength_um) || 0) - (Number(b.wavelength_um) || 0));
      slices.push({ detector: d, mjd: pts[0] && pts[0].mjd, points: pts });
    }
    slices.sort((a, b) => Number(a.detector) - Number(b.detector));
    msgEl.textContent = `Built ${slices.length} detector slices · ${points.length} spectral points`;
  } catch (err) {
    msgEl.textContent = `Error: ${escapeHtml(String(err))}`;
  }
}

async function rebuildSlices() {
  const wrap = document.querySelector("#cube-wrap");
  const sliceInp = document.querySelector("#cube-slice");
  const infoEl = document.querySelector("#cube-info");
  if (!slices.length) return;
  sliceInp.max = String(slices.length - 1);
  sliceInp.value = "0";
  sliceIdx = 0;
  const sel = sliceAxis === "wavelength" ? "Wavelength" : "Time";
  infoEl.textContent = `${slices.length} slices · axis: ${sel}`;
  renderSlice(wrap, sliceIdx);
}

function renderSlice(wrap, idx) {
  const s = slices[idx];
  if (!s) return;
  const pts = s.points || [];
  const size = 200;
  wrap.innerHTML = `<canvas class="cube-canvas" width="${size}" height="${size}"></canvas>`;
  const canvas = wrap.querySelector("canvas");
  const ctx = canvas.getContext("2d");

  ctx.clearRect(0, 0, size, size);
  ctx.fillStyle = "#050A0F";
  ctx.fillRect(0, 0, size, size);

  if (!pts.length) {
    ctx.fillStyle = "rgba(150, 180, 210, 0.55)";
    ctx.font = "12px ui-sans-serif, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No points", size / 2, size / 2);
    return;
  }

  const wls = pts.map((p) => Number(p.wavelength_um) || 0).filter((v) => Number.isFinite(v));
  const flux = pts.map((p) => Number(p.flux_jy) || 0);
  const wMin = Math.min(...wls), wMax = Math.max(...wls);
  const fMin = Math.min(...flux), fMax = Math.max(...flux);

  // Draw each point as a colored square sized by flux
  const cellW = size / 8;
  const cellH = size / 8;
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    const x = ((Number(p.wavelength_um) || 0) - wMin) / Math.max(wMax - wMin, 1e-9);
    const y = i / Math.max(pts.length - 1, 1);
    const cx = x * (size - cellW) + cellW / 2;
    const cy = y * (size - cellH) + cellH / 2;
    const f = fMax > fMin ? (flux[i] - fMin) / (fMax - fMin) : 0.5;
    const b = 0.4 + 0.6 * f;
    ctx.fillStyle = `rgba(${Math.round(100 + 155 * f)}, ${Math.round(120 + 135 * f)}, ${Math.round(180 + 75 * f)}, ${b})`;
    ctx.fillRect(cx - cellW / 2, cy - cellH / 2, cellW * 0.85, cellH * 0.85);
  }

  // Axis labels
  ctx.fillStyle = "rgba(140, 170, 200, 0.75)";
  ctx.font = "11px ui-sans-serif, system-ui, sans-serif";
  ctx.fillText(`Detector ${s.detector}`, 8, 16);
  ctx.fillText(`Wavelength ${wMin.toFixed(2)}–${wMax.toFixed(2)} µm`, 8, size - 8);
}

function updateSlice(idx) {
  if (!slices.length) return;
  sliceIdx = Math.max(0, Math.min(slices.length - 1, idx));
  const sliceInp = document.querySelector("#cube-slice");
  if (sliceInp) sliceInp.value = String(sliceIdx);
  const sliceVal = document.querySelector("#cube-slice-val");
  if (sliceVal) sliceVal.textContent = String(sliceIdx);
  const wrap = document.querySelector("#cube-wrap");
  renderSlice(wrap, sliceIdx);
  const infoEl = document.querySelector("#cube-info");
  if (infoEl) {
    const s = slices[sliceIdx];
    const n = s.points ? s.points.length : 0;
    infoEl.textContent = `Slice ${sliceIdx + 1} / ${slices.length} · detector ${s.detector} · ${n} points`;
  }
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
  slices = [];
}
