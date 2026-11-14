// Project WATS — spectral fit view (native canvas, no external deps)
import { fetchJson } from "/api.js";
import { fmt, escapeHtml } from "/state.js";

let abortCtrl = null;
let lastFit = null;

export function render(container) {
  const q = new URLSearchParams(location.search);
  const ra = q.get("ra"); const dec = q.get("dec");
  const fov = q.get("fov") || "0.15";
  const maxFrames = q.get("max_frames") || "6";
  const name = q.get("name") || "Custom field";

  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Spectral Fit: ${escapeHtml(name)}</h2>
        <div class="coords-meta">${escapeHtml(ra ? `RA ${Number(ra).toFixed(5)}° · Dec ${Number(dec).toFixed(5)}°` : "—")}</div>
      </div>
      <div class="sf-toolbar" style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
        <button class="primary" id="sf-load">Load & Fit</button>
        <label style="font-size:0.82rem;color:var(--muted);">Continuum degree
          <select id="sf-degree" style="font-size:0.78rem;padding:4px 6px;">
            <option value="1">1 (linear)</option>
            <option value="2" selected>2 (quadratic)</option>
            <option value="3">3 (cubic)</option>
          </select>
        </label>
        <label style="font-size:0.82rem;color:var(--muted);">Lines
          <select id="sf-lines" style="font-size:0.78rem;padding:4px 6px;">
            <option value="0">0</option>
            <option value="2" selected>2</option>
            <option value="4">4</option>
          </select>
        </label>
        <span id="sf-info" class="muted" style="margin-left:auto;font-size:12px;"></span>
      </div>
      <div class="sf-wrap" id="sf-wrap"></div>
      <div id="sf-stats" style="margin-top:8px;font-size:12px;color:var(--muted-2);"></div>
    </div>
  `;

  const loadBtn = container.querySelector("#sf-load");
  const degreeSel = container.querySelector("#sf-degree");
  const linesSel = container.querySelector("#sf-lines");
  const infoEl = container.querySelector("#sf-info");
  const statsEl = container.querySelector("#sf-stats");
  const wrap = container.querySelector("#sf-wrap");

  loadBtn.addEventListener("click", async () => {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    wrap.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Loading spectrum…</div>`;
    statsEl.textContent = "";
    try {
      const qp = new URLSearchParams({ ra: q.get("ra") || "", dec: q.get("dec") || "", fov, max_frames: maxFrames, name });
      const res = await fetchJson(`/api/spectrum?${qp.toString()}`, abortCtrl.signal);
      if (res.status === 202) {
        setTimeout(() => loadBtn.click(), 2500);
        return;
      }
      if (!res.ok || !res.data) throw new Error(res.data?.error || "Spectrum unavailable");
      const data = res.data;
      infoEl.textContent = `${data.n_points} points · ${data.wavelength_range_um ? data.wavelength_range_um.map((v) => v.toFixed(2)).join("–") + " µm" : ""}`;
      const fitRes = await fitSpectrum(data.points, {
        continuum_degree: Number(degreeSel.value),
        n_lines: Number(linesSel.value),
      });
      lastFit = fitRes;
      renderSpectrum(data, fitRes, statsEl);
    } catch (err) {
      if (err.name === "AbortError") return;
      wrap.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
    }
  });

  if (ra && dec) loadBtn.click();
}

async function fitSpectrum(points, opts) {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = new AbortController();
  const res = await fetchJson("/api/analysis/spectral-fit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ points, ...opts }),
    signal: abortCtrl.signal,
  });
  if (!res.ok || !res.data) throw new Error(res.data?.error || "Spectral fit failed");
  return res.data;
}

function renderSpectrum(data, fit, statsEl) {
  const wrap = document.querySelector("#sf-wrap");
  wrap.innerHTML = `<canvas class="sf-canvas" width="700" height="300"></canvas>`;
  const canvas = wrap.querySelector("canvas");
  const ctx = canvas.getContext("2d");

  const points = data.points || [];
  const wl = points.map((p) => Number(p.wavelength_um) || 0);
  const flux = points.map((p) => Number(p.flux_jy || 0));

  const wMin = Math.min(...wl) - 0.05;
  const wMax = Math.max(...wl) + 0.05;
  const fMin = Math.min(0, ...flux) - 1;
  const fMax = Math.max(0, ...flux) + 1;

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

  ctx.fillStyle = "rgba(160, 190, 220, 0.8)";
  ctx.font = "12px ui-sans-serif, system-ui, sans-serif";
  ctx.fillText("Wavelength (µm)", (x0 + x1) / 2 - 42, canvas.height - 8);
  ctx.save();
  ctx.translate(14, (y0 + y1) / 2 + 40);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("Flux (Jy)", 0, 0);
  ctx.restore();

  // Flux points
  for (const p of points) {
    const x = mapX(Number(p.wavelength_um));
    const y = mapY(Number(p.flux_jy || 0));
    ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2);
    ctx.fillStyle = "#4f9dff";
    ctx.fill();
  }

  // Continuum line
  const coeffs = fit.continuum_coeffs || [];
  const n = 100;
  ctx.beginPath();
  let first = true;
  for (let i = 0; i < n; i++) {
    const x = wMin + (wMax - wMin) * i / (n - 1);
    let y = 0;
    for (let d = 0; d < coeffs.length; d++) {
      y += coeffs[d] * Math.pow(x, d);
    }
    const px = mapX(x);
    const py = mapY(y);
    if (first) { ctx.moveTo(px, py); first = false; }
    else ctx.lineTo(px, py);
  }
  ctx.strokeStyle = "#7cc4ff";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([5, 5]);
  ctx.stroke();
  ctx.setLineDash([]);

  // Stats
  statsEl.innerHTML = `
    <strong>Fit:</strong> continuum degree ${fit.continuum_degree} · ${fit.n_lines} line${fit.n_lines === 1 ? "" : "s"} ·
    χ²/dof = ${fit.chi2_dof} · RMS residual = ${fit.rms_residual_jy} Jy · N = ${fit.n_points}
  `;
  if (fit.lines && fit.lines.length) {
    statsEl.innerHTML += `<br><strong>Lines:</strong> ${fit.lines.map((l) => `${l.center_um.toFixed(3)} µm (SNR ${l.snr.toFixed(1)})`).join(" · ")}`;
  }
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
  lastFit = null;
}
