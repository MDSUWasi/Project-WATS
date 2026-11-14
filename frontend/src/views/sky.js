// Project WATS — sky / blink view
// Extracted from main.js; renders the blink comparator into a container.

import { fetchJson } from "/api.js";
import {
  clamp, lerp, fmt, fmtSci, fmtTime, fmtDeg, parseRa, parseDec,
  escapeHtml, FALLBACK_PRESETS,
} from "/state.js";

let state = null;
let els = null;
let raf = null;
let pollTimer = null;
let spectrumTimer = null;
let loadToken = 0;
let boundCleanup = [];

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function createShell() {
  return `
    <div class="sky-view" role="region" aria-label="SPHEREx blink comparator">
      <div class="view-toolbar">
        <div>
          <h2 id="target-title">No target loaded</h2>
          <div class="coords" id="target-coords">Enter coordinates above, or pick a preset.</div>
        </div>
        <div class="badges" id="target-badges"><span class="badge" id="visit-pill">—</span></div>
      </div>

      <div class="canvas-wrap" id="canvas-wrap">
        <canvas id="sky" width="720" height="720" aria-label="SPHEREx sky image"></canvas>
        <div class="canvas-hud">
          <div class="compass" aria-hidden="true"><span class="n">N</span><span class="e">E</span><span class="cross"></span></div>
          <div class="scalebar"><span id="scale-bar"></span><span id="scale-label">—</span></div>
        </div>
        <div class="loading" id="loading" hidden>
          <div class="spinner" aria-hidden="true"></div>
          <div class="loading-text" id="loading-text" role="status" aria-live="polite">Loading…</div>
          <div class="progress"><div id="progress-bar"></div></div>
          <button class="ghost" id="cancel-btn" type="button" hidden>Cancel</button>
        </div>
        <div class="error-box" id="error-box" hidden role="alert">
          <strong>Could not load frames.</strong>
          <p id="error-text"></p>
          <button class="ghost" id="retry-btn" type="button">Try again</button>
        </div>
      </div>

      <div class="transport">
        <button class="primary" id="play" type="button" aria-label="Play animation">Play</button>
        <button class="icon" id="prev" type="button" title="Previous frame" aria-label="Previous frame">◀</button>
        <button class="icon" id="next" type="button" title="Next frame" aria-label="Next frame">▶</button>
        <input type="range" id="frame-slider" min="0" max="0" value="0" aria-label="Frame index">
        <span class="counter" id="frame-counter">0 / 0</span>
      </div>

      <div class="controls">
        <label>Speed <input type="range" id="speed" min="1" max="12" value="4"><span id="speed-label">4 fps</span></label>
        <label class="check"><input type="checkbox" id="loop" checked> Loop</label>
        <label class="check"><input type="checkbox" id="blink"> Blink A/B</label>
        <label class="check"><input type="checkbox" id="subtract"> Subtract</label>
        <label class="check"><input type="checkbox" id="sum"> Sum all</label>
        <label class="check"><input type="checkbox" id="color" checked> Colour composite</label>
        <label class="check"><input type="checkbox" id="background" checked> Background-subtracted</label>
        <label class="check"><input type="checkbox" id="gaia-toggle"> Gaia</label>
        <label class="check"><input type="checkbox" id="catwise-toggle"> CatWISE</label>
        <label>Stretch <input type="range" id="stretch" min="0.5" max="4" step="0.1" value="1.5"><span id="stretch-label">1.5</span></label>
      </div>

      <div class="sky-lower">
        <section class="panel sky-meta-panel">
          <div class="panel-head"><h3>Frame metadata</h3><span id="overlay-info" class="muted">—</span></div>
          <div id="frame-meta" class="meta-grid"><div class="meta-empty">Load a target to see observation metadata.</div></div>
        </section>
        <section class="panel sky-spectrum-panel">
          <div class="panel-head"><h3>Spectrum at target</h3><span id="spectrum-info" class="muted">—</span><span id="spectrum-count" class="muted"></span></div>
          <canvas id="spectrum" width="700" height="260" aria-label="Spectrum at target position"></canvas>
        </section>
        <details class="panel sky-overlay-panel">
          <summary>Catalog overlays</summary>
          <table id="overlay-table"><thead><tr><th>Catalog</th><th>RA</th><th>Dec</th><th>Mag</th><th>PM (mas/yr)</th></tr></thead><tbody><tr><td colspan="5" class="muted">No overlay sources in this field.</td></tr></tbody></table>
        </details>
      </div>

      <p class="hint controls-hint">
        Tip: press <kbd>Space</kbd> to play/pause, <kbd>←</kbd>/<kbd>→</kbd> to step.
        Subtract shows <em>this frame minus the previous frame</em>; a moving object leaves a positive and negative pair.
      </p>
    </div>
  `;
}

export function render(container) {
  container.innerHTML = createShell();
  initState();
  bindEvents();
  const hasUrl = applyUrl();
  if (!hasUrl) {
    $("#ra").value = "133.795096";
    $("#dec").value = "-7.245147";
    $("#fov").value = "0.15";
    $("#max-frames").value = "6";
  }
  drawPlaceholder();
  drawSpectrum();
  if (hasUrl) startLoad(new URLSearchParams(location.search).get("refresh") === "1");
}

