// Project WATS — export view
// CSV export for frames, light curve, sources; PNG for frames.

import { fetchJson } from "/api.js";
import { escapeHtml } from "/state.js";

let abortCtrl = null;

export function render(container) {
  const q = new URLSearchParams(location.search);
  const ra = q.get("ra"); const dec = q.get("dec");
  const key = q.get("key");
  const name = q.get("name") || "Custom field";

  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Export: ${escapeHtml(name)}</h2>
        <div class="coords-meta">${escapeHtml(ra ? `RA ${Number(ra).toFixed(5)}° · Dec ${Number(dec).toFixed(5)}°` : "—")}</div>
      </div>
      <div id="export-wrap">
        <div class="panel muted">Load a target in the Sky view to enable export.</div>
      </div>
    </div>
  `;

  const wrap = container.querySelector("#export-wrap");
  if (key) loadExports(wrap, key);
}

async function loadExports(wrap, key) {
  wrap.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Loading export products…</div>`;
  try {
    const res = await fetchJson(`/api/targets/${encodeURIComponent(key)}`);
    if (!res.ok || !res.data) throw new Error(res.data?.error || "Target not found");
    const manifest = res.data;
    const frames = Array.isArray(manifest.frames) ? manifest.frames : [];
    wrap.innerHTML = `
      <div class="export-links" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">
        <a class="primary" href="/api/targets/${encodeURIComponent(key)}/export?format=json">Manifest JSON</a>
        <a class="ghost" href="/api/targets/${encodeURIComponent(key)}/export?format=csv&what=frames">Frames CSV</a>
        <a class="ghost" href="/api/targets/${encodeURIComponent(key)}/export?format=csv&what=lightcurve">Light curve CSV</a>
      </div>
      <table class="visit-table">
        <thead><tr><th>Frame</th><th>MJD</th><th>Time</th><th>Gray</th><th>Color</th></tr></thead>
        <tbody>${frames.map((f) => `
          <tr>
            <td>${f.index ?? "—"}</td>
            <td>${Number(f.mjd || 0).toFixed(3)}</td>
            <td>${escapeHtml(String(f.time || "—"))}</td>
            <td>${f.gray ? `<a href="/frames/${encodeURIComponent(key)}/${encodeURIComponent(f.gray)}">PNG</a>` : "—"}</td>
            <td>${f.color ? `<a href="/frames/${encodeURIComponent(key)}/${encodeURIComponent(f.color)}">PNG</a>` : "—"}</td>
          </tr>`).join("")}
        </tbody>
      </table>`;
  } catch (err) {
    wrap.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(err))}</div>`;
  }
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
}