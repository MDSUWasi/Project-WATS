// Project WATS — scientific whole-field sky context view (deck.gl)
// Renders real SPHEREx frame imagery (when a target is loaded) with
// Gaia DR3 / CatWISE2020 catalog overlays and proper-motion vectors.

import { fetchJson } from "/api.js";
import { escapeHtml, parseRa, parseDec } from "/state.js";

let deckInstance = null;
let currentOverlay = null;
let currentManifest = null;
let resizeObs = null;
let viewState = null;

const CSS = `
.skydeck-wrap { position: relative; width: 100%; height: 560px; background: #02050a; border: 1px solid var(--line, #243041); border-radius: 8px; overflow: hidden; }
.skydeck-canvas { position: absolute; inset: 0; }
.skydeck-controls { display:flex; flex-wrap:wrap; gap:10px 14px; align-items:center; margin-top:10px; font-size:13px; }
.skydeck-controls label { display:inline-flex; align-items:center; gap:6px; }
.skydeck-controls input[type=range] { width:140px; vertical-align:middle; }
.skydeck-status { margin-top:8px; font-size:12px; color: var(--muted, #9fb0c7); line-height:1.4; }
.skydeck-legend { display:flex; flex-wrap:wrap; gap:12px; margin-top:6px; font-size:12px; color: var(--muted, #9fb0c7); }
.skydeck-swatch { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:4px; vertical-align:middle; }
.skydeck-tooltip { position:absolute; pointer-events:none; z-index:5; background:rgba(10,16,24,.95); border:1px solid #2b394d; color:#e7f0fb; padding:6px 8px; border-radius:6px; font-size:12px; max-width:260px; white-space:pre-line; }
`;

function ensureStyle() {
  if (document.getElementById("skydeck-style")) return;
  const s = document.createElement("style");
  s.id = "skydeck-style";
  s.textContent = CSS;
  document.head.appendChild(s);
}

function readParams() {
  const q = new URLSearchParams(location.search);
  let ra = q.get("ra"), dec = q.get("dec");
  const raNum = ra !== null ? Number(parseRa(ra)) : NaN;
  const decNum = dec !== null ? Number(parseDec(dec)) : NaN;
  return {
    key: q.get("key") || "",
    ra: Number.isFinite(raNum) ? raNum : null,
    dec: Number.isFinite(decNum) ? decNum : null,
    fov: Number(q.get("fov")) || 0.15,
    name: q.get("name") || "Custom field",
  };
}

function pickFrameImage(manifest) {
  if (!manifest || !Array.isArray(manifest.frames) || !manifest.frames.length) return null;
  const mid = Math.floor(manifest.frames.length / 2);
  const f = manifest.frames[mid];
  if (!f) return null;
  const file = f.color_sub || f.color || f.gray_sub || f.gray;
  if (!file) return null;
  const key = manifest.key;
  return { url: `/frames/${key}/${file}`, frame: f, index: mid };
}

// Gnomonic (tangent-plane) projection centered at (ra0,dec0). Returns [xEast,yNorth] in degrees.
function tangentXY(ra, dec, ra0, dec0) {
  const d2r = Math.PI / 180;
  const raR = ra * d2r, decR = dec * d2r, ra0R = ra0 * d2r, dec0R = dec0 * d2r;
  const dra = raR - ra0R;
  const cosc = Math.sin(dec0R) * Math.sin(decR) + Math.cos(dec0R) * Math.cos(decR) * Math.cos(dra);
  if (!(cosc > 0)) return [NaN, NaN];
  const x = (Math.cos(decR) * Math.sin(dra)) / cosc; // east
  const y = (Math.cos(dec0R) * Math.sin(decR) - Math.sin(dec0R) * Math.cos(decR) * Math.cos(dra)) / cosc; // north
  return [x / d2r, y / d2r];
}

function pmToDeltaDeg(pmra_masyr, pmdec_masyr, decDeg, years) {
  // pmra is mu_alpha* = mu_alpha*cos(dec) in mas/yr
  const dra = ((Number(pmra_masyr) || 0) / 3.6e6) * years / Math.max(Math.cos((decDeg * Math.PI) / 180), 0.05);
  const ddec = ((Number(pmdec_masyr) || 0) / 3.6e6) * years;
  return [dra, ddec];
}

function magRadius(mag, base, scale) {
  const m = Number(mag);
  if (!Number.isFinite(m)) return base;
  const r = base + scale * Math.max(0, 18 - m);
  return Math.max(2, Math.min(r, base + scale * 10));
}