function initState() {
  state = {
    manifest: null, frames: [], idx: 0, playing: false, speed: 4, loop: true,
    blink: false, subtract: false, sum: false, color: true, background: true,
    stretch: 1.5, blinkA: 0, blinkB: 0, blinkPhase: false, raf: null,
    lastTime: 0, acc: 0, spectrum: null, overlays: null, showGaia: false,
    showCatwise: false, images: new Map(), dataCache: new Map(),
    sumCache: new Map(), subCache: new Map(), statusTimer: null,
    pollTimer: null, spectrumTimer: null, pollFailures: 0, loadToken: 0,
    spectrumRetries: 0, loading: false, key: null, params: null,
    lastRendered: -1,
  };
  els = {
    ra: $("#ra"), dec: $("#dec"), fov: $("#fov"), maxFrames: $("#max-frames"),
    detector: $("#detector"), targetTitle: $("#target-title"), targetCoords: $("#target-coords"),
    targetBadges: $("#target-badges"), frameSlider: $("#frame-slider"), frameCounter: $("#frame-counter"),
    canvas: $("#sky"), spectrumCanvas: $("#spectrum"), loading: $("#loading"),
    loadingText: $("#loading-text"), progressBar: $("#progress-bar"), cancelBtn: $("#cancel-btn"),
    errorBox: $("#error-box"), errorText: $("#error-text"), retryBtn: $("#retry-btn"),
    play: $("#play"), prev: $("#prev"), next: $("#next"), speed: $("#speed"),
    speedLabel: $("#speed-label"), loop: $("#loop"), blink: $("#blink"), subtract: $("#subtract"),
    sum: $("#sum"), color: $("#color"), background: $("#background"), stretch: $("#stretch"),
    stretchLabel: $("#stretch-label"), gaiaToggle: $("#gaia-toggle"), catwiseToggle: $("#catwise-toggle"),
    copyLink: $("#copy-link"), visitPill: $("#visit-pill"), frameMeta: $("#frame-meta"),
    spectrumCount: $("#spectrum-count"), spectrumInfo: $("#spectrum-info"),
    overlayInfo: $("#overlay-info"), overlayTable: $("#overlay-table"),
    scaleBar: $("#scale-bar"), scaleLabel: $("#scale-label"),
  };
}

export function destroy() {
  stopPlayback();
  if (raf) cancelAnimationFrame(raf);
  if (pollTimer) clearTimeout(pollTimer);
  if (spectrumTimer) clearTimeout(spectrumTimer);
  raf = null; pollTimer = null; spectrumTimer = null;
  for (const cleanup of boundCleanup) { try { cleanup(); } catch (_) { /* ignore */ } }
  boundCleanup = [];
  state = null; els = null;
  delete window.__watsLoadFrames;
  delete window.__watsCancelFetch;
}

window.__watsLoadFrames = startLoad;
window.__watsCancelFetch = cancelFetch;

// ---- pixel rendering (same as main.js) ----
const work = document.createElement("canvas");
const workCtx = work.getContext("2d", { willReadFrequently: true });
const native = document.createElement("canvas");
const nativeCtx = native.getContext("2d", { willReadFrequently: true });

function frameFileUrl(frame, field) {
  if (!frame) return null;
  const direct = frame[`${field}_url`];
  if (direct) return direct;
  const file = frame[field];
  if (!file) return null;
  const key = state.manifest && state.manifest.key;
  return key ? `/frames/${key}/${file}` : null;
}

function imageUrl(frame, mode) {
  const field = mode === "gray" ? "gray" : mode === "graySub" ? "gray_sub"
    : mode === "color" ? "color" : mode === "colorSub" ? "color_sub" : null;
  return field ? frameFileUrl(frame, field) : null;
}

function loadImage(url) {
  if (!url) return Promise.resolve(null);
  if (state.images.has(url)) return Promise.resolve(state.images.get(url));
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => { state.images.set(url, img); resolve(img); };
    img.onerror = () => { state.images.set(url, null); resolve(null); };
    img.src = url;
  });
}

async function preloadFrames(onProgress) {
  const n = state.manifest ? state.manifest.naxis : 320;
  native.width = n; native.height = n;
  const jobs = [];
  for (let i = 0; i < state.frames.length; i++) {
    const f = state.frames[i];
    for (const mode of ["gray", "graySub", "color", "colorSub"]) {
      const url = imageUrl(f, mode);
      if (url) jobs.push(loadImage(url));
    }
  }
  let done = 0;
  const total = jobs.length;
  await Promise.all(jobs.map((p) => p.then(() => { done += 1; if (onProgress) onProgress(done, total); })));
}

function modeForDisplay() {
  if (state.color) return state.background ? "colorSub" : "color";
  return state.background ? "graySub" : "gray";
}

function displayIndex() {
  if (!state.frames.length) return 0;
  if (state.sum || state.subtract) return clamp(state.idx, 0, state.frames.length - 1);
  if (state.blink) return clamp(state.blinkPhase ? state.blinkB : state.blinkA, 0, state.frames.length - 1);
  return clamp(state.idx, 0, state.frames.length - 1);
}

function getData(i, mode) {
  const key = `${i}:${mode}`;
  if (state.dataCache.has(key)) return state.dataCache.get(key);
  if (state.dataCache.size > 260) state.dataCache.clear();
  const frame = state.frames[i];
  const url = imageUrl(frame, mode);
  const img = url ? state.images.get(url) : null;
  if (!img) return null;
  const n = state.manifest ? state.manifest.naxis : img.naturalWidth || 320;
  native.width = n; native.height = n;
  nativeCtx.clearRect(0, 0, n, n);
  nativeCtx.drawImage(img, 0, 0, n, n);
  let data;
  try { data = nativeCtx.getImageData(0, 0, n, n); } catch (_) { return null; }
  state.dataCache.set(key, data);
  return data;
}

function getSubtract(i, mode) {
  const b = getData(Math.max(0, i - 1), mode);
  const a = getData(i, mode);
  if (!a) return null;
  if (!b) return a;
  const cacheKey = `${i}:${mode}:${state.stretch.toFixed(2)}`;
  if (state.subCache.has(cacheKey)) return state.subCache.get(cacheKey);
  const n = a.width;
  const out = new ImageData(new Uint8ClampedArray(a.data.length), a.width, a.height);
  const gain = 1.6 * state.stretch;
  for (let p = 0; p < a.data.length; p += 4) {
    out.data[p] = clamp(128 + (a.data[p] - b.data[p]) * gain, 0, 255);
    out.data[p + 1] = clamp(128 + (a.data[p + 1] - b.data[p + 1]) * gain, 0, 255);
    out.data[p + 2] = clamp(128 + (a.data[p + 2] - b.data[p + 2]) * gain, 0, 255);
    out.data[p + 3] = 255;
  }
  state.subCache.set(cacheKey, out);
  return out;
}

function getSum(mode) {
  if (state.sumCache.has(mode)) return state.sumCache.get(mode);
  if (!state.frames.length) return null;
  let acc = null; let n = 0;
  for (let i = 0; i < state.frames.length; i++) {
    const d = getData(i, mode);
    if (!d) continue;
    if (!acc) { acc = new Float32Array(d.data.length); acc.width = d.width; acc.height = d.height; }
    n += 1;
    for (let p = 0; p < d.data.length; p += 4) { acc[p] += d.data[p]; acc[p + 1] += d.data[p + 1]; acc[p + 2] += d.data[p + 2]; }
  }
  if (!acc || !n) return null;
  const out = new ImageData(new Uint8ClampedArray(acc.length), acc.width, acc.height);
  for (let p = 0; p < acc.length; p += 4) { out.data[p] = acc[p] / n; out.data[p + 1] = acc[p + 1] / n; out.data[p + 2] = acc[p + 2] / n; out.data[p + 3] = 255; }
  state.sumCache.set(mode, out);
  return out;
}

