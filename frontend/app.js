/* Project WATS — SPHEREx blink comparator
 * Vanilla JS, no external dependencies.
 */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const lerp = (a, b, t) => a + (b - a) * t;

  const canvas = $("#sky");
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  const specCanvas = $("#spectrum");
  const specCtx = specCanvas.getContext("2d");
  const work = document.createElement("canvas");
  const workCtx = work.getContext("2d", { willReadFrequently: true });
  const native = document.createElement("canvas");
  const nativeCtx = native.getContext("2d", { willReadFrequently: true });

  const state = {
    manifest: null,
    frames: [],
    idx: 0,
    playing: false,
    speed: 4,
    loop: true,
    blink: false,
    subtract: false,
    sum: false,
    color: true,
    background: true,
    stretch: 1.5,
    blinkA: 0,
    blinkB: 0,
    blinkPhase: false,
    raf: null,
    lastTime: 0,
    acc: 0,
    spectrum: null,
    overlays: null,
    showGaia: false,
    showCatwise: false,
    images: new Map(),
    dataCache: new Map(),
    sumCache: new Map(),
    subCache: new Map(),
    statusTimer: null,
    pollTimer: null,
    loading: false,
    key: null,
    params: null,
    lastRendered: -1,
  };

  const FALLBACK_PRESETS = [
    { name: "WISE 0855", ra: 133.795096, dec: -7.245147, fov: 0.15, description: "Fast-moving Y brown dwarf" },
    { name: "Barnard's Star", ra: 269.4521, dec: 4.6933, fov: 0.15, description: "High proper-motion red dwarf" },
    { name: "Epsilon Indi B", ra: 330.59025, dec: -56.7833, fov: 0.15, description: "Brown-dwarf binary" },
    { name: "RW Aurigae", ra: 76.95625, dec: 30.6747, fov: 0.2, description: "Eruptive variable star" },
    { name: "R Draconis", ra: 269.4321, dec: 57.4, fov: 0.2, description: "Mira variable" },
  ];

  // -----------------------------------------------------------------------
  // Utilities
  // -----------------------------------------------------------------------

  function fmt(v, digits = 3) {
    if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
    return Number(v).toFixed(digits);
  }

  function fmtSci(v) {
    if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
    if (v === 0) return "0";
    const a = Math.abs(v);
    if (a >= 0.01 && a < 1000) return Number(v).toFixed(3);
    return Number(v).toExponential(2);
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toISOString().replace("T", " ").replace("Z", " UTC");
  }

  function fmtDeg(ra, dec) {
    return `RA ${Number(ra).toFixed(5)}°  ·  Dec ${Number(dec).toFixed(5)}°`;
  }

  function parseRa(text) {
    const s = String(text).trim();
    if (/^[+-]?\d+(\.\d+)?$/.test(s)) return Number(s) % 360;
    let m = s.match(/^(\d{1,2})[hH:\s]+(\d{1,2})[mM:\s]+(\d+(?:\.\d+)?)[sS]?$/);
    if (m) return (Number(m[1]) + Number(m[2]) / 60 + Number(m[3]) / 3600) * 15;
    m = s.match(/^(\d{1,2})[hH:\s]+(\d{1,2})[mM]?$/);
    if (m) return (Number(m[1]) + Number(m[2]) / 60) * 15;
    return NaN;
  }

  function parseDec(text) {
    const s = String(text).trim();
    if (/^[+-]?\d+(\.\d+)?$/.test(s)) return Number(s);
    let m = s.match(/^([+-]?\d{1,3})[dD:\s]+(\d{1,2})[mM:\s]+(\d+(?:\.\d+)?)[sS]?$/);
    if (m) {
      const sign = Math.sign(Number(m[1])) || 1;
      return Number(m[1]) + sign * (Number(m[2]) / 60 + Number(m[3]) / 3600);
    }
    m = s.match(/^([+-]?\d{1,3})[dD:\s]+(\d{1,2})[mM]?$/);
    if (m) return Number(m[1]) + (Math.sign(Number(m[1])) || 1) * Number(m[2]) / 60;
    return NaN;
  }

  async function fetchJson(url) {
    try {
      const resp = await fetch(url, { headers: { Accept: "application/json" } });
      let data = null;
      try { data = await resp.json(); } catch (_) { /* no body */ }
      return { ok: resp.ok, status: resp.status, data };
    } catch (err) {
      return { ok: false, status: 0, data: { error: String(err) } };
    }
  }

  function showError(message) {
    state.loading = false;
    $("#loading").hidden = true;
    const box = $("#error-box");
    $("#error-text").textContent = message || "Unknown error.";
    box.hidden = false;
  }

  function clearError() {
    $("#error-box").hidden = true;
  }

  function showLoading(text, progress, total) {
    state.loading = true;
    clearError();
    const el = $("#loading");
    el.hidden = false;
    $("#loading-text").textContent = text || "Loading…";
    const pct = total > 0 ? clamp((progress / total) * 100, 0, 100) : 0;
    $("#progress-bar").style.width = `${pct}%`;
    $("#cancel-btn").hidden = !state.key;
  }

  function hideLoading() {
    state.loading = false;
    $("#loading").hidden = true;
  }

  // -----------------------------------------------------------------------
  // Image loading / pixel data
  // -----------------------------------------------------------------------

  function imageUrl(frame, mode) {
    if (!frame) return null;
    if (mode === "gray") return frame.gray_url || frame.gray;
    if (mode === "graySub") return frame.gray_sub_url || frame.gray_sub;
    if (mode === "color") return frame.color_url || frame.color;
    if (mode === "colorSub") return frame.color_sub_url || frame.color_sub;
    return null;
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
    native.width = n;
    native.height = n;
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

  function getData(i, mode) {
    const key = `${i}:${mode}`;
    if (state.dataCache.has(key)) return state.dataCache.get(key);
    if (state.dataCache.size > 260) state.dataCache.clear();
    const frame = state.frames[i];
    const url = imageUrl(frame, mode);
    const img = url ? state.images.get(url) : null;
    if (!img) return null;
    const n = state.manifest ? state.manifest.naxis : img.naturalWidth || 320;
    native.width = n;
    native.height = n;
    nativeCtx.clearRect(0, 0, n, n);
    nativeCtx.drawImage(img, 0, 0, n, n);
    let data;
    try {
      data = nativeCtx.getImageData(0, 0, n, n);
    } catch (_) {
      return null;
    }
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
    let acc = null;
    for (let i = 0; i < state.frames.length; i++) {
      const d = getData(i, mode);
      if (!d) continue;
      if (!acc) {
        acc = new Float32Array(d.data.length);
        acc.width = d.width;
        acc.height = d.height;
      }
      for (let p = 0; p < d.data.length; p += 4) {
        acc[p] += d.data[p];
        acc[p + 1] += d.data[p + 1];
        acc[p + 2] += d.data[p + 2];
      }
    }
    if (!acc) return null;
    const n = state.frames.length;
    const out = new ImageData(new Uint8ClampedArray(acc.length), acc.width, acc.height);
    const gamma = 1 / Math.max(0.2, state.stretch);
    for (let p = 0; p < acc.length; p += 4) {
      const r = acc[p] / n / 255;
      const g = acc[p + 1] / n / 255;
      const b = acc[p + 2] / n / 255;
      out.data[p] = 255 * Math.pow(clamp(r, 0, 1), gamma);
      out.data[p + 1] = 255 * Math.pow(clamp(g, 0, 1), gamma);
      out.data[p + 2] = 255 * Math.pow(clamp(b, 0, 1), gamma);
      out.data[p + 3] = 255;
    }
    state.sumCache.set(mode, out);
    return out;
  }

  function applyStretch(data) {
    if (!data) return null;
    if (state.subtract) return data; // subtract already has its own gain
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

  // -----------------------------------------------------------------------
  // Rendering
  // -----------------------------------------------------------------------

  function drawPlaceholder() {
    const w = canvas.width;
    const h = canvas.height;
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

  function render() {
    if (!state.manifest || !state.frames.length) {
      drawPlaceholder();
      updateMeta();
      return;
    }
    const n = state.manifest.naxis || 320;
    if (canvas.width !== 720) { canvas.width = 720; canvas.height = 720; }
    work.width = n;
    work.height = n;

    let data = null;
    if (state.sum) {
      data = getSum(modeForDisplay());
    } else if (state.subtract) {
      data = getSubtract(state.idx, modeForDisplay());
    } else {
      const idx = state.blink ? (state.blinkPhase ? state.blinkB : state.blinkA) : state.idx;
      data = getData(idx, modeForDisplay());
    }
    data = applyStretch(data);
    if (!data) {
      drawPlaceholder();
      updateMeta();
      return;
    }
    workCtx.putImageData(data, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(work, 0, 0, canvas.width, canvas.height);

    drawOverlays(n);
    drawCrosshair();
    drawTimestamp(n);
    const shown = state.blink ? (state.blinkPhase ? state.blinkB : state.blinkA) : state.idx;
    if (state.lastRendered !== shown) {
      state.lastRendered = shown;
      updateMeta();
      drawSpectrum();
    }
  }

  function drawCrosshair() {
    const w = canvas.width;
    const h = canvas.height;
    ctx.save();
    ctx.strokeStyle = "rgba(220, 238, 255, 0.32)";
    ctx.lineWidth = 1;
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(w / 2, 0);
    ctx.lineTo(w / 2, h);
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();
    ctx.restore();
  }

  function drawTimestamp(n) {
    const frame = state.frames[state.blink ? (state.blinkPhase ? state.blinkB : state.blinkA) : state.idx];
    if (!frame) return;
    const lines = [];
    lines.push(`${frame.time || ""}`.replace("T", " ").replace("Z", " UTC"));
    lines.push(`${(frame.detectors || []).join(", ")} · ${frame.n_channels || 0} band${frame.n_channels === 1 ? "" : "s"}`);
    const w = canvas.width;
    ctx.save();
    ctx.font = "600 15px ui-monospace, SFMono-Regular, Menlo, monospace";
    const text = lines.join("\n");
    const metrics = ctx.measureText(lines[0]);
    const boxW = Math.max(metrics.width, 260) + 24;
    const boxH = 52;
    ctx.fillStyle = "rgba(3, 8, 13, 0.66)";
    ctx.strokeStyle = "rgba(150, 190, 230, 0.18)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(10, 10, boxW, boxH, 8);
    else ctx.rect(10, 10, boxW, boxH);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = "rgba(220, 238, 255, 0.92)";
    ctx.fillText(lines[0], 22, 32);
    ctx.font = "500 13px ui-sans-serif, system-ui, sans-serif";
    ctx.fillStyle = "rgba(150, 185, 220, 0.8)";
    ctx.fillText(lines[1], 22, 51);
    ctx.restore();
  }

  function drawOverlays(n) {
    if (!state.overlays) return;
    const sx = canvas.width / n;
    const sy = canvas.height / n;
    const drawSet = (items, color, show, label) => {
      if (!show || !items) return;
      const inside = items.filter((s) => s && s.inside && Number.isFinite(s.x) && Number.isFinite(s.y));
      inside.sort((a, b) => (a.mag ?? a.w1 ?? 99) - (b.mag ?? b.w1 ?? 99));
      const shown = inside.slice(0, 120);
      ctx.save();
      for (let i = 0; i < shown.length; i++) {
        const s = shown[i];
        const mag = s.mag ?? s.w1 ?? 15;
        const r = clamp(9.5 - (mag - 8) * 0.45, 2.5, 8);
        const x = (s.x + 0.5) * sx;
        const y = (s.y + 0.5) * sy;
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.4;
        ctx.stroke();
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

  // -----------------------------------------------------------------------
  // Metadata / spectrum / overlays panels
  // -----------------------------------------------------------------------

  function updateMeta() {
    const f = state.frames[state.idx];
    const pill = $("#visit-pill");
    const meta = $("#frame-meta");
    if (!f) {
      pill.textContent = "—";
      meta.innerHTML = '<div class="meta-empty">Load a target to see observation metadata.</div>';
      return;
    }
    pill.textContent = `Visit ${state.idx + 1} / ${state.frames.length}`;
    const wls = f.wavelengths_um || {};
    const wlText = Object.keys(wls).length
      ? Object.entries(wls).map(([k, v]) => `${k}: ${Number(v).toFixed(2)}`).join(" · ")
      : (f.wavelength_um ? `${Number(f.wavelength_um).toFixed(2)} µm` : "—");
    const band = Array.isArray(f.bandpass_um) ? `${Number(f.bandpass_um[0]).toFixed(2)}–${Number(f.bandpass_um[1]).toFixed(2)} µm` : "—";
    const items = [
      ["Time (UTC)", fmtTime(f.time)],
      ["Visit", f.obs_base || f.obs_id || "—"],
      ["Detector(s)", (f.detectors || []).join(", ") || "—"],
      ["Wavelength at target", wlText],
      ["Detector bandpass", band],
      ["Exposure", f.exptime ? `${Number(f.exptime).toFixed(1)} s` : "—"],
      ["Background", f.background_mjy_sr !== undefined ? `${fmt(f.background_mjy_sr, 3)} MJy/sr` : "—"],
      ["Noise (MAD)", f.noise_mjy_sr !== undefined ? `${fmt(f.noise_mjy_sr, 4)} MJy/sr` : "—"],
      ["Peak", f.peak_mjy_sr !== undefined ? `${fmt(f.peak_mjy_sr, 2)} MJy/sr` : "—"],
      ["Flagged pixels", f.flags_bad_fraction !== undefined ? `${(100 * f.flags_bad_fraction).toFixed(1)}%` : "—"],
      ["Release / version", [f.release, f.version].filter(Boolean).join(" · ") || "—"],
    ];
    meta.innerHTML = items.map(([k, v]) => `<div class="meta-item"><div class="k">${escapeHtml(k)}</div><div class="v">${escapeHtml(v)}</div></div>`).join("");
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function drawSpectrum() {
    const w = specCanvas.width;
    const h = specCanvas.height;
    const c = specCtx;
    c.clearRect(0, 0, w, h);
    c.fillStyle = "#080d13";
    c.fillRect(0, 0, w, h);
    const ml = 62, mr = 18, mt = 20, mb = 42;
    const x0 = ml, x1 = w - mr, y0 = mt, y1 = h - mb;
    const points = (state.spectrum && state.spectrum.points) || [];
    $("#spectrum-count").textContent = points.length ? `${points.length} points` : "";
    if (!points.length) {
      c.fillStyle = "rgba(150, 180, 210, 0.55)";
      c.font = "400 14px ui-sans-serif, system-ui, sans-serif";
      c.textAlign = "center";
      c.fillText("Spectrum appears when frames are ready.", w / 2, h / 2);
      c.textAlign = "left";
      return;
    }
    const wlMin = 0.75, wlMax = 5.0;
    const vals = points.map((p) => (Number(p.flux_jy) || 0) * 1000);
    const errs = points.map((p) => (Number(p.error_jy) || 0) * 1000);
    let yMin = Math.min(0, ...vals.map((v, i) => v - errs[i]));
    let yMax = Math.max(0, ...vals.map((v, i) => v + errs[i]));
    if (yMax - yMin < 1e-9) { yMin = -1; yMax = 1; }
    const pad = (yMax - yMin) * 0.12;
    yMin -= pad;
    yMax += pad;
    const mapX = (wl) => x0 + ((wl - wlMin) / (wlMax - wlMin)) * (x1 - x0);
    const mapY = (v) => y1 - ((v - yMin) / (yMax - yMin)) * (y1 - y0);

    // grid
    c.strokeStyle = "rgba(80, 115, 150, 0.22)";
    c.lineWidth = 1;
    c.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
    c.fillStyle = "rgba(140, 170, 200, 0.75)";
    for (let wl = 1; wl <= 5; wl++) {
      const x = mapX(wl);
      c.beginPath(); c.moveTo(x, y0); c.lineTo(x, y1); c.stroke();
      c.fillText(`${wl}`, x - 3, y1 + 16);
    }
    const yTicks = 5;
    for (let i = 0; i <= yTicks; i++) {
      const v = lerp(yMin, yMax, i / yTicks);
      const y = mapY(v);
      c.beginPath(); c.moveTo(x0, y); c.lineTo(x1, y); c.stroke();
      c.fillText(fmt(v, 2), 6, y + 4);
    }
    c.strokeStyle = "rgba(140, 175, 210, 0.5)";
    c.beginPath(); c.moveTo(x0, y1); c.lineTo(x1, y1); c.moveTo(x0, y0); c.lineTo(x0, y1); c.stroke();
    c.fillStyle = "rgba(160, 190, 220, 0.8)";
    c.font = "12px ui-sans-serif, system-ui, sans-serif";
    c.fillText("Wavelength (µm)", (x0 + x1) / 2 - 42, h - 8);
    c.save();
    c.translate(14, (y0 + y1) / 2 + 40);
    c.rotate(-Math.PI / 2);
    c.fillText("Flux (mJy)", 0, 0);
    c.restore();

    // highlight current frame
    const current = points.filter((p) => p.frame === state.idx);
    if (current.length) {
      const wl = current.map((p) => p.wavelength_um);
      const lo = mapX(Math.min(...wl) - 0.04);
      const hi = mapX(Math.max(...wl) + 0.04);
      c.fillStyle = "rgba(79, 157, 255, 0.12)";
      c.fillRect(lo, y0, Math.max(2, hi - lo), y1 - y0);
    }

    // points + error bars, coloured by time
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
      c.strokeStyle = colorFor(Number(p.mjd) || 0);
      c.globalAlpha = 0.7;
      c.beginPath(); c.moveTo(x, yTop); c.lineTo(x, yBot); c.stroke();
      c.globalAlpha = 1;
      c.beginPath();
      c.arc(x, y, p.frame === state.idx ? 5 : 3.2, 0, Math.PI * 2);
      c.fillStyle = colorFor(Number(p.mjd) || 0);
      c.fill();
      if (p.frame === state.idx) {
        c.strokeStyle = "#dceeff";
        c.lineWidth = 1.5;
        c.stroke();
      }
    }
    const wl = points.map((p) => p.wavelength_um);
    $("#spectrum-info").textContent = `${Math.min(...wl).toFixed(2)}–${Math.max(...wl).toFixed(2)} µm`;
  }

  function updateOverlayTable() {
    const body = $("#overlay-table tbody");
    const rows = [];
    if (state.overlays) {
      for (const s of state.overlays.gaia || []) {
        if (!s.inside) continue;
        rows.push({ cat: "Gaia", ra: s.ra, dec: s.dec, mag: s.mag, pm: Math.hypot(Number(s.pmra) || 0, Number(s.pmdec) || 0) });
      }
      for (const s of state.overlays.catwise || []) {
        if (!s.inside) continue;
        rows.push({ cat: "CatWISE", ra: s.ra, dec: s.dec, mag: s.w1, pm: Math.hypot(Number(s.pmra) || 0, Number(s.pmdec) || 0) });
      }
    }
    rows.sort((a, b) => (a.mag ?? 99) - (b.mag ?? 99));
    const top = rows.slice(0, 30);
    if (!top.length) {
      body.innerHTML = '<tr><td colspan="5" class="muted">No overlay sources in this field.</td></tr>';
      return;
    }
    body.innerHTML = top.map((r) => `<tr><td>${r.cat}</td><td>${Number(r.ra).toFixed(5)}</td><td>${Number(r.dec).toFixed(5)}</td><td>${r.mag === null || r.mag === undefined ? "—" : Number(r.mag).toFixed(2)}</td><td>${r.pm ? Number(r.pm).toFixed(1) : "—"}</td></tr>`).join("");
  }

  // -----------------------------------------------------------------------
  // Loading targets
  // -----------------------------------------------------------------------

  function readForm() {
    return {
      ra: $("#ra").value.trim(),
      dec: $("#dec").value.trim(),
      fov: Number($("#fov").value),
      max_frames: Number($("#max-frames").value),
      detector: $("#detector").value || "",
      name: $("#target-title").textContent && $("#target-title").textContent !== "No target loaded" ? $("#target-title").textContent : "custom",
    };
  }

  function paramsFromForm() {
    const f = readForm();
    const ra = parseRa(f.ra);
    const dec = parseDec(f.dec);
    if (!Number.isFinite(ra) || !Number.isFinite(dec)) return null;
    return {
      ra,
      dec,
      fov: f.fov,
      max_frames: f.max_frames,
      detector: f.detector || "",
      name: f.name || "custom",
    };
  }

  function updateUrl(params, extra = {}) {
    if (!params) return;
    const q = new URLSearchParams();
    q.set("ra", Number(params.ra).toFixed(6));
    q.set("dec", Number(params.dec).toFixed(6));
    q.set("fov", String(params.fov));
    q.set("max_frames", String(params.max_frames));
    if (params.detector) q.set("detector", params.detector);
    if (params.name && params.name !== "custom") q.set("name", params.name);
    if (state.color) q.set("color", "1");
    if (state.subtract) q.set("subtract", "1");
    if (state.blink) q.set("blink", "1");
    if (state.sum) q.set("sum", "1");
    if (!state.background) q.set("background", "0");
    q.set("stretch", state.stretch.toFixed(1));
    for (const [k, v] of Object.entries(extra)) if (v !== undefined && v !== null) q.set(k, v);
    const url = `${location.pathname}?${q.toString()}`;
    history.replaceState(null, "", url);
  }

  function resetForLoad() {
    state.manifest = null;
    state.frames = [];
    state.spectrum = null;
    state.overlays = null;
    state.idx = 0;
    state.dataCache.clear();
    state.sumCache.clear();
    state.subCache.clear();
    state.lastRendered = -1;
    $("#frame-slider").max = "0";
    $("#frame-slider").value = "0";
    $("#frame-counter").textContent = "0 / 0";
    $("#target-title").textContent = "Loading…";
    $("#target-coords").textContent = "";
    $("#target-badges").innerHTML = "";
    $("#frame-meta").innerHTML = '<div class="meta-empty">Fetching SPHEREx frames…</div>';
    $("#spectrum-info").textContent = "Extracting…";
    $("#overlay-info").textContent = "Querying Gaia / CatWISE…";
    $("#overlay-table tbody").innerHTML = '<tr><td colspan="5" class="muted">Querying catalogues…</td></tr>';
    drawPlaceholder();
    drawSpectrum();
  }

  async function startLoad(useRefresh = false) {
    const params = paramsFromForm();
    if (!params) {
      showError("Enter a valid Right Ascension and Declination (decimal degrees or sexagesimal).");
      return;
    }
    if (useRefresh) params.refresh = true;
    state.params = params;
    state.key = null;
    updateUrl(params);
    resetForLoad();
    showLoading("Querying IRSA for SPHEREx images…", 0, 1);
    try {
      const q = new URLSearchParams({
        ra: params.ra.toFixed(7),
        dec: params.dec.toFixed(7),
        fov: String(params.fov),
        max_frames: String(params.max_frames),
        name: params.name,
      });
      if (params.detector) q.set("detector", params.detector);
      if (useRefresh) q.set("refresh", "1");
      const res = await fetchJson(`/api/frames?${q.toString()}`);
      if (res.status === 202 && res.data && res.data.key) {
        state.key = res.data.key;
        showLoading(res.data.message || "Fetching cutouts from IRSA…", 0, res.data.total || 1);
        pollStatus();
      } else if (res.ok && res.data && res.data.status === "ready") {
        onManifest(res.data);
      } else {
        showError((res.data && (res.data.error || res.data.message)) || "IRSA did not return frames.");
      }
    } catch (err) {
      showError(String(err));
    }
  }

  function pollStatus() {
    if (!state.key) return;
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(async () => {
      if (!state.key) return;
      const res = await fetchJson(`/api/status?key=${encodeURIComponent(state.key)}`);
      if (!res.data) return;
      if (res.data.status === "ready" && res.data.manifest) {
        onManifest(res.data.manifest);
        return;
      }
      if (res.data.status === "cancelled") {
        state.key = null;
        hideLoading();
        showError("Fetch cancelled. Try again with fewer frames, or press Retry.");
        return;
      }
      if (res.data.status === "error") {
        showError(res.data.error || res.data.message || "Fetch failed.");
        return;
      }
      const progress = res.data.progress || 0;
      const total = res.data.total || 1;
      showLoading(res.data.message || "Fetching SPHEREx cutouts from IRSA…", progress, total);
      pollStatus();
    }, 1800);
  }

  async function cancelFetch() {
    if (!state.key) return;
    const key = state.key;
    if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
    await fetchJson(`/api/cancel?key=${encodeURIComponent(key)}`);
    state.key = null;
    hideLoading();
    showError("Fetch cancelled. Try again with fewer frames, or press Retry.");
  }

  async function onManifest(manifest) {
    if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
    state.manifest = manifest;
    state.frames = manifest.frames || [];
    state.key = manifest.key || state.key;
    state.idx = 0;
    state.blinkA = 0;
    state.blinkB = Math.max(0, state.frames.length - 1);
    state.sumCache.clear();
    state.subCache.clear();
    state.dataCache.clear();
    $("#target-title").textContent = manifest.name || "SPHEREx field";
    $("#target-coords").textContent = fmtDeg(manifest.ra, manifest.dec);
    const badges = [];
    badges.push(`<span class="badge">${manifest.visits || state.frames.length} visit${(manifest.visits || 0) === 1 ? "" : "s"}</span>`);
    badges.push(`<span class="badge">${(manifest.detectors || []).length} detector${(manifest.detectors || []).length === 1 ? "" : "s"}</span>`);
    if (manifest.time_range) badges.push(`<span class="badge">${manifest.time_range[0].slice(0, 10)} → ${manifest.time_range[1].slice(0, 10)}</span>`);
    if (manifest.query_rows) badges.push(`<span class="badge">${manifest.query_rows} MEFs</span>`);
    $("#target-badges").innerHTML = badges.join("");
    $("#frame-slider").max = String(Math.max(0, state.frames.length - 1));
    $("#frame-slider").value = "0";
    $("#frame-counter").textContent = `0 / ${state.frames.length}`;
    const fov = Number(manifest.fov_deg) || 0.15;
    const n = Number(manifest.naxis) || 320;
    const barPx = 90;
    const arcsec = fov * 3600 * (barPx / 720);
    const scaleLabel = arcsec >= 60 ? `${(arcsec / 60).toFixed(1)}′` : `${arcsec.toFixed(1)}″`;
    $("#scale-bar").style.width = `${barPx}px`;
    $("#scale-label").textContent = scaleLabel;
    showLoading("Loading rendered frames…", 0, 1);
    await preloadFrames((done, total) => {
      const pct = total ? Math.round((done / total) * 100) : 0;
      showLoading(`Loading rendered frames… ${pct}%`, done, total);
    });
    hideLoading();
    setFrame(0);
    startPlayback();
    loadSpectrum();
    loadOverlays();
  }

  // -----------------------------------------------------------------------
  // Playback / controls
  // -----------------------------------------------------------------------

  function setFrame(i) {
    if (!state.frames.length) return;
    const n = state.frames.length;
    state.idx = clamp(Number(i) || 0, 0, n - 1);
    $("#frame-slider").value = String(state.idx);
    $("#frame-counter").textContent = `${state.idx + 1} / ${n}`;
    if (state.blink) state.blinkB = state.idx;
    render();
    drawSpectrum();
  }

  function startPlayback() {
    if (state.raf) cancelAnimationFrame(state.raf);
    state.playing = true;
    $("#play").textContent = "Pause";
    $("#play").setAttribute("aria-label", "Pause animation");
    state.lastTime = performance.now();
    state.acc = 0;
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      state.playing = false;
      $("#play").textContent = "Play";
      return;
    }
    const tick = (t) => {
      if (!state.playing) return;
      const dt = Math.min(120, t - state.lastTime);
      state.lastTime = t;
      state.acc += dt;
      const interval = 1000 / Math.max(0.5, state.speed);
      if (state.sum) {
        render();
        state.raf = requestAnimationFrame(tick);
        return;
      }
      if (state.blink) {
        if (state.acc >= interval) {
          state.acc = 0;
          state.blinkPhase = !state.blinkPhase;
          render();
        }
      } else {
        if (state.acc >= interval) {
          state.acc = 0;
          if (state.idx >= state.frames.length - 1) {
            if (state.loop) setFrame(0);
            else stopPlayback();
          } else {
            setFrame(state.idx + 1);
          }
        }
      }
      state.raf = requestAnimationFrame(tick);
    };
    state.raf = requestAnimationFrame(tick);
  }

  function stopPlayback() {
    state.playing = false;
    if (state.raf) cancelAnimationFrame(state.raf);
    state.raf = null;
    $("#play").textContent = "Play";
    $("#play").setAttribute("aria-label", "Play animation");
  }

  function togglePlayback() {
    if (state.playing) stopPlayback();
    else startPlayback();
  }

  async function loadSpectrum() {
    if (!state.params) return;
    const q = new URLSearchParams({
      ra: state.params.ra.toFixed(7),
      dec: state.params.dec.toFixed(7),
      fov: String(state.params.fov),
      max_frames: String(state.params.max_frames),
      name: state.params.name,
    });
    if (state.params.detector) q.set("detector", state.params.detector);
    $("#spectrum-count").textContent = "extracting…";
    const res = await fetchJson(`/api/spectrum?${q.toString()}`);
    if (res.status === 202) {
      $("#spectrum-count").textContent = "pending";
      setTimeout(loadSpectrum, 2500);
      return;
    }
    if (res.ok && res.data) {
      state.spectrum = res.data;
      drawSpectrum();
    } else {
      $("#spectrum-count").textContent = "unavailable";
      $("#spectrum-info").textContent = (res.data && (res.data.error || res.data.message)) || "";
    }
  }

  async function loadOverlays() {
    if (!state.params) return;
    const q = new URLSearchParams({
      ra: state.params.ra.toFixed(7),
      dec: state.params.dec.toFixed(7),
      fov: String(state.params.fov),
      naxis: String(state.manifest ? state.manifest.naxis : 320),
    });
    const res = await fetchJson(`/api/overlays?${q.toString()}`);
    if (res.ok && res.data) {
      state.overlays = res.data;
      const g = (res.data.gaia || []).filter((s) => s.inside).length;
      const w = (res.data.catwise || []).filter((s) => s.inside).length;
      $("#overlay-info").textContent = `${g} Gaia · ${w} CatWISE in field`;
      updateOverlayTable();
      render();
    } else {
      $("#overlay-info").textContent = "unavailable";
    }
  }

  // -----------------------------------------------------------------------
  // Presets / URL
  // -----------------------------------------------------------------------

  async function loadPresets() {
    let presets = FALLBACK_PRESETS;
    try {
      const res = await fetchJson("/api/presets");
      if (res.ok && res.data && Array.isArray(res.data.presets) && res.data.presets.length) presets = res.data.presets;
    } catch (_) { /* use fallback */ }
    const wrap = $("#presets");
    wrap.innerHTML = '<span class="label">Try</span>';
    for (const p of presets) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "preset";
      b.innerHTML = `${escapeHtml(p.name)}<small>${escapeHtml(p.description || "")}</small>`;
      b.addEventListener("click", () => {
        $("#ra").value = typeof p.ra === "number" ? p.ra.toFixed(6) : String(p.ra_text || p.ra);
        $("#dec").value = typeof p.dec === "number" ? p.dec.toFixed(6) : String(p.dec_text || p.dec);
        $("#fov").value = String(p.fov || 0.15);
        $("#target-title").textContent = p.name;
        startLoad(false);
      });
      wrap.appendChild(b);
    }
  }

  function applyUrl() {
    const q = new URLSearchParams(location.search);
    if (q.has("ra") && q.has("dec")) {
      $("#ra").value = q.get("ra");
      $("#dec").value = q.get("dec");
      if (q.has("fov")) {
        const fov = Number(q.get("fov"));
        if ([...$("#fov").options].some((o) => Number(o.value) === fov)) $("#fov").value = String(fov);
        else {
          const opt = document.createElement("option");
          opt.value = String(fov);
          opt.textContent = `${(fov * 60).toFixed(1)} arcmin`;
          $("#fov").appendChild(opt);
          $("#fov").value = String(fov);
        }
      }
      if (q.has("max_frames")) {
        const mf = String(q.get("max_frames"));
        if ([...$("#max-frames").options].some((o) => o.value === mf)) $("#max-frames").value = mf;
      }
      if (q.has("detector")) $("#detector").value = q.get("detector");
      if (q.has("name")) $("#target-title").textContent = q.get("name");
      state.color = q.get("color") !== "0";
      state.subtract = q.get("subtract") === "1";
      state.blink = q.get("blink") === "1";
      state.sum = q.get("sum") === "1";
      state.background = q.get("background") !== "0";
      if (q.has("stretch")) state.stretch = clamp(Number(q.get("stretch")) || 1.5, 0.5, 4);
      $("#color").checked = state.color;
      $("#subtract").checked = state.subtract;
      $("#blink").checked = state.blink;
      $("#sum").checked = state.sum;
      $("#background").checked = state.background;
      $("#stretch").value = String(state.stretch);
      $("#stretch-label").textContent = state.stretch.toFixed(1);
      return true;
    }
    return false;
  }

  // -----------------------------------------------------------------------
  // Events
  // -----------------------------------------------------------------------

  function bind() {
    $("#query-form").addEventListener("submit", (e) => {
      e.preventDefault();
      startLoad(false);
    });
    for (const id of ["#ra", "#dec"]) {
      $(id).addEventListener("input", () => {
        const title = $("#target-title").textContent;
        if (title && title !== "No target loaded" && title !== "Loading…") $("#target-title").textContent = "Custom field";
      });
    }
    $("#refresh-btn").addEventListener("click", () => startLoad(true));
    $("#retry-btn").addEventListener("click", () => startLoad(false));
    $("#cancel-btn").addEventListener("click", cancelFetch);
    $("#play").addEventListener("click", togglePlayback);
    $("#prev").addEventListener("click", () => { stopPlayback(); setFrame(state.idx - 1); });
    $("#next").addEventListener("click", () => { stopPlayback(); setFrame(state.idx + 1); });
    $("#frame-slider").addEventListener("input", (e) => { stopPlayback(); setFrame(Number(e.target.value)); });
    $("#speed").addEventListener("input", (e) => {
      state.speed = Number(e.target.value);
      $("#speed-label").textContent = `${state.speed} fps`;
    });
    $("#loop").addEventListener("change", (e) => { state.loop = e.target.checked; });
    $("#blink").addEventListener("change", (e) => {
      state.blink = e.target.checked;
      if (state.blink) {
        state.blinkA = 0;
        state.blinkB = state.frames.length ? state.frames.length - 1 : 0;
        state.blinkPhase = false;
        stopPlayback();
        startPlayback();
      } else {
        setFrame(state.idx);
      }
    });
    $("#subtract").addEventListener("change", (e) => {
      state.subtract = e.target.checked;
      if (state.subtract) state.sum = false;
      $("#sum").checked = state.sum;
      state.subCache.clear();
      render();
    });
    $("#sum").addEventListener("change", (e) => {
      state.sum = e.target.checked;
      if (state.sum) state.subtract = false;
      $("#subtract").checked = state.subtract;
      render();
    });
    $("#color").addEventListener("change", (e) => { state.color = e.target.checked; render(); });
    $("#background").addEventListener("change", (e) => { state.background = e.target.checked; render(); });
    $("#stretch").addEventListener("input", (e) => {
      state.stretch = Number(e.target.value);
      $("#stretch-label").textContent = state.stretch.toFixed(1);
      state.subCache.clear();
      state.sumCache.clear();
      render();
    });
    $("#gaia-toggle").addEventListener("change", (e) => { state.showGaia = e.target.checked; render(); });
    $("#catwise-toggle").addEventListener("change", (e) => { state.showCatwise = e.target.checked; render(); });
    $("#copy-link").addEventListener("click", async () => {
      const btn = $("#copy-link");
      try {
        await navigator.clipboard.writeText(location.href);
        const old = btn.textContent;
        btn.textContent = "Copied!";
        setTimeout(() => { btn.textContent = old; }, 1400);
      } catch (_) {
        btn.textContent = location.href;
      }
    });
    window.addEventListener("resize", () => { if (state.manifest) render(); });
    document.addEventListener("keydown", (e) => {
      const tag = (e.target && e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "select" || tag === "textarea") return;
      if (e.code === "Space") { e.preventDefault(); togglePlayback(); }
      else if (e.code === "ArrowLeft") { e.preventDefault(); stopPlayback(); setFrame(state.idx - 1); }
      else if (e.code === "ArrowRight") { e.preventDefault(); stopPlayback(); setFrame(state.idx + 1); }
      else if (e.key === "b") { $("#blink").click(); }
      else if (e.key === "s") { $("#subtract").click(); }
      else if (e.key === "c") { $("#color").click(); }
    });
  }

  function init() {
    bind();
    loadPresets();
    const hasUrl = applyUrl();
    if (!hasUrl) {
      // Sensible defaults, but do not auto-fetch until the user asks.
      $("#ra").value = "133.795096";
      $("#dec").value = "-7.245147";
      $("#fov").value = "0.15";
      $("#max-frames").value = "6";
    }
    drawPlaceholder();
    drawSpectrum();
    if (hasUrl) startLoad(false);
  }

  init();
})();