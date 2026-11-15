// Project WATS — spectrum view (native canvas, no external deps)
import { fetchJson } from "/api.js";
import { fmt, escapeHtml } from "/state.js";

let abortCtrl = null;
let lastPoints = null;
let _retryCount = 0;
const MAX_RETRIES = 20; // ~50s of polling before giving up

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
        _retryCount = 0;
        if (abortCtrl) abortCtrl.abort();
        abortCtrl = new AbortController();
        loadSpectrum({ ra, dec, fov, max_frames: maxFrames, name }, { signal: abortCtrl.signal });
    });

    csvBtn.addEventListener("click", () => {
        if (!lastPoints || !lastPoints.length) return;
        const rows = lastPoints.map((p) => {
          const flux_mjy = Number.isFinite(Number(p.flux_mjy)) ? Number(p.flux_mjy) : (Number(p.flux_jy) || 0) * 1000;
          const err_mjy = Number.isFinite(Number(p.error_mjy)) ? Number(p.error_mjy) : (Number(p.error_jy) || 0) * 1000;
          return [p.wavelength_um, p.bandwidth_um ?? "", flux_mjy, err_mjy, p.mjd, p.detector ?? ""];
        });
        const csv = ["wavelength_um,bandwidth_um,flux_mjy,error_mjy,mjd,detector", ...rows.map((r) => r.join(","))].join("\n");
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
        const key = new URLSearchParams(location.search).get("key");
        let res;
        if (key) {
            res = await fetchJson(`/api/targets/${encodeURIComponent(key)}/spectrum`, opts.signal);
        } else {
            const q = new URLSearchParams({ ra: params.ra, dec: params.dec, fov: params.fov, max_frames: params.max_frames, name: params.name });
            res = await fetchJson(`/api/spectrum?${q.toString()}`, opts.signal);
        }
        if (res.status === 202) {
            if (_retryCount++ >= MAX_RETRIES) {
                wrap.innerHTML = `<div class="panel error">Spectrum is not available yet. Load a target in the Sky view first, or wait for frame extraction to complete on a host with IRSA access.</div>`;
                msgEl.textContent = "";
                return;
            }
            msgEl.textContent = `Spectrum is being computed (frames may still be loading), retrying… (${_retryCount}/${MAX_RETRIES})`;
            setTimeout(() => loadSpectrum(params, opts), 2500);
            return;
        }
        _retryCount = 0;
        if (!res.ok || !res.data) throw new Error((res.data && (res.data.error || res.data.message)) || "Spectrum unavailable");
        const data = res.data;
        const points = Array.isArray(data.points) ? data.points.slice() : [];
        lastPoints = points;

        const wlVals = points.map(p => Number(p.wavelength_um)).filter(Number.isFinite);
        const range = (Array.isArray(data.wavelength_range_um) && data.wavelength_range_um.length === 2)
            ? data.wavelength_range_um
            : (wlVals.length ? [Math.min(...wlVals), Math.max(...wlVals)] : null);
        const detCount = new Set(points.map(p => p.detector).filter(Boolean)).size;
        infoEl.textContent = `${data.n_points ?? points.length} points${range ? ` · ${range[0].toFixed(2)}–${range[1].toFixed(2)} µm` : ""}`;
        countEl.textContent = detCount ? `${detCount} detector${detCount === 1 ? "" : "s"}` : "";
        if (points.length) {
            msgEl.textContent = `Extraction: ${data.method || "aperture photometry on SPHEREx cutouts"} · ${data.status || "ready"}. Aperture flux at target position vs wavelength (per-frame WCS-WAVE samples, converted to mJy).`;
        } else {
            msgEl.textContent = data.message || "No spectrum samples extracted for this target position (frames may be missing cached cutouts).";
        }
        renderSpectrum({ ...data, points });
        csvBtn.disabled = points.length === 0;
    } catch (err) {
        if (err && err.name === "AbortError") return;
        wrap.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
        msgEl.textContent = "";
        lastPoints = [];
    }
}

