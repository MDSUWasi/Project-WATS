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
      <span id="mo-info" class="muted" style="margin-left:auto;font-size:12px;"></span>
    </div>
    <div id="mo-list"></div>
  `;

  const detectBtn = wrap.querySelector("#mo-detect");
  const infoEl = wrap.querySelector("#mo-info");
  const listEl = wrap.querySelector("#mo-list");

  detectBtn.addEventListener("click", async () => {
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    listEl.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Comparing consecutive frames…</div>`;
    infoEl.textContent = "";
    try {
      const res = await fetchJson("/api/analysis/moving-objects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key }),
        signal: abortCtrl.signal,
      });
      if (!res.ok || !res.data) throw new Error(res.data?.error || "Detection failed");
      const data = res.data;
      infoEl.textContent = `${data.n_candidates} candidate${data.n_candidates === 1 ? "" : "s"} across ${data.n_frames} frames`;
      renderCandidates(listEl, data.candidates || []);
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
      <thead><tr><th>Frame</th><th>X</th><th>Y</th><th>Pixels</th><th>Max Diff</th><th>Confidence</th></tr></thead>
      <tbody>
        ${candidates.map((c) => `
          <tr>
            <td>${c.frame_index}</td>
            <td>${c.x}</td>
            <td>${c.y}</td>
            <td>${c.n_pixels}</td>
            <td>${c.max_diff.toFixed(3)}</td>
            <td>${(100 * c.confidence).toFixed(0)}%</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
}