function applyStretch(data) {
  if (!data) return null;
  if (state.subtract) return data;
  const out = new ImageData(new Uint8ClampedArray(data.data.length), data.width, data.height);
  const gamma = 1 / Math.max(0.2, state.stretch);
  for (let p = 0; p < data.data.length; p += 4) {
    out.data[p] = 255 * Math.pow(clamp(data.data[p] / 255, 0, 1), gamma);
    out.data[p + 1] = 255 * Math.pow(clamp(data.data[p + 1] / 255, 0, 1), gamma);
    out.data[p + 2] = 255 * Math.pow(clamp(data.data[p + 2] / 255, 0, 1), gamma);
    out.data[p + 3] = 255;
  }
  return out;
}

function drawPlaceholder() {
  const c = els.canvas; const ctx = c.getContext("2d");
  const w = c.width; const h = c.height;
  ctx.fillStyle = "#03070b";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "rgba(160, 190, 220, 0.55)";
  ctx.font = "500 22px ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText("Load a target to see the infrared sky", w / 2, h / 2 - 8);
  ctx.font = "400 15px ui-sans-serif, system-ui, sans-serif";
  ctx.fillStyle = "rgba(130, 160, 190, 0.45)";
  ctx.fillText("SPHEREx · 0.75–5 µm · blink comparator", w / 2, h / 2 + 22);
  ctx.textAlign = "left";
}

function renderFrame() {
  if (!state.manifest || !state.frames.length) { drawPlaceholder(); updateMeta(); return; }
  const n = state.manifest.naxis || 320;
  const c = els.canvas;
  if (c.width !== 720) { c.width = 720; c.height = 720; }
  work.width = n; work.height = n;
  let data = null;
  const shown = displayIndex();
  if (state.sum) data = getSum(modeForDisplay());
  else if (state.subtract) data = getSubtract(shown, modeForDisplay());
  else data = getData(shown, modeForDisplay());
  data = applyStretch(data);
  if (!data) { drawPlaceholder(); updateMeta(); return; }
  workCtx.putImageData(data, 0, 0);
  const ctx = c.getContext("2d");
  ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
  ctx.clearRect(0, 0, c.width, c.height);
  ctx.drawImage(work, 0, 0, c.width, c.height);
  drawOverlays(n); drawCrosshair(); drawTimestamp(n);
  if (state.lastRendered !== shown) {
    state.lastRendered = shown; updateMeta(); drawSpectrum();
  }
}

function drawCrosshair() {
  const c = els.canvas; const ctx = c.getContext("2d");
  const w = c.width; const h = c.height;
  ctx.save();
  ctx.strokeStyle = "rgba(220, 238, 255, 0.32)";
  ctx.lineWidth = 1; ctx.setLineDash([5, 5]);
  ctx.beginPath(); ctx.moveTo(w / 2, 0); ctx.lineTo(w / 2, h);
  ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();
  ctx.restore();
}

function drawTimestamp(n) {
  const frame = state.frames[displayIndex()];
  if (!frame) return;
  const lines = [];
  lines.push(`${frame.time || ""}`.replace("T", " ").replace("Z", " UTC"));
  lines.push(`${(frame.detectors || []).join(", ")} · ${frame.n_channels || 0} band${frame.n_channels === 1 ? "" : "s"}`);
  const c = els.canvas; const ctx = c.getContext("2d");
  const w = c.width;
  ctx.save();
  ctx.font = "600 15px ui-monospace, SFMono-Regular, Menlo, monospace";
  const text = lines.join("\n");
  const metrics = ctx.measureText(lines[0]);
  const boxW = Math.max(metrics.width, 260) + 24; const boxH = 52;
  ctx.fillStyle = "rgba(3, 8, 13, 0.66)";
  ctx.strokeStyle = "rgba(150, 190, 230, 0.18)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  if (ctx.roundRect) ctx.roundRect(10, 10, boxW, boxH, 8); else ctx.rect(10, 10, boxW, boxH);
  ctx.fill(); ctx.stroke();
  ctx.fillStyle = "rgba(220, 238, 255, 0.92)";
  ctx.fillText(lines[0], 22, 32);
  ctx.font = "500 13px ui-sans-serif, system-ui, sans-serif";
  ctx.fillStyle = "rgba(150, 185, 220, 0.8)";
  ctx.fillText(lines[1], 22, 51);
  ctx.restore();
}

function drawOverlays(n) {
  if (!state.overlays) return;
  const c = els.canvas; const ctx = c.getContext("2d");
  const sx = c.width / n; const sy = c.height / n;
  const drawSet = (items, color, show, label) => {
    if (!show || !items) return;
    const inside = items.filter((s) => s && s.inside && Number.isFinite(s.x) && Number.isFinite(s.y));
    inside.sort((a, b) => (a.mag ?? a.w1 ?? 99) - (b.mag ?? b.w1 ?? 99));
    const shown = inside.slice(0, 120);
    ctx.save();
    for (let i = 0; i < shown.length; i++) {
      const s = shown[i];
      const mag = Number(s.mag ?? s.w1 ?? 15);
      if (!Number.isFinite(mag)) continue;
      const r = clamp(9.5 - (mag - 8) * 0.45, 2.5, 8);
      const x = (s.x + 0.5) * sx; const y = (s.y + 0.5) * sy;
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.strokeStyle = color; ctx.lineWidth = 1.4; ctx.stroke();
      if (i < 6) {
        ctx.font = "500 11px ui-sans-serif, system-ui, sans-serif";
        ctx.fillStyle = color;
        ctx.fillText(`${label} ${mag.toFixed(1)}`, x + r + 4, y + 3);
      }
    }
    ctx.restore();
  };
  drawSet(state.overlays.gaia, "#67b7ff", state.showGaia, "G");
  drawSet(state.overlays.catwise, "#ffa94d", state.showCatwise, "W");
}