function renderSpectrum(data) {
  const wrap = document.querySelector("#spec-wrap");
  wrap.innerHTML = `<canvas class="spectrum-canvas" width="700" height="300"></canvas>`;
  const canvas = wrap.querySelector("canvas");
  const ctx = canvas.getContext("2d");

  const points = (Array.isArray(data.points) ? data.points : []).filter(
    (p) => Number.isFinite(Number(p.wavelength_um)) && Number.isFinite(Number(p.flux_jy))
  );
  if (!points.length) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "rgba(150, 180, 210, 0.55)";
    ctx.font = "400 14px ui-sans-serif, system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("No spectrum points available for this target.", canvas.width / 2, canvas.height / 2);
    return;
  }

  const wl = points.map((p) => Number(p.wavelength_um));
  const flux = points.map((p) => Number(p.flux_jy) * 1000); // mJy
  const err = points.map((p) => (Number(p.error_jy) || 0) * 1000);

  const wMinRaw = Math.min(...wl);
  const wMaxRaw = Math.max(...wl);
  const wPad = Math.max(0.02, (wMaxRaw - wMinRaw) * 0.05) || 0.05;
  const wMin = wMinRaw - wPad;
  const wMax = wMaxRaw + wPad;
  const vals = flux.map((v, i) => v - (err[i] || 0));
  const tops = flux.map((v, i) => v + (err[i] || 0));
  let fMin = Math.min(0, ...vals);
  let fMax = Math.max(0, ...tops);
  if (!Number.isFinite(fMin) || !Number.isFinite(fMax) || fMax - fMin < 1e-9) { fMin = -1; fMax = 1; }
  const pad = (fMax - fMin) * 0.12;
  fMin -= pad; fMax += pad;

  const ml = 62, mr = 18, mt = 20, mb = 42;
  const x0 = ml, x1 = canvas.width - mr, y0 = mt, y1 = canvas.height - mb;

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#080D13";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const mapX = (v) => x0 + ((v - wMin) / (wMax - wMin || 1)) * (x1 - x0);
  const mapY = (v) => y1 - ((v - fMin) / (fMax - fMin || 1)) * (y1 - y0);

  // Grid
  ctx.strokeStyle = "rgba(80, 115, 150, 0.22)";
  ctx.lineWidth = 1;
  ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
  ctx.fillStyle = "rgba(140, 170, 200, 0.75)";
  ctx.textAlign = "center";
  for (let w = 0.5; w <= 5.5; w += 0.5) {
    if (w < wMin || w > wMax) continue;
    const x = mapX(w);
    ctx.beginPath(); ctx.moveTo(x, y0); ctx.lineTo(x, y1); ctx.stroke();
    ctx.fillText(w.toFixed(1).replace(/\.0$/, ""), x, y1 + 16);
  }
  ctx.textAlign = "right";
  const yTicks = 5;
  for (let i = 0; i <= yTicks; i++) {
    const v = fMin + (fMax - fMin) * (i / yTicks);
    const y = mapY(v);
    ctx.beginPath(); ctx.moveTo(x0, y); ctx.lineTo(x1, y); ctx.stroke();
    ctx.fillText(fmt(v, 2), x0 - 6, y + 4);
  }
  ctx.strokeStyle = "rgba(140, 175, 210, 0.5)";
  ctx.beginPath(); ctx.moveTo(x0, y1); ctx.lineTo(x1, y1); ctx.moveTo(x0, y0); ctx.lineTo(x0, y1); ctx.stroke();

  // Labels
  ctx.textAlign = "left";
  ctx.fillStyle = "rgba(160, 190, 220, 0.8)";
  ctx.font = "12px ui-sans-serif, system-ui, sans-serif";
  ctx.fillText("Wavelength (µm)", (x0 + x1) / 2 - 42, canvas.height - 8);
  ctx.save();
  ctx.translate(14, (y0 + y1) / 2 + 40);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("Flux (mJy)", 0, 0);
  ctx.restore();

  // Color by epoch (MJD) to show multi-visit sampling; guide connects in wavelength order.
  const mjds = points.map((p) => Number(p.mjd)).filter(Number.isFinite);
  const mMin = mjds.length ? Math.min(...mjds) : 0;
  const mMax = mjds.length ? Math.max(...mjds) : 1;
  const colorFor = (mjd) => {
    const t = mMax > mMin ? (Number(mjd) - mMin) / (mMax - mMin) : 0.5;
    const r = Math.round(90 + (255 - 90) * t);
    const g = Math.round(167 + (138 - 167) * t);
    const b = Math.round(255 + (90 - 255) * t);
    return `rgb(${r},${g},${b})`;
  };

  const sorted = points.slice().sort((a, b) => Number(a.wavelength_um) - Number(b.wavelength_um));
  if (sorted.length > 1) {
    ctx.strokeStyle = "rgba(130,170,220,0.30)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let i = 0; i < sorted.length; i++) {
      const x = mapX(Number(sorted[i].wavelength_um));
      const y = mapY(Number(sorted[i].flux_jy) * 1000);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    const x = mapX(Number(p.wavelength_um));
    const fy = Number(p.flux_jy) * 1000;
    const e = (Number(p.error_jy) || 0) * 1000;
    const y = mapY(fy);
    const yTop = mapY(fy + e);
    const yBot = mapY(fy - e);
    const c = colorFor(p.mjd);
    if (e > 0) {
      ctx.strokeStyle = c;
      ctx.globalAlpha = 0.55;
      ctx.beginPath(); ctx.moveTo(x, yTop); ctx.lineTo(x, yBot); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    ctx.beginPath(); ctx.arc(x, y, 3.2, 0, Math.PI * 2);
    ctx.fillStyle = c;
    ctx.fill();
  }

  const minWl = wMinRaw.toFixed(2);
  const maxWl = wMaxRaw.toFixed(2);
  const info = document.querySelector("#spec-info");
  if (info) info.textContent = `${minWl}–${maxWl} µm · ${points.length} points`;
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
  lastPoints = null;
}
