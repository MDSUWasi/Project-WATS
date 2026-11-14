// Project WATS — OpenSeadragon / deck.gl sky view
// Whole-sky context view with proper-motion vectors.
// NOTE: OpenSeadragon and deck.gl are not installed in this environment.
// Add them to frontend/package.json (openseadragon, deck.gl) and run
// `npm install` to enable the tile-pyramid / GPU path. This view falls back
// to the existing canvas blink comparator when the libraries are absent.

import { escapeHtml } from "/state.js";

let osd = null;
let deck = null;
let animId = null;

const CSS = `
.sky-deckgl-wrap { position: relative; width: 100%; height: 520px; background: #050a0f; border: 1px solid var(--line); border-radius: var(--radius-sm); overflow: hidden; }
.sky-deckgl-placeholder { position: absolute; inset: 0; display:flex; flex-direction:column; align-items:center; justify-content:center; color:var(--muted); text-align:center; padding:24px; }
.sky-deckgl-controls { display:flex; gap:8px; align-items:center; margin-top:8px; flex-wrap:wrap; }
`;

function ensureStyle() {
  if (document.getElementById("sky-deckgl-style")) return;
  const s = document.createElement("style");
  s.id = "sky-deckgl-style";
  s.textContent = CSS;
  document.head.appendChild(s);
}

export function render(container) {
  ensureStyle();
  const q = new URLSearchParams(location.search);
  const ra = q.get("ra"); const dec = q.get("dec");
  const fov = q.get("fov") || "0.15";
  const name = q.get("name") || "Custom field";

  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Sky / deck.gl: ${escapeHtml(name)}</h2>
        <div class="coords-meta">${escapeHtml(ra ? `RA ${Number(ra).toFixed(5)}° · Dec ${Number(dec).toFixed(5)}°` : "—")}</div>
      </div>
      <div class="sky-deckgl-wrap" id="sky-deckgl-wrap"></div>
      <div class="sky-deckgl-controls">
        <label class="check"><input type="checkbox" id="deck-show-pm" checked> Proper-motion vectors</label>
        <label class="check"><input type="checkbox" id="deck-show-gaia" checked> Gaia DR3</label>
        <label class="check"><input type="checkbox" id="deck-show-catwise" checked> CatWISE</label>
        <span id="deck-info" class="muted" style="margin-left:auto;font-size:12px;"></span>
      </div>
      <div id="deck-msg" style="margin-top:6px;font-size:12px;color:var(--muted-2);"></div>
    </div>
  `;

  const wrap = container.querySelector("#sky-deckgl-wrap");
  const infoEl = container.querySelector("#deck-info");
  const msgEl = container.querySelector("#deck-msg");

  // Try OpenSeadragon first (tile pyramid), then deck.gl, then fallback.
  let lib = "none";
  if (typeof OpenSeadragon !== "undefined") {
    lib = "openseadragon";
    initOpenSeadragon(wrap, ra, dec, fov);
  } else if (deck) {
    lib = "deckgl";
    initDeckGL(wrap, ra, dec, fov);
  } else {
    lib = "fallback";
    wrap.innerHTML = `
      <div class="sky-deckgl-placeholder">
        <div style="font-size:1.2rem;color:var(--text);margin-bottom:8px;">Whole-sky view</div>
        <div style="font-size:0.86rem;max-width:340px;">
          OpenSeadragon / deck.gl not installed. Add them to frontend/package.json and run
          <code>npm install</code> to enable the tile-pyramid / GPU sky view.<br><br>
          Use the <strong>Sky / Blink</strong> view for the on-demand cutout blink comparator.
        </div>
        <button class="primary" id="deck-open-sky" style="margin-top:12px;">Open Sky / Blink view</button>
      </div>
    `;
    const btn = wrap.querySelector("#deck-open-sky");
    if (btn) btn.addEventListener("click", () => {
      const qp = new URLSearchParams(location.search);
      qp.set("view", "sky");
      history.replaceState(null, "", `${location.pathname}?${qp.toString()}`);
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
  }
  infoEl.textContent = `Library: ${lib} · RA ${ra ? Number(ra).toFixed(4) : "—"}° · Dec ${dec ? Number(dec).toFixed(4) : "—"}° · FoV ${fov}°`;
  msgEl.textContent = lib === "fallback" ? "Install openseadragon and @deck.gl/* for whole-sky tiles." : "";
}

function initOpenSeadragon(wrap, ra, dec, fov) {
  wrap.innerHTML = `<div id="osd-container" style="width:100%;height:100%;"></div>`;
  try {
    osd = new OpenSeadragon.Viewer({
      element: "osd-container",
      showNavigationControl: true,
      showNavigationControlInFullScreen: false,
      visibilityRatio: 1,
      minZoomImageRatio: 0.5,
      maxZoomImageRatio: 4,
      homeFillsViewer: true,
      gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true, dragToPan: true, scrollToZoom: true },
    });
    // Tile source: /tiles/{z}/{x}/{y} when built; otherwise fall back to a single cutout URL.
    const tileUrl = `/tiles/${encodeURIComponent(String(ra || 0))}/${encodeURIComponent(String(dec || 0))}/{level}/{col}/{row}.png`;
    osd.open({ tileSource: tileUrl, width: 4096, height: 4096, tileSize: 256 });
  } catch (err) {
    wrap.innerHTML = `<div class="sky-deckgl-placeholder">OpenSeadragon init failed: ${escapeHtml(String(err))}</div>`;
  }
}

function initDeckGL(wrap, ra, dec, fov) {
  wrap.innerHTML = `<div id="deckgl-container" style="width:100%;height:100%;"></div>`;
  // deck.gl integration would go here (ScatterplotLayer for proper-motion vectors,
  // TileLayer for whole-sky tiles). Requires @deck.gl/core, @deck.gl/layers,
  // @deck.gl/react or vanilla deck.gl setup.
  wrap.innerHTML += `<div class="sky-deckgl-placeholder">deck.gl container ready. Add ScatterplotLayer (proper motion) + TileLayer (whole-sky).</div>`;
}

export function destroy() {
  if (animId) cancelAnimationFrame(animId);
  animId = null;
  if (osd) { try { osd.destroy(); } catch (_) {} osd = null; }
  if (deck) { try { deck.finalize(); } catch (_) {} deck = null; }
}