function updateMeta() {
  const idx = displayIndex();
  const f = state.frames[idx];
  const pill = els.visitPill; const meta = els.frameMeta;
  if (!f) { pill.textContent = "—"; meta.innerHTML = '<div class="meta-empty">Load a target to see observation metadata.</div>'; return; }
  pill.textContent = state.sum ? `Sum of ${state.frames.length} visit${state.frames.length === 1 ? "" : "s"}` : `Visit ${idx + 1} / ${state.frames.length}`;
  const wls = f.wavelengths_um || {};
  const wlText = Object.keys(wls).length ? Object.entries(wls).map(([k, v]) => `${k}: ${Number(v).toFixed(2)}`).join(" · ") : (f.wavelength_um ? `${Number(f.wavelength_um).toFixed(2)} µm` : "—");
  const band = Array.isArray(f.bandpass_um) ? `${Number(f.bandpass_um[0]).toFixed(2)}–${Number(f.bandpass_um[1]).toFixed(2)} µm` : "—";
  const items = [
    ["Time (UTC)", fmtTime(f.time)], ["Visit", f.obs_base || f.obs_id || "—"],
    ["Detector(s)", (f.detectors || []).join(", ") || "—"],
    ["Wavelength at target", wlText], ["Detector bandpass", band],
    ["Exposure", f.exptime ? `${Number(f.exptime).toFixed(1)} s` : "—"],
    ["Background", f.background_mjy_sr !== undefined ? `${fmt(f.background_mjy_sr, 3)} MJy/sr` : "—"],
    ["Noise (MAD)", f.noise_mjy_sr !== undefined ? `${fmt(f.noise_mjy_sr, 4)} MJy/sr` : "—"],
    ["Peak", f.peak_mjy_sr !== undefined ? `${fmt(f.peak_mjy_sr, 2)} MJy/sr` : "—"],
    ["Flagged pixels", f.flags_bad_fraction !== undefined ? `${(100 * f.flags_bad_fraction).toFixed(1)}%` : "—"],
    ["Release / version", [f.release, f.version].filter(Boolean).join(" · ") || "—"],
  ];
  meta.innerHTML = items.map(([k, v]) => `<div class="meta-item"><div class="k">${escapeHtml(k)}</div><div class="v">${escapeHtml(v)}</div></div>`).join("");
}

function drawSpectrum() {
  const c = els.spectrumCanvas; if (!c) return;
  const ctx = c.getContext("2d");
  const w = c.width; const h = c.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#080d13"; ctx.fillRect(0, 0, w, h);
  const ml = 62, mr = 18, mt = 20, mb = 42;
  const x0 = ml, x1 = w - mr, y0 = mt, y1 = h - mb;
  const points = ((state.spectrum && state.spectrum.points) || []).filter((p) => Number.isFinite(Number(p.wavelength_um)) && Number(p.wavelength_um) > 0);
  const allPoints = (state.spectrum && state.spectrum.points) || [];
  if (els.spectrumCount) els.spectrumCount.textContent = allPoints.length ? `${allPoints.length} points` : "";
  if (!points.length) {
    ctx.fillStyle = "rgba(150, 180, 210, 0.55)";
    ctx.font = "400 14px ui-sans-serif, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Spectrum appears when frames are ready.", w / 2, h / 2);
    ctx.textAlign = "left";
    return;
  }
  const wlMin = 0.75, wlMax = 5.0;
  const vals = points.map((p) => (Number(p.flux_jy) || 0) * 1000);
  const errs = points.map((p) => (Number(p.error_jy) || 0) * 1000);
  let yMin = Math.min(0, ...vals.map((v, i) => v - errs[i]));
  let yMax = Math.max(0, ...vals.map((v, i) => v + errs[i]));
  if (yMax - yMin < 1e-9) { yMin = -1; yMax = 1; }
  const pad = (yMax - yMin) * 0.12; yMin -= pad; yMax += pad;
  const mapX = (wl) => x0 + ((wl - wlMin) / (wlMax - wlMin)) * (x1 - x0);
  const mapY = (v) => y1 - ((v - yMin) / (yMax - yMin)) * (y1 - y0);
  ctx.strokeStyle = "rgba(80, 115, 150, 0.22)";
  ctx.lineWidth = 1;
  ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.fillStyle = "rgba(140, 170, 200, 0.75)";
  for (let wl = 1; wl <= 5; wl++) {
    const x = mapX(wl);
    ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1); ctx.stroke();
    ctx.fillText(`${wl}`, x - 3, y1 + 16);
  }
  const yTicks = 5;
  for (let i = 0; i <= yTicks; i++) {
    const v = lerp(yMin, yMax, i / yTicks); const y = mapY(v);
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    ctx.fillText(fmt(v, 2), 6, y + 4);
  }
  ctx.strokeStyle = "rgba(140, 175, 210, 0.5)";
  ctx.beginPath(); ctx.moveTo(x0, y1); ctx.lineTo(x1, y1); ctx.moveTo(x0, y0); ctx.lineTo(x0, y1); ctx.stroke();
  ctx.fillStyle = "rgba(160, 190, 220, 0.8)";
  ctx.font = "12px ui-sans-serif, system-ui, sans-serif";
  ctx.fillText("Wavelength (µm)", (x0 + x1) / 2 - 42, h - 8);
  ctx.save();
  ctx.translate(14, (y0 + y1) / 2 + 40);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("Flux (mJy)", 0, 0);
  ctx.restore();
  const currentIdx = displayIndex();
  const current = points.filter((p) => p.frame === currentIdx);
  if (current.length) {
    const wl = current.map((p) => p.wavelength_um);
    const lo = mapX(Math.min(...wl) - 0.04);
    const hi = mapX(Math.max(...wl) + 0.04);
    ctx.fillStyle = "rgba(79, 157, 255, 0.12)";
    ctx.fillRect(lo, y0, Math.max(2, hi - lo), y1 - y0);
  }
  const mjds = points.map((p) => Number(p.mjd) || 0);
  const mMin = Math.min(...mjds), mMax = Math.max(...mjds);
  const colorFor = (mjd) => {
    const t = mMax > mMin ? (mjd - mMin) / (mMax - mMin) : 0.5;
    const r = Math.round(lerp(90, 255, t));
    const g = Math.round(lerp(167, 138, t));
    const b = Math.round(lerp(255, 90, t));
    return `rgb(${r},${g},${b})`;
  };
  for (const p of points) {
    const x = mapX(Number(p.wavelength_um));
    const y = mapY((Number(p.flux_jy) || 0) * 1000);
    const e = (Number(p.error_jy) || 0) * 1000;
    const yTop = mapY((Number(p.flux_jy) || 0) * 1000 + e);
    const yBot = mapY((Number(p.flux_jy) || 0) * 1000 - e);
    ctx.strokeStyle = colorFor(Number(p.mjd) || 0);
    ctx.globalAlpha = 0.7;
    ctx.beginPath(); ctx.moveTo(x, yTop); ctx.lineTo(x, yBot); ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.beginPath(); ctx.arc(x, y, p.frame === currentIdx ? 5 : 3.2, 0, Math.PI * 2);
    ctx.fillStyle = colorFor(Number(p.mjd) || 0); ctx.fill();
    if (p.frame === currentIdx) {
      ctx.strokeStyle = "#dceeff"; ctx.lineWidth = 1.5; ctx.stroke();
    }
  }
  const wl = points.map((p) => p.wavelength_um);
  if (els.spectrumInfo) els.spectrumInfo.textContent = `${Math.min(...wl).toFixed(2)}–${Math.max(...wl).toFixed(2)} µm`;
}