async function ensureDeck() {
  if (typeof deck !== "undefined" && deck.Deck) return true;
  throw new Error("deck.gl library is not available (lib/deck.gl.min.js not loaded).");
}

export async function render(container) {
  ensureStyle();
  const p = readParams();
  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Whole-field sky context: ${escapeHtml(p.name)}</h2>
        <div class="coords-meta">${p.ra != null ? `RA ${p.ra.toFixed(5)}° · Dec ${p.dec.toFixed(5)}° · FoV ${p.fov}°` : "Enter RA/Dec in the query panel to load a field."}</div>
      </div>
      <div class="skydeck-wrap" id="skydeck-wrap">
        <canvas id="skydeck-canvas" class="skydeck-canvas"></canvas>
        <div id="skydeck-tooltip" class="skydeck-tooltip" hidden></div>
      </div>
      <div class="skydeck-controls">
        <label class="check"><input type="checkbox" id="sd-frame" checked> SPHEREx frame</label>
        <label class="check"><input type="checkbox" id="sd-gaia" checked> Gaia DR3</label>
        <label class="check"><input type="checkbox" id="sd-catwise" checked> CatWISE2020</label>
        <label class="check"><input type="checkbox" id="sd-pm" checked> Proper-motion vectors</label>
        <label>PM × <input type="range" id="sd-pm-years" min="10" max="500" step="10" value="100"><span id="sd-pm-years-val">100 yr</span></label>
        <label>Point scale <input type="range" id="sd-pscale" min="0.5" max="3" step="0.1" value="1"><span id="sd-pscale-val">1.0×</span></label>
        <button class="ghost" id="sd-reload" type="button">Reload data</button>
        <button class="ghost" id="sd-reset" type="button">Reset view</button>
      </div>
      <div class="skydeck-legend">
        <span><span class="skydeck-swatch" style="background:#4fc3ff"></span>Gaia source</span>
        <span><span class="skydeck-swatch" style="background:#ff9f43"></span>CatWISE source</span>
        <span><span class="skydeck-swatch" style="background:#c7f36b"></span>Proper-motion vector (scaled)</span>
      </div>
      <div class="skydeck-status" id="skydeck-status">Initializing…</div>
    </div>
  `;

  if (p.ra == null || p.dec == null) {
    setStatus(container, "No valid coordinates in URL. Load a target from the Sky / Blink view or enter RA/Dec and load frames, then open this view.");
    return;
  }

  try {
    await ensureDeck();
  } catch (err) {
    setStatus(container, `deck.gl unavailable: ${escapeHtml(String(err))}. The bundled library at frontend/src/lib/deck.gl.min.js must be served.`);
    return;
  }

  bindControls(container);
  await loadAndRender(container, p);

  const wrap = container.querySelector("#skydeck-wrap");
  if (wrap && "ResizeObserver" in window) {
    resizeObs = new ResizeObserver(() => {
      if (deckInstance && wrap) {
        try { deckInstance.setProps({ width: wrap.clientWidth, height: wrap.clientHeight }); } catch (_) {}
      }
    });
    resizeObs.observe(wrap);
  }
}

function setStatus(container, msg) {
  const el = container.querySelector("#skydeck-status");
  if (el) el.textContent = msg;
}

function bindControls(container) {
  const rerender = () => renderLayers(container).catch(() => {});
  for (const id of ["sd-frame", "sd-gaia", "sd-catwise", "sd-pm"]) {
    const el = container.querySelector(`#${id}`);
    if (el) el.addEventListener("change", rerender);
  }
  const years = container.querySelector("#sd-pm-years");
  const yearsVal = container.querySelector("#sd-pm-years-val");
  if (years) years.addEventListener("input", () => { if (yearsVal) yearsVal.textContent = `${years.value} yr`; rerender(); });
  const pscale = container.querySelector("#sd-pscale");
  const pscaleVal = container.querySelector("#sd-pscale-val");
  if (pscale) pscale.addEventListener("input", () => { if (pscaleVal) pscaleVal.textContent = `${Number(pscale.value).toFixed(1)}×`; rerender(); });
  const reload = container.querySelector("#sd-reload");
  if (reload) reload.addEventListener("click", () => loadAndRender(container, readParams()));
  const reset = container.querySelector("#sd-reset");
  if (reset) reset.addEventListener("click", () => {
    const p = readParams();
    if (p.ra == null || p.dec == null) return;
    const wrap = container.querySelector("#skydeck-wrap");
    const fovDeg = Number((currentManifest && currentManifest.fov_deg) || p.fov || 0.15);
    const wpx = wrap ? (wrap.clientWidth || 700) : 700, hpx = wrap ? (wrap.clientHeight || 560) : 560;
    const fitZoom = Math.log2(Math.min(wpx, hpx) / (fovDeg * 1.25));
    viewState = { target: [0, 0, 0], zoom: fitZoom };
    if (deckInstance) try { deckInstance.setProps({ initialViewState: viewState, viewState }); } catch (_) {}
  });
}

