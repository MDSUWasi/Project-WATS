// Project WATS — OpenSeadragon / deck.gl sky view
// Whole-sky context view with proper-motion vectors.
// Loads OpenSeadragon and deck.gl via CDN importmap, with graceful fallback.

import { escapeHtml } from "/state.js";

let osd = null;
let deckInstance = null;
let animId = null;

let deckCore = null;
let deckLayers = null;

async function loadDeckGL() {
  if (deckCore && deckLayers) return true;
  if (deckCore === false) return false;
  try {
    deckCore = await import("@deck.gl/core");
    deckLayers = await import("@deck.gl/layers");
    return true;
  } catch (err) {
    console.warn("deck.gl CDN load failed:", err);
    deckCore = false;
    deckLayers = false;
    return false;
  }
}

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

export async function render(container) {
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
  } else if (await loadDeckGL()) {
    lib = "deckgl";
    initDeckGL(wrap, ra, dec, fov);
  } else {
    lib = "fallback";
    wrap.innerHTML = `
      <div class="sky-deckgl-placeholder">
        <div style="font-size:1.2rem;color:var(--text);margin-bottom:8px;">Whole-sky view</div>
        <div style="font-size:0.86rem;max-width:340px;">
          OpenSeadragon / deck.gl not loaded from CDN. Use the <strong>Sky / Blink</strong> view for the on-demand cutout blink comparator.
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
  msgEl.textContent = lib === "fallback" ? "Load OpenSeadragon or deck.gl from CDN for whole-sky tiles." : "";
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
  if (!deckCore || !deckLayers) {
    wrap.innerHTML = `<div class="sky-deckgl-placeholder">deck.gl modules not available.</div>`;
    return;
  }
  const {Deck} = deckCore;
  const {ScatterplotLayer} = deckLayers;
  wrap.innerHTML = `<div id="deckgl-container" style="width:100%;height:100%;"></div>`;
  try {
    // Generate mock proper-motion scatter points for the field center
    const points = [];
    const n = 400;
    for (let i = 0; i < n; i++) {
      const angle = Math.random() * Math.PI * 2;
      const dist = 0.02 + Math.random() * 0.08;
      points.push({
        position: [Number(ra || 0) + Math.cos(angle) * dist * 3600, Number(dec || 0) + Math.sin(angle) * dist * 3600],
        pmra: (Math.random() - 0.5) * 200,
        pmdec: (Math.random() - 0.5) * 200,
        mag: 10 + Math.random() * 10,
      });
    }
    const layer = new ScatterplotLayer({
      id: "proper-motion-layer",
      data: points,
      getPosition: (d) => d.position,
      getRadius: (d) => 60 + (14 - d.mag) * 30,
      getFillColor: (d) => [255, 140, 92, 200],
      getLineColor: (d) => [255, 255, 255, 180],
      getLineWidth: 2,
      pickable: true,
      autoHighlight: true,
    });
    deckInstance = new Deck({
      canvas: "deckgl-container",
      width: wrap.clientWidth || 700,
      height: wrap.clientHeight || 520,
      initialViewState: {
        longitude: Number(ra || 0),
        latitude: Number(dec || 0),
        zoom: 11,
        pitch: 0,
        bearing: 0,
      },
      controller: true,
      layers: [layer],
    });
  } catch (err) {
    wrap.innerHTML = `<div class="sky-deckgl-placeholder">deck.gl init failed: ${escapeHtml(String(err))}</div>`;
  }
}

export function destroy() {
  if (animId) cancelAnimationFrame(animId);
  animId = null;
  if (osd) { try { osd.destroy(); } catch (_) {} osd = null; }
  if (deckInstance) { try { deckInstance.finalize(); } catch (_) {} deckInstance = null; }
}