function updateOverlayTable() {
  const body = els.overlayTable.querySelector("tbody");
  const rows = [];
  if (state.overlays) {
    for (const s of state.overlays.gaia || []) { if (!s.inside) continue; rows.push({ cat: "Gaia", ra: s.ra, dec: s.dec, mag: s.mag, pm: Math.hypot(Number(s.pmra) || 0, Number(s.pmdec) || 0) }); }
    for (const s of state.overlays.catwise || []) { if (!s.inside) continue; rows.push({ cat: "CatWISE", ra: s.ra, dec: s.dec, mag: s.w1, pm: Math.hypot(Number(s.pmra) || 0, Number(s.pmdec) || 0) }); }
  }
  rows.sort((a, b) => (a.mag ?? 99) - (b.mag ?? 99));
  const top = rows.slice(0, 30);
  if (!top.length) { body.innerHTML = '<tr><td colspan="5" class="muted">No overlay sources in this field.</td></tr>'; return; }
  body.innerHTML = top.map((r) => {
    const raTxt = Number.isFinite(Number(r.ra)) ? Number(r.ra).toFixed(5) : "—";
    const decTxt = Number.isFinite(Number(r.dec)) ? Number(r.dec).toFixed(5) : "—";
    const magTxt = Number.isFinite(Number(r.mag)) ? Number(r.mag).toFixed(2) : "—";
    const pmTxt = Number.isFinite(Number(r.pm)) && Number(r.pm) > 0 ? Number(r.pm).toFixed(1) : "—";
    return `<tr><td>${r.cat}</td><td>${raTxt}</td><td>${decTxt}</td><td>${magTxt}</td><td>${pmTxt}</td></tr>`;
  }).join("");
}

// ---- loading targets ----
function readForm() {
  return {
    ra: els.ra.value.trim(), dec: els.dec.value.trim(),
    fov: Number(els.fov.value), max_frames: Number(els.maxFrames.value),
    detector: els.detector.value || "",
    name: els.targetTitle.textContent && els.targetTitle.textContent !== "No target loaded" ? els.targetTitle.textContent : "custom",
  };
}

function paramsFromForm() {
  const f = readForm();
  const ra = parseRa(f.ra); const dec = parseDec(f.dec);
  if (!Number.isFinite(ra) || !Number.isFinite(dec)) return null;
  return { ra, dec, fov: f.fov, max_frames: f.max_frames, detector: f.detector || "", name: f.name || "custom" };
}

function updateUrl(params, extra = {}) {
  if (!params) return;
  const q = new URLSearchParams();
  q.set("ra", Number(params.ra).toFixed(6)); q.set("dec", Number(params.dec).toFixed(6));
  q.set("fov", String(params.fov)); q.set("max_frames", String(params.max_frames));
  if (params.detector) q.set("detector", params.detector);
  if (params.name && params.name !== "custom") q.set("name", params.name);
  q.set("color", state.color ? "1" : "0");
  if (state.subtract) q.set("subtract", "1");
  if (state.blink) q.set("blink", "1");
  if (state.sum) q.set("sum", "1");
  if (!state.background) q.set("background", "0");
  q.set("stretch", state.stretch.toFixed(1));
  for (const [k, v] of Object.entries(extra)) if (v !== undefined && v !== null) q.set(k, v);
  history.replaceState(null, "", `${location.pathname}?${q.toString()}`);
}

function syncUrl() { if (state.params) updateUrl(state.params, state.key ? { key: state.key } : {}); }

function resetForLoad() {
  loadToken += 1; state.pollFailures = 0; state.spectrumRetries = 0;
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  if (spectrumTimer) { clearTimeout(spectrumTimer); spectrumTimer = null; }
  state.manifest = null; state.frames = []; state.spectrum = null; state.overlays = null;
  state.idx = 0; state.images.clear(); state.dataCache.clear(); state.sumCache.clear(); state.subCache.clear();
  state.lastRendered = -1;
  els.frameSlider.max = "0"; els.frameSlider.value = "0";
  els.frameCounter.textContent = "0 / 0";
  els.targetTitle.textContent = "Loading…";
  els.targetCoords.textContent = "";
  els.targetBadges.innerHTML = "";
  els.frameMeta.innerHTML = '<div class="meta-empty">Fetching SPHEREx frames…</div>';
  els.spectrumInfo.textContent = "Extracting…";
  els.overlayInfo.textContent = "Querying Gaia / CatWISE…";
  els.overlayTable.querySelector("tbody").innerHTML = '<tr><td colspan="5" class="muted">Querying catalogues…</td></tr>';
  drawPlaceholder(); drawSpectrum();
}