async function loadAndRender(container, p) {
  setStatus(container, "Loading catalog overlays and target data…");
  currentManifest = null;
  currentOverlay = null;
  const tasks = [];
  const naxis = 512;
  tasks.push(fetchJson(`/api/overlays?ra=${encodeURIComponent(p.ra)}&dec=${encodeURIComponent(p.dec)}&fov=${encodeURIComponent(p.fov)}&naxis=${naxis}`));
  if (p.key) tasks.push(fetchJson(`/api/targets/${encodeURIComponent(p.key)}`));
  else tasks.push(Promise.resolve(null));
  let overlayRes, targetRes;
  try {
    [overlayRes, targetRes] = await Promise.all(tasks);
  } catch (err) {
    setStatus(container, `Data load failed: ${String(err)}`);
    return;
  }
  if (overlayRes && overlayRes.ok && overlayRes.data) currentOverlay = overlayRes.data;
  if (targetRes && targetRes.ok && targetRes.data) currentManifest = targetRes.data;

  const fovDeg = Number((currentManifest && currentManifest.fov_deg) || p.fov || 0.15);
  const wrap = container.querySelector("#skydeck-wrap");
  const wpx = wrap ? (wrap.clientWidth || 700) : 700, hpx = wrap ? (wrap.clientHeight || 560) : 560;
  viewState = { target: [0, 0, 0], zoom: Math.log2(Math.min(wpx, hpx) / (fovDeg * 1.25)) };
  await renderLayers(container);
  const o = currentOverlay || {};
  const gN = (o.gaia || []).filter(s => s.inside).length;
  const cN = (o.catwise || []).filter(s => s.inside).length;
  const errs = Object.values(o.errors || {}).filter(Boolean);
  const img = pickFrameImage(currentManifest);
  const parts = [
    `Gaia: ${gN} in field (${(o.gaia||[]).length} queried)`,
    `CatWISE: ${cN} in field (${(o.catwise||[]).length} queried)`,
    img ? `SPHEREx frame: ${img.frame.time || img.frame.mjd || ("frame "+img.index)}` : "SPHEREx frame: not loaded (load a target in Sky / Blink view to underlay imagery)",
  ];
  if (errs.length) parts.push(`Catalog warnings: ${errs.join("; ")}`);
  if (o.stale) parts.push("Note: showing cached catalog data (stale).");
  parts.push("Scientific view: positions from Gaia DR3 / CatWISE2020 TAP queries via IRSA; proper-motion vectors show extrapolated displacement (pmra*cosδ, pmdec) over the selected time baseline. Drag to pan, scroll to zoom, hover for source details.");
  setStatus(container, parts.join(" · "));
}

