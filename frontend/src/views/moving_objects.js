// Project WATS — moving object detection (Phase 5)
// Compares consecutive frames to find candidate moving objects.

import { fetchJson } from "/api.js";
import { escapeHtml } from "/state.js";

let abortCtrl = null;

export function render(container) {
  const q = new URLSearchParams(location.search);
  const key = q.get("key");
  const ra = q.get("ra"); const dec = q.get("dec");
  const name = q.get("name") || "Custom field";

  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Moving Objects: ${escapeHtml(name)}</h2>
        <div class="coords-meta">${escapeHtml(key ? `key ${key}` : "no target")}</div>
      </div>
      <div id="mo-wrap"></div>
    </div>
  `;

  const wrap = container.querySelector("#mo-wrap");

  if (!key) {
    wrap.innerHTML = `<div class="panel muted">Load a target (Sky view) to run moving-object detection.</div>`;
    return;
  }

  wrap.innerHTML = `
    <div class="mo-toolbar" style="display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;">
      <button class="primary" id="mo-detect">Detect moving objects</button>
      <label class="check" style="display:flex;align-items:center;gap:6px;color:var(--muted-2);font-size:0.8rem;">
        <input type="checkbox" id="mo-predict" checked> Predict trajectories
      </label>
      <label class="check" style="display:flex;align-items:center;gap:6px;color:var(--muted-2);font-size:0.8rem;">
        <input type="checkbox" id="mo-classify" checked> Auto-classify
      </label>
      <span id="mo-info" class="muted" style="margin-left:auto;font-size:12px;"></span>
    </div>
    <div id="mo-list"></div>
    <div id="mo-predictions" style="margin-top:14px;"></div>
  `;

  const detectBtn = wrap.querySelector("#mo-detect");
  const predictEl = wrap.querySelector("#mo-predict");
  const classifyEl = wrap.querySelector("#mo-classify");
  const infoEl = wrap.querySelector("#mo-info");
  const listEl = wrap.querySelector("#mo-list");
  const predEl = wrap.querySelector("#mo-predictions");

  detectBtn.addEventListener("click", async () => {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    listEl.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Comparing consecutive frames…</div>`;
    predEl.innerHTML = "";
    infoEl.textContent = "";
    try {
      const res = await fetchJson("/api/analysis/moving-objects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, predict: predictEl.checked, classify: classifyEl.checked }),
        signal: abortCtrl.signal,
      });
      if (!res.ok || !res.data) throw new Error(res.data?.error || "Detection failed");
      const data = res.data;
      infoEl.textContent = `${data.n_candidates} candidate${data.n_candidates === 1 ? "" : "s"} across ${data.n_frames} frames`;
      renderCandidates(listEl, data.candidates || []);
      renderPredictions(predEl, data.predictions || []);
    } catch (err) {
      if (err.name === "AbortError") return;
      listEl.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
    }
  });
}

function renderCandidates(listEl, candidates) {
  if (!candidates.length) {
    listEl.innerHTML = `<div class="muted">No moving-object candidates found.</div>`;
    return;
  }
  listEl.innerHTML = `
    <table class="visit-table">
      <thead><tr><th>Frame</th><th>X</th><th>Y</th><th>Pixels</th><th>Max Diff</th><th>Class</th><th>Confidence</th></tr></thead>
      <tbody>
        ${candidates.map((c) => {
          const cls = c.classification || {};
          const label = cls.label || "—";
          const conf = c.confidence !== undefined ? (100 * c.confidence).toFixed(0) : "—";
          const clsColor = { asteroid: "#67b7ff", brown_dwarf: "#ffa94d", variable_star: "#ffd43b", artifact: "#ff6b6b" }[label] || "var(--muted-2)";
          const reasons = (cls.reasons || []).join("; ");
          return `
          <tr>
            <td>${c.frame_index}</td>
            <td>${c.x}</td>
            <td>${c.y}</td>
            <td>${c.n_pixels}</td>
            <td>${c.max_diff.toFixed(3)}</td>
            <td><span style="color:${clsColor};font-weight:600;">${escapeHtml(label)}</span>${reasons ? `<small style="display:block;color:var(--muted-2);font-size:0.68rem;">${escapeHtml(reasons)}</small>` : ""}</td>
            <td>${conf}%</td>
          </tr>`;
        }).join("")}
      </tbody>
    </table>
  `;
}

function renderPredictions(el, predictions) {
  if (!predictions.length) {
    el.innerHTML = `<div class="muted" style="font-size:0.8rem;">No trajectory predictions (need >= 2 detections of the same cluster).</div>`;
    return;
  }
  el.innerHTML = `
    <div class="panel" style="padding:12px 14px;">
      <div style="font-weight:600;margin-bottom:8px;">Predicted trajectories</div>
      <table class="visit-table">
        <thead><tr><th>Cluster</th><th>Detections</th><th>Velocity (px/day)</th><th>Predicted next (px)</th><th>Next MJD</th><th>Confidence</th></tr></thead>
        <tbody>
          ${predictions.map((p) => `
            <tr>
              <td>(${p.cluster[0]}, ${p.cluster[1]})</td>
              <td>${p.n_detections}</td>
              <td>${p.velocity_px_per_day[0].toFixed(3)}, ${p.velocity_px_per_day[1].toFixed(3)}</td>
              <td>(${p.predicted_next_xy[0]}, ${p.predicted_next_xy[1]})</td>
              <td>${p.predicted_next_mjd}</td>
              <td>${(100 * p.confidence).toFixed(0)}%</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
}