async function startLoad(useRefresh = false) {
  const params = paramsFromForm();
  if (!params) { showError("Enter a valid Right Ascension and Declination."); return; }
  if (useRefresh) params.refresh = true;
  state.params = params; state.key = null;
  updateUrl(params); resetForLoad();
  const token = loadToken;
  showLoading("Querying IRSA for SPHEREx images…", 0, 1);
  try {
    const q = new URLSearchParams({ ra: params.ra.toFixed(7), dec: params.dec.toFixed(7), fov: String(params.fov), max_frames: String(params.max_frames), name: params.name });
    if (params.detector) q.set("detector", params.detector);
    if (useRefresh) q.set("refresh", "1");
    const res = await fetchJson(`/api/frames?${q.toString()}`);
    if (token !== loadToken) return;
    if (res.status === 202 && res.data && res.data.key) {
      state.key = res.data.key;
      showLoading(res.data.message || "Fetching cutouts from IRSA…", 0, res.data.total || 1);
      pollStatus();
    } else if (res.ok && res.data && res.data.status === "ready") {
      onManifest(res.data);
    } else {
      showError((res.data && (res.data.error || res.data.message)) || "IRSA did not return frames.");
    }
  } catch (err) { showError(String(err)); }
}

function showError(message) {
  state.loading = false; els.loading.hidden = true;
  els.errorText.textContent = message || "Unknown error.";
  els.errorBox.hidden = false;
}

function clearError() { els.errorBox.hidden = true; }

function showLoading(text, progress, total) {
  state.loading = true; clearError();
  els.loading.hidden = false;
  els.loadingText.textContent = text || "Loading…";
  const pct = total > 0 ? clamp((progress / total) * 100, 0, 100) : 0;
  els.progressBar.style.width = `${pct}%`;
  els.cancelBtn.hidden = !state.key;
}

function hideLoading() { state.loading = false; els.loading.hidden = true; }

function pollStatus() {
  if (!state.key) return;
  const key = state.key; const token = loadToken;
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (!state.key || key !== state.key) return;
    const res = await fetchJson(`/api/status?key=${encodeURIComponent(key)}`);
    if (token !== loadToken || key !== state.key) return;
    if (!res.ok || !res.data) { state.pollFailures += 1; if (state.pollFailures >= 5) { state.key = null; hideLoading(); showError("Lost contact with the server. Please try again."); return; } pollStatus(); return; }
    state.pollFailures = 0;
    if (res.data.status === "ready") {
      if (res.data.manifest) onManifest(res.data.manifest);
      else { state.key = null; hideLoading(); showError("Frames ready but manifest unreadable."); }
      return;
    }
    if (res.data.status === "cancelled") { state.key = null; hideLoading(); showError("Fetch cancelled."); return; }
    if (res.data.status === "error") { state.key = null; hideLoading(); showError(res.data.error || res.data.message || "Fetch failed."); return; }
    if (res.data.status === "idle") { state.key = null; hideLoading(); showError("Fetch no longer running. Please try again."); return; }
    const progress = res.data.progress || 0; const total = res.data.total || 1;
    showLoading(res.data.message || "Fetching SPHEREx cutouts…", progress, total);
    pollStatus();
  }, 1800);
}

async function cancelFetch() {
  if (!state.key) return;
  const key = state.key; loadToken += 1;
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  if (spectrumTimer) { clearTimeout(spectrumTimer); spectrumTimer = null; }
  await fetchJson(`/api/cancel?key=${encodeURIComponent(key)}`);
  state.key = null; hideLoading();
  showError("Fetch cancelled. Try again with fewer frames, or press Retry.");
}

async function onManifest(manifest) {
  const token = loadToken;
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  if (spectrumTimer) { clearTimeout(spectrumTimer); spectrumTimer = null; }
  state.pollFailures = 0; state.spectrumRetries = 0;
  state.manifest = manifest;
  const frames = Array.isArray(manifest.frames) ? manifest.frames : [];
  state.frames = frames; state.key = manifest.key || state.key;
  state.idx = 0; state.blinkA = 0; state.blinkB = Math.max(0, frames.length - 1);
  state.sumCache.clear(); state.subCache.clear(); state.dataCache.clear();
  els.targetTitle.textContent = manifest.name || "SPHEREx field";
  if (manifest.name) state.params.name = manifest.name;
  els.targetCoords.textContent = fmtDeg(manifest.ra, manifest.dec);
  const badges = [];
  const visits = Number(manifest.visits) || frames.length;
  const visitText = `${visits} visit${visits === 1 ? "" : "s"}`;
  badges.push(`<span class="badge" id="visit-pill">${escapeHtml(visitText)}</span>`);
  badges.push(`<span class="badge">${(manifest.detectors || []).length} detector${(manifest.detectors || []).length === 1 ? "" : "s"}</span>`);
  const tr = Array.isArray(manifest.time_range) && manifest.time_range.length >= 2 ? manifest.time_range : null;
  if (tr) badges.push(`<span class="badge">${String(tr[0]).slice(0, 10)} → ${String(tr[1]).slice(0, 10)}</span>`);
  if (manifest.query_rows) badges.push(`<span class="badge">${manifest.query_rows} MEFs</span>`);
  els.targetBadges.innerHTML = badges.join("");
  els.visitPill = $("#visit-pill");
  if (state.key) updateUrl(state.params, { key: state.key });
  els.frameSlider.max = String(Math.max(0, frames.length - 1));
  els.frameSlider.value = "0";
  els.frameCounter.textContent = `0 / ${frames.length}`;
  const fov = Number(manifest.fov_deg) || 0.15;
  const n = Number(manifest.naxis) || 320;
  const barPx = 90;
  const arcsec = fov * 3600 * (barPx / 720);
  const scaleLabel = arcsec >= 60 ? `${(arcsec / 60).toFixed(1)}′` : `${arcsec.toFixed(1)}″`;
  els.scaleBar.style.width = `${barPx}px`;
  els.scaleLabel.textContent = scaleLabel;
  showLoading("Loading rendered frames…", 0, 1);
  await preloadFrames((done, total) => {
    if (token !== loadToken) return;
    const pct = total ? Math.round((done / total) * 100) : 0;
    showLoading(`Loading rendered frames… ${pct}%`, done, total);
  });
  if (token !== loadToken) return;
  hideLoading();
  setFrame(0); startPlayback(); loadSpectrum(); loadOverlays();
}

// ---- playback ----
function setFrame(i) {
  if (!state.frames.length) return;
  const n = state.frames.length;
  state.idx = clamp(Number(i) || 0, 0, n - 1);
  els.frameSlider.value = String(state.idx);
  els.frameCounter.textContent = `${state.idx + 1} / ${n}`;
  if (state.blink) state.blinkB = state.idx;
  renderFrame(); drawSpectrum();
}