async function renderLayers(container) {
  const p = readParams();
  if (p.ra == null || p.dec == null) return;
  const wrap = container.querySelector("#skydeck-wrap");
  const canvas = container.querySelector("#skydeck-canvas");
  const tooltip = container.querySelector("#skydeck-tooltip");
  if (!wrap || !canvas) return;
  const { Deck, OrthographicView, ScatterplotLayer, LineLayer, BitmapLayer, PathLayer, COORDINATE_SYSTEM } = deck;
  const showFrame = !!container.querySelector("#sd-frame")?.checked;
  const showGaia = !!container.querySelector("#sd-gaia")?.checked;
  const showCatwise = !!container.querySelector("#sd-catwise")?.checked;
  const showPM = !!container.querySelector("#sd-pm")?.checked;
  const years = Number(container.querySelector("#sd-pm-years")?.value || 100);
  const pscale = Number(container.querySelector("#sd-pscale")?.value || 1);

  // Work in tangent-plane degrees centered on the field (Cartesian). This avoids
  // geospatial-view/zoom mismatches that can render a blank screen.
  const fovDeg = Number(currentManifest?.fov_deg || p.fov || 0.15);
  const half = fovDeg / 2;
  const toXY = (ra, dec) => tangentXY(ra, dec, p.ra, p.dec);

  const layers = [];

  // Field boundary and crosshair so the view is never visually "empty".
  const border = [[-half,-half],[half,-half],[half,half],[-half,half],[-half,-half]];
  const cross = [[-half,0],[half,0],[0,-half],[0,half]];
  if (PathLayer) {
    layers.push(new PathLayer({ id: 'field-border', data: [{ path: border }], getPath: d=>d.path, getColor: [70,100,140,200], widthUnits: 'pixels', getWidth: 1.5, pickable: false }));
    layers.push(new PathLayer({ id: 'field-cross', data: [{ path: cross[0] && [[-half,0],[half,0]] }, { path: [[0,-half],[0,half]] }], getPath: d=>d.path, getColor: [50,75,110,160], widthUnits: 'pixels', getWidth: 1, pickable: false }));
  } else {
    layers.push(new LineLayer({ id:'field-border', data:[{s:[-half,-half],t:[half,-half]},{s:[half,-half],t:[half,half]},{s:[half,half],t:[-half,half]},{s:[-half,half],t:[-half,-half]},{s:[-half,0],t:[half,0]},{s:[0,-half],t:[0,half]}], getSourcePosition:d=>d.s, getTargetPosition:d=>d.t, getColor:[70,100,140,200], getWidth:1, widthUnits:'pixels' }));
  }

  const img = showFrame ? pickFrameImage(currentManifest) : null;
  if (img) {
    // BitmapLayer bounds in Cartesian: [left,bottom,right,top]
    const bounds = [-half, -half, half, half];
    layers.push(new BitmapLayer({
      id: "spherex-frame",
      bounds,
      image: img.url,
      opacity: 0.92,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
    }));
  }

  const gaiaRaw = (currentOverlay && currentOverlay.gaia) ? currentOverlay.gaia.filter(s => s && s.inside !== false) : [];
  const catRaw = (currentOverlay && currentOverlay.catwise) ? currentOverlay.catwise.filter(s => s && s.inside !== false) : [];
  const gaia = gaiaRaw.map(d => ({ ...d, xy: toXY(Number(d.ra), Number(d.dec)) })).filter(d => Number.isFinite(d.xy[0]));
  const catwise = catRaw.map(d => ({ ...d, xy: toXY(Number(d.ra), Number(d.dec)) })).filter(d => Number.isFinite(d.xy[0]));

  if (showGaia && gaia.length) {
    layers.push(new ScatterplotLayer({
      id: "gaia-points",
      data: gaia,
      pickable: true,
      opacity: 0.95,
      stroked: true,
      filled: true,
      radiusUnits: "pixels",
      radiusMinPixels: 2,
      radiusMaxPixels: 30,
      lineWidthMinPixels: 1,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPosition: d => d.xy,
      getRadius: d => magRadius(d.mag, 3, 0.6) * pscale,
      getFillColor: [79, 195, 255, 170],
      getLineColor: [190, 240, 255, 230],
    }));
  }
  if (showCatwise && catwise.length) {
    layers.push(new ScatterplotLayer({
      id: "catwise-points",
      data: catwise,
      pickable: true,
      opacity: 0.9,
      stroked: true,
      filled: true,
      radiusUnits: "pixels",
      radiusMinPixels: 2,
      radiusMaxPixels: 30,
      lineWidthMinPixels: 1,
      coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
      getPosition: d => d.xy,
      getRadius: d => magRadius(d.mag ?? d.w1, 3.5, 0.55) * pscale,
      getFillColor: [255, 159, 67, 160],
      getLineColor: [255, 225, 180, 230],
    }));
  }
  if (showPM) {
    const pmData = [];
    const addPM = (rows, cat) => {
      for (const d of rows) {
        const pmra = Number(d.pmra), pmdec = Number(d.pmdec);
        if (!Number.isFinite(pmra) || !Number.isFinite(pmdec)) continue;
        if (Math.abs(pmra) < 0.01 && Math.abs(pmdec) < 0.01) continue;
        const ra0 = Number(d.ra), dec0 = Number(d.dec);
        if (!Number.isFinite(ra0) || !Number.isFinite(dec0)) continue;
        const [dra, ddec] = pmToDeltaDeg(pmra, pmdec, dec0, years);
        const sxy = d.xy || toXY(ra0, dec0);
        const txy = toXY(ra0 + dra, dec0 + ddec);
        if (!Number.isFinite(sxy[0]) || !Number.isFinite(txy[0])) continue;
        pmData.push({ source: sxy, target: txy, cat, d, pmra, pmdec });
      }
    };
    if (showGaia) addPM(gaia, "Gaia");
    if (showCatwise) addPM(catwise, "CatWISE");
    if (pmData.length) {
      layers.push(new LineLayer({
        id: "pm-vectors",
        data: pmData,
        pickable: true,
        opacity: 0.95,
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        getSourcePosition: d => d.source,
        getTargetPosition: d => d.target,
        getColor: [199, 243, 107, 230],
        getWidth: 2,
        widthUnits: "pixels",
        widthMinPixels: 1,
        widthMaxPixels: 6,
      }));
      layers.push(new ScatterplotLayer({
        id: "pm-heads",
        data: pmData,
        pickable: false,
        opacity: 0.95,
        radiusUnits: "pixels",
        coordinateSystem: COORDINATE_SYSTEM.CARTESIAN,
        getPosition: d => d.target,
        getRadius: 2.5 * pscale,
        getFillColor: [199, 243, 107, 240],
      }));
    }
  }

  const onHover = (info) => {
    if (!tooltip) return;
    if (info && info.object) {
      const d = info.object.d || info.object;
      const lines = [];
      if (d.catalog || info.object.cat) lines.push(`Catalog: ${d.catalog || info.object.cat}`);
      if (d.id != null) lines.push(`ID: ${d.id}`);
      if (Number.isFinite(Number(d.ra))) lines.push(`RA: ${Number(d.ra).toFixed(6)}°`);
      if (Number.isFinite(Number(d.dec))) lines.push(`Dec: ${Number(d.dec).toFixed(6)}°`);
      if (d.mag != null) lines.push(`mag: ${Number(d.mag).toFixed(3)}`);
      if (d.w1 != null) lines.push(`W1: ${Number(d.w1).toFixed(3)}`);
      if (d.parallax != null) lines.push(`parallax: ${Number(d.parallax).toFixed(3)} mas`);
      const pmra = info.object.pmra ?? d.pmra, pmdec = info.object.pmdec ?? d.pmdec;
      if (Number.isFinite(Number(pmra)) || Number.isFinite(Number(pmdec))) {
        lines.push(`pmRA: ${Number(pmra||0).toFixed(2)} mas/yr`);
        lines.push(`pmDec: ${Number(pmdec||0).toFixed(2)} mas/yr`);
        const pmt = Math.hypot(Number(pmra||0), Number(pmdec||0));
        lines.push(`|pm|: ${pmt.toFixed(2)} mas/yr`);
      }
      if (info.layer && info.layer.id === "pm-vectors") lines.push(`Vector baseline: ${years} yr (scaled)`);
      tooltip.hidden = false;
      tooltip.style.left = `${info.x + 12}px`;
      tooltip.style.top = `${info.y + 12}px`;
      tooltip.textContent = lines.join("\n") || "source";
    } else {
      tooltip.hidden = true;
    }
  };

  // OrthographicView zoom: scale = 2^zoom pixels per world unit (degree). Fit field to view.
  const wpx = wrap.clientWidth || 700, hpx = wrap.clientHeight || 560;
  const fitZoom = Math.log2(Math.min(wpx, hpx) / (fovDeg * 1.25));
  if (!viewState) viewState = { target: [0, 0, 0], zoom: fitZoom };
  if (!Number.isFinite(viewState.zoom)) viewState.zoom = fitZoom;

  const props = {
    canvas,
    width: wpx,
    height: hpx,
    views: new OrthographicView({ id: "ortho" }),
    initialViewState: viewState,
    controller: { dragPan: true, dragRotate: false, scrollZoom: true, doubleClickZoom: true, touchZoom: true },
    layers,
    onHover,
    getCursor: ({ isHovering }) => (isHovering ? "pointer" : "grab"),
    onViewStateChange: ({ viewState: vs }) => { viewState = vs; },
  };

  if (!deckInstance) {
    deckInstance = new Deck(props);
  } else {
    deckInstance.setProps(props);
  }
}

export function destroy() {
  if (resizeObs) { try { resizeObs.disconnect(); } catch (_) {} resizeObs = null; }
  if (deckInstance) { try { deckInstance.finalize(); } catch (_) {} deckInstance = null; }
  currentOverlay = null; currentManifest = null; viewState = null;
}