function startPlayback() {
  if (raf) cancelAnimationFrame(raf);
  if (!state.frames.length || state.sum) { state.playing = false; els.play.textContent = "Play"; els.play.setAttribute("aria-label", "Play animation"); return; }
  state.playing = true; els.play.textContent = "Pause"; els.play.setAttribute("aria-label", "Pause animation");
  state.lastTime = performance.now(); state.acc = 0;
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) { state.playing = false; els.play.textContent = "Play"; return; }
  const tick = (t) => {
    if (!state.playing) return;
    const dt = Math.min(120, t - state.lastTime); state.lastTime = t; state.acc += dt;
    const interval = 1000 / Math.max(0.5, state.speed);
    if (state.blink) {
      if (state.acc >= interval) { state.acc = 0; state.blinkPhase = !state.blinkPhase; renderFrame(); }
    } else {
      if (state.acc >= interval) { state.acc = 0;
        if (state.idx >= state.frames.length - 1) { if (state.loop) setFrame(0); else stopPlayback(); }
        else setFrame(state.idx + 1); }
    }
    raf = requestAnimationFrame(tick);
  };
  raf = requestAnimationFrame(tick);
}

function stopPlayback() { state.playing = false; if (raf) cancelAnimationFrame(raf); raf = null; els.play.textContent = "Play"; els.play.setAttribute("aria-label", "Play animation"); }

function togglePlayback() { if (state.playing) stopPlayback(); else startPlayback(); }

async function loadSpectrum(retry = 0) {
  if (!state.params) return;
  const token = loadToken;
  const q = new URLSearchParams({ ra: state.params.ra.toFixed(7), dec: state.params.dec.toFixed(7), fov: String(state.params.fov), max_frames: String(state.params.max_frames), name: state.params.name });
  if (state.params.detector) q.set("detector", state.params.detector);
  const url = state.key
    ? `/api/targets/${encodeURIComponent(state.key)}/spectrum`
    : `/api/spectrum?${q.toString()}`;
  els.spectrumCount.textContent = retry ? "pending…" : "extracting…";
  const res = await fetchJson(url);
  if (token !== loadToken) return;
  if (res.status === 202) {
    if (retry >= 40) { els.spectrumCount.textContent = "unavailable"; els.spectrumInfo.textContent = "Spectrum is not ready yet."; return; }
    state.spectrumRetries = retry + 1;
    if (spectrumTimer) clearTimeout(spectrumTimer);
    spectrumTimer = setTimeout(() => loadSpectrum(retry + 1), 2500);
    return;
  }
  if (res.ok && res.data) { state.spectrum = res.data; drawSpectrum(); }
  else { els.spectrumCount.textContent = "unavailable"; els.spectrumInfo.textContent = (res.data && (res.data.error || res.data.message)) || ""; }
}

async function loadOverlays() {
  if (!state.params) return;
  const token = loadToken;
  const q = new URLSearchParams({ ra: state.params.ra.toFixed(7), dec: state.params.dec.toFixed(7), fov: String(state.params.fov), naxis: String(state.manifest ? state.manifest.naxis : 320) });
  const res = await fetchJson(`/api/overlays?${q.toString()}`);
  if (token !== loadToken) return;
  if (res.ok && res.data) {
    state.overlays = res.data;
    const g = (res.data.gaia || []).filter((s) => s.inside).length;
    const w = (res.data.catwise || []).filter((s) => s.inside).length;
    const errs = Object.values(res.data.errors || {});
    els.overlayInfo.textContent = errs.length ? `${g} Gaia · ${w} CatWISE · ${errs.length} catalogue error(s)` : `${g} Gaia · ${w} CatWISE in field`;
    updateOverlayTable(); renderFrame();
  } else { els.overlayInfo.textContent = "unavailable"; }
}

// ---- presets / URL ----
function mergePresets(apiPresets) {
  const seen = new Map(); const out = [];
  const add = (p) => {
    const name = String((p && p.name) || "").trim(); if (!name) return;
    const key = name.toLowerCase(); const existing = seen.get(key);
    if (existing) { for (const [k, v] of Object.entries(p || {})) { if (existing[k] === undefined || existing[k] === null || existing[k] === "") existing[k] = v; } return; }
    const entry = { ...(p || {}) }; seen.set(key, entry); out.push(entry);
  };
  for (const p of apiPresets) add(p);
  for (const p of FALLBACK_PRESETS) add(p);
  return out;
}

async function loadPresets() {
  let presets = FALLBACK_PRESETS.slice();
  try {
    const res = await fetchJson("/api/presets");
    if (res.ok && res.data && Array.isArray(res.data.presets) && res.data.presets.length) presets = mergePresets(res.data.presets);
  } catch (_) { /* use fallback */ }
  const wrap = $("#presets");
  if (!wrap) return;
  wrap.innerHTML = '<span class="label">Try</span>';
  for (const p of presets) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "preset";
    b.innerHTML = `${escapeHtml(p.name)}<small>${escapeHtml(p.description || "")}</small>`;
    b.addEventListener("click", () => {
      els.ra.value = typeof p.ra === "number" ? p.ra.toFixed(6) : String(p.ra_text || p.ra);
      els.dec.value = typeof p.dec === "number" ? p.dec.toFixed(6) : String(p.dec_text || p.dec);
      els.fov.value = String(p.fov || 0.15);
      els.targetTitle.textContent = p.name;
      startLoad(false);
    });
    wrap.appendChild(b);
  }
}

function applyUrl() {
  const q = new URLSearchParams(location.search);
  if (q.has("ra") && q.has("dec")) {
    els.ra.value = q.get("ra"); els.dec.value = q.get("dec");
    if (q.has("fov")) {
      const fov = Number(q.get("fov"));
      if ([...els.fov.options].some((o) => Number(o.value) === fov)) els.fov.value = String(fov);
      else { const opt = document.createElement("option"); opt.value = String(fov); opt.textContent = `${(fov * 60).toFixed(1)} arcmin`; els.fov.appendChild(opt); els.fov.value = String(fov); }
    }
    if (q.has("max_frames")) { const mf = String(q.get("max_frames")); if (![...els.maxFrames.options].some((o) => o.value === mf)) { const opt = document.createElement("option"); opt.value = mf; opt.textContent = mf; els.maxFrames.appendChild(opt); } els.maxFrames.value = mf; }
    if (q.has("detector")) { const detRaw = String(q.get("detector")).toUpperCase(); const det = detRaw === "ALL" || detRaw === "ANY" ? "" : detRaw; if (det && ![...els.detector.options].some((o) => o.value === det)) { const opt = document.createElement("option"); opt.value = det; opt.textContent = det; els.detector.appendChild(opt); } els.detector.value = det; }
    if (q.has("name")) els.targetTitle.textContent = q.get("name");
    state.color = q.get("color") !== "0";
    state.blink = q.get("blink") === "1";
    state.sum = q.get("sum") === "1" && !state.blink;
    state.subtract = q.get("subtract") === "1" && !state.blink && !state.sum;
    state.background = q.get("background") !== "0";
    if (q.has("stretch")) state.stretch = clamp(Number(q.get("stretch")) || 1.5, 0.5, 4);
    els.color.checked = state.color; els.subtract.checked = state.subtract;
    els.blink.checked = state.blink; els.sum.checked = state.sum;
    els.background.checked = state.background; els.stretch.value = String(state.stretch);
    els.stretchLabel.textContent = state.stretch.toFixed(1);
    return true;
  }
  return false;
}

// ---- events ----
function bindEvents() {
  const onCoordInput = () => {
    if (!els || !state || !els.targetTitle) return;
    const title = els.targetTitle.textContent;
    if (title && title !== "No target loaded" && title !== "Loading…") els.targetTitle.textContent = "Custom field";
  };
  for (const id of ["#ra", "#dec"]) {
    const el = $(id); if (el) el.addEventListener("input", onCoordInput);
  }
  boundCleanup.push(() => {
    for (const id of ["#ra", "#dec"]) {
      const el = $(id); if (el) el.removeEventListener("input", onCoordInput);
    }
  });
  const playBtn = $("#play"); if (playBtn) playBtn.addEventListener("click", togglePlayback);
  const prevBtn = $("#prev"); if (prevBtn) prevBtn.addEventListener("click", () => { stopPlayback(); setFrame(state.idx - 1); });
  const nextBtn = $("#next"); if (nextBtn) nextBtn.addEventListener("click", () => { stopPlayback(); setFrame(state.idx + 1); });
  const slider = $("#frame-slider"); if (slider) slider.addEventListener("input", (e) => { stopPlayback(); setFrame(Number(e.target.value)); });
  const speedEl = $("#speed"); if (speedEl) speedEl.addEventListener("input", (e) => { state.speed = Number(e.target.value); els.speedLabel.textContent = `${state.speed} fps`; });
  const loopEl = $("#loop"); if (loopEl) loopEl.addEventListener("change", (e) => { state.loop = e.target.checked; });
  const blinkEl = $("#blink"); if (blinkEl) blinkEl.addEventListener("change", (e) => {
    state.blink = e.target.checked;
    if (state.blink) { state.sum = false; state.subtract = false; els.sum.checked = false; els.subtract.checked = false; state.sumCache.clear(); state.subCache.clear(); state.blinkA = 0; state.blinkB = state.frames.length ? state.frames.length - 1 : 0; state.blinkPhase = false; stopPlayback(); startPlayback(); } else { setFrame(state.idx); }
    syncUrl();
  });
  const subtractEl = $("#subtract"); if (subtractEl) subtractEl.addEventListener("change", (e) => {
    state.subtract = e.target.checked;
    if (state.subtract) { state.sum = false; state.blink = false; els.sum.checked = false; els.blink.checked = false; stopPlayback(); }
    state.subCache.clear(); renderFrame(); updateMeta(); syncUrl();
  });
  const sumEl = $("#sum"); if (sumEl) sumEl.addEventListener("change", (e) => {
    state.sum = e.target.checked;
    if (state.sum) { state.subtract = false; state.blink = false; els.subtract.checked = false; els.blink.checked = false; stopPlayback(); }
    renderFrame(); updateMeta(); syncUrl();
  });
  const colorEl = $("#color"); if (colorEl) colorEl.addEventListener("change", (e) => { state.color = e.target.checked; renderFrame(); syncUrl(); });
  const backgroundEl = $("#background"); if (backgroundEl) backgroundEl.addEventListener("change", (e) => { state.background = e.target.checked; renderFrame(); syncUrl(); });
  const stretchEl = $("#stretch"); if (stretchEl) stretchEl.addEventListener("input", (e) => {
    state.stretch = Number(e.target.value); els.stretchLabel.textContent = state.stretch.toFixed(1);
    state.subCache.clear(); state.sumCache.clear(); renderFrame(); syncUrl();
  });
  const gaiaEl = $("#gaia-toggle"); if (gaiaEl) gaiaEl.addEventListener("change", (e) => { state.showGaia = e.target.checked; renderFrame(); });
  const catwiseEl = $("#catwise-toggle"); if (catwiseEl) catwiseEl.addEventListener("change", (e) => { state.showCatwise = e.target.checked; renderFrame(); });
  const copyLink = $("#copy-link"); if (copyLink) copyLink.addEventListener("click", async () => {
    const btn = copyLink; syncUrl();
    try { await navigator.clipboard.writeText(location.href); const old = btn.textContent; btn.textContent = "Copied!"; setTimeout(() => { btn.textContent = old; }, 1400); } catch (_) { btn.textContent = location.href; }
  });
  const onResize = () => { if (state && state.manifest) renderFrame(); };
  const onKeydown = (e) => {
    if (!state || !els) return;
    const tag = (e.target && e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea") return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.code === "Space") { e.preventDefault(); togglePlayback(); }
    else if (e.code === "ArrowLeft") { e.preventDefault(); stopPlayback(); setFrame(state.idx - 1); }
    else if (e.code === "ArrowRight") { e.preventDefault(); stopPlayback(); setFrame(state.idx + 1); }
    else if (e.key === "b") { const el = $("#blink"); if (el) el.click(); }
    else if (e.key === "s") { const el = $("#subtract"); if (el) el.click(); }
    else if (e.key === "c") { const el = $("#color"); if (el) el.click(); }
  };
  window.addEventListener("resize", onResize);
  document.addEventListener("keydown", onKeydown);
  boundCleanup.push(() => window.removeEventListener("resize", onResize));
  boundCleanup.push(() => document.removeEventListener("keydown", onKeydown));
  loadPresets();
}
