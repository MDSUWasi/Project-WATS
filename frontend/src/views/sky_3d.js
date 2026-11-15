// Project WATS — scientific 3D sky field view (three.js)
// Reconstructs the loaded SPHEREx field in 3D: frame textured on a sky
// tangent plane, Gaia DR3 / CatWISE2020 sources placed by sky position,
// and proper-motion vectors shown as 3D arrows over a selectable time baseline.

import { fetchJson } from "/api.js";
import { escapeHtml, parseRa, parseDec } from "/state.js";

let raf = null;
let renderer = null;
let scene = null;
let camera = null;
let controlsState = null;
let resizeObs = null;
let rootGroup = null;
let raycaster = null;
let mouse = null;
let pickables = [];
let tooltipEl = null;
let currentOverlay = null;
let currentManifest = null;

const CSS = `
.sky3d-wrap { position: relative; width: 100%; height: 560px; background: radial-gradient(ellipse at 50% 30%, #0a1020 0%, #02040a 70%); border: 1px solid var(--line, #243041); border-radius: 8px; overflow: hidden; }
.sky3d-canvas { width: 100%; height: 100%; display: block; cursor: grab; }
.sky3d-canvas:active { cursor: grabbing; }
.sky3d-controls { display:flex; flex-wrap:wrap; gap:10px 14px; align-items:center; margin-top:10px; font-size:13px; }
.sky3d-controls label { display:inline-flex; align-items:center; gap:6px; }
.sky3d-controls input[type=range] { width:140px; vertical-align:middle; }
.sky3d-status { margin-top:8px; font-size:12px; color: var(--muted, #9fb0c7); line-height:1.4; }
.sky3d-legend { display:flex; flex-wrap:wrap; gap:12px; margin-top:6px; font-size:12px; color: var(--muted, #9fb0c7); }
.sky3d-swatch { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:4px; vertical-align:middle; }
.sky3d-tooltip { position:absolute; pointer-events:none; z-index:5; background:rgba(10,16,24,.95); border:1px solid #2b394d; color:#e7f0fb; padding:6px 8px; border-radius:6px; font-size:12px; max-width:260px; white-space:pre-line; }
`;

function ensureStyle() {
  if (document.getElementById("sky3d-style")) return;
  const s = document.createElement("style");
  s.id = "sky3d-style";
  s.textContent = CSS;
  document.head.appendChild(s);
}

function readParams() {
  const q = new URLSearchParams(location.search);
  const raNum = Number(parseRa(q.get("ra")));
  const decNum = Number(parseDec(q.get("dec")));
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
  return { url: `/frames/${manifest.key}/${file}`, frame: f, index: mid };
}

// Tangent-plane projection centered at (ra0,dec0). Returns [x,y] in degrees.
function tangentXY(ra, dec, ra0, dec0) {
  const d2r = Math.PI / 180;
  const raR = ra * d2r, decR = dec * d2r, ra0R = ra0 * d2r, dec0R = dec0 * d2r;
  const dra = raR - ra0R;
  const cosc = Math.sin(dec0R) * Math.sin(decR) + Math.cos(dec0R) * Math.cos(decR) * Math.cos(dra);
  if (cosc <= 0) return [NaN, NaN];
  const x = (Math.cos(decR) * Math.sin(dra)) / cosc;
  const y = (Math.cos(dec0R) * Math.sin(decR) - Math.sin(dec0R) * Math.cos(decR) * Math.cos(dra)) / cosc;
  return [x / d2r, y / d2r]; // back to degrees (small-angle)
}

function magSize(mag, base) {
  const m = Number(mag);
  if (!Number.isFinite(m)) return base;
  return Math.max(0.4, Math.min(4.5, base + 0.25 * (18 - m)));
}

function setStatus(container, msg) {
  const el = container.querySelector("#sky3d-status");
  if (el) el.textContent = msg;
}

export async function render(container) {
  ensureStyle();
  const p = readParams();
  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>3D field reconstruction: ${escapeHtml(p.name)}</h2>
        <div class="coords-meta">${p.ra != null ? `RA ${p.ra.toFixed(5)}° · Dec ${p.dec.toFixed(5)}° · FoV ${p.fov}°` : "Enter RA/Dec in the query panel to load a field."}</div>
      </div>
      <div class="sky3d-wrap" id="sky3d-wrap">
        <canvas id="sky3d-canvas" class="sky3d-canvas"></canvas>
        <div id="sky3d-tooltip" class="sky3d-tooltip" hidden></div>
      </div>
      <div class="sky3d-controls">
        <label class="check"><input type="checkbox" id="s3-frame" checked> SPHEREx frame plane</label>
        <label class="check"><input type="checkbox" id="s3-gaia" checked> Gaia DR3</label>
        <label class="check"><input type="checkbox" id="s3-catwise" checked> CatWISE2020</label>
        <label class="check"><input type="checkbox" id="s3-pm" checked> Proper-motion vectors</label>
        <label>PM × <input type="range" id="s3-pm-years" min="10" max="500" step="10" value="100"><span id="s3-pm-years-val">100 yr</span></label>
        <label>Depth exaggeration <input type="range" id="s3-depth" min="0" max="3" step="0.1" value="1"><span id="s3-depth-val">1.0×</span></label>
        <button class="ghost" id="s3-reload" type="button">Reload data</button>
        <button class="ghost" id="s3-reset" type="button">Reset camera</button>
      </div>
      <div class="sky3d-legend">
        <span><span class="sky3d-swatch" style="background:#4fc3ff"></span>Gaia (size ∝ brightness, depth ∝ 1/parallax)</span>
        <span><span class="sky3d-swatch" style="background:#ff9f43"></span>CatWISE</span>
        <span><span class="sky3d-swatch" style="background:#c7f36b"></span>Proper-motion vector (scaled)</span>
      </div>
      <div class="sky3d-status" id="sky3d-status">Initializing 3D view…</div>
    </div>
  `;

  if (p.ra == null || p.dec == null) {
    setStatus(container, "No valid coordinates in URL. Load a target from the Sky / Blink view or enter RA/Dec and load frames, then open this view.");
    return;
  }
  if (typeof THREE === "undefined") {
    setStatus(container, "three.js library is not available (lib/three.min.js not loaded).");
    return;
  }

  bindControls(container);
  await loadAndBuild(container, p);

  const wrap = container.querySelector("#sky3d-wrap");
  if (wrap && "ResizeObserver" in window) {
    resizeObs = new ResizeObserver(() => resizeRenderer(container));
    resizeObs.observe(wrap);
  }
}

function bindControls(container) {
  const rerender = () => { try { rebuildScene(container, readParams()); } catch (_) {} };
  for (const id of ["s3-frame","s3-gaia","s3-catwise","s3-pm","s3-depth"]) {
    const el = container.querySelector(`#${id}`);
    if (el) el.addEventListener("input", () => {
      const yv = container.querySelector("#s3-pm-years-val");
      if (yv) yv.textContent = `${container.querySelector("#s3-pm-years").value} yr`;
      const dv = container.querySelector("#s3-depth-val");
      if (dv) dv.textContent = `${Number(container.querySelector("#s3-depth").value).toFixed(1)}×`;
      rerender();
    });
    if (el) el.addEventListener("change", rerender);
  }
  const reload = container.querySelector("#s3-reload");
  if (reload) reload.addEventListener("click", () => loadAndBuild(container, readParams()));
  const reset = container.querySelector("#s3-reset");
  if (reset) reset.addEventListener("click", () => resetCamera(container));
}

async function loadAndBuild(container, p) {
  setStatus(container, "Loading catalog overlays and target data…");
  currentManifest = null; currentOverlay = null;
  const naxis = 512;
  const tasks = [
    fetchJson(`/api/overlays?ra=${encodeURIComponent(p.ra)}&dec=${encodeURIComponent(p.dec)}&fov=${encodeURIComponent(p.fov)}&naxis=${naxis}`),
    p.key ? fetchJson(`/api/targets/${encodeURIComponent(p.key)}`) : Promise.resolve(null),
  ];
  let overlayRes, targetRes;
  try { [overlayRes, targetRes] = await Promise.all(tasks); }
  catch (err) { setStatus(container, `Data load failed: ${String(err)}`); return; }
  if (overlayRes && overlayRes.ok && overlayRes.data) currentOverlay = overlayRes.data;
  if (targetRes && targetRes.ok && targetRes.data) currentManifest = targetRes.data;
  initThree(container, p);
  rebuildScene(container, p);
  updateStatus(container, p);
}

function updateStatus(container, p) {
  const o = currentOverlay || {};
  const gN = (o.gaia || []).filter(s => s.inside).length;
  const cN = (o.catwise || []).filter(s => s.inside).length;
  const errs = Object.values(o.errors || {}).filter(Boolean);
  const img = pickFrameImage(currentManifest);
  const parts = [
    `Gaia: ${gN} in field`, `CatWISE: ${cN} in field`,
    img ? `SPHEREx frame textured on sky plane (${img.frame.time || "mid visit"})` : "SPHEREx frame: not loaded (open from Sky / Blink view with a loaded target to texture the field plane)",
  ];
  if (errs.length) parts.push(`Catalog warnings: ${errs.join("; ")}`);
  if (o.stale) parts.push("Cached (stale) catalog data");
  parts.push("3D mapping: tangent-plane (gnomonic) sky coordinates; z-depth from Gaia parallax (distance ∝ 1/π, exaggerated) when available; proper-motion vectors extrapolate pmRA/pmDec over the selected baseline. Drag to orbit, scroll to zoom, right-drag to pan, hover for source details.");
  setStatus(container, parts.join(" · "));
}

function initThree(container, p) {
  const canvas = container.querySelector("#sky3d-canvas");
  const wrap = container.querySelector("#sky3d-wrap");
  tooltipEl = container.querySelector("#sky3d-tooltip");
  if (!canvas || !wrap) return;
  disposeThree();
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x050a14);
  const aspect = Math.max(1, wrap.clientWidth) / Math.max(1, wrap.clientHeight);
  camera = new THREE.PerspectiveCamera(50, aspect, 0.01, 5000);
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(wrap.clientWidth, wrap.clientHeight, false);

  // lights
  scene.add(new THREE.AmbientLight(0xb8c8e0, 1.0));
  const d = new THREE.DirectionalLight(0xffffff, 0.8); d.position.set(30, 80, 60); scene.add(d);

  // starfield background (closer, brighter so the view is never pitch black)
  const starGeo = new THREE.BufferGeometry();
  const starN = 1500; const spos = new Float32Array(starN * 3);
  for (let i = 0; i < starN; i++) {
    const th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1), r = 300 + Math.random() * 250;
    spos[i*3] = r*Math.sin(ph)*Math.cos(th); spos[i*3+1] = r*Math.sin(ph)*Math.sin(th); spos[i*3+2] = r*Math.cos(ph);
  }
  starGeo.setAttribute("position", new THREE.BufferAttribute(spos, 3));
  scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xcfe0ff, size: 2.0, sizeAttenuation: true, transparent: true, opacity: 0.9 })));

  rootGroup = new THREE.Group();
  scene.add(rootGroup);
  raycaster = new THREE.Raycaster(); raycaster.params.Points = { threshold: 1.2 };
  mouse = new THREE.Vector2();
  controlsState = { dragging: false, right: false, lastX: 0, lastY: 0, theta: 0.6, phi: 0.9, radius: 120, panX: 0, panY: 0, target: new THREE.Vector3(0,0,0) };
  resetCamera(container);
  setupInteraction(canvas, container);
  startLoop();
}

function resetCamera(container) {
  if (!controlsState) return;
  const p = readParams();
  const fovDeg = Number((currentManifest && currentManifest.fov_deg) || p.fov || 0.15);
  const sceneScale = 100 / Math.max(fovDeg, 0.01);
  controlsState.theta = 0.7; controlsState.phi = 1.0; controlsState.radius = Math.max(80, Math.min(220, fovDeg * sceneScale * 1.3)); controlsState.panX = 0; controlsState.panY = 0;
  updateCamera();
}

function updateCamera() {
  if (!camera || !controlsState) return;
  const { theta, phi, radius, panX, panY } = controlsState;
  const sinPhi = Math.sin(phi), cosPhi = Math.cos(phi);
  camera.position.set(radius * sinPhi * Math.cos(theta) + panX, radius * cosPhi + panY, radius * sinPhi * Math.sin(theta));
  camera.lookAt(panX, panY, 0);
}

function setupInteraction(canvas, container) {
  const onDown = (e) => { controlsState.dragging = true; controlsState.right = e.button === 2; controlsState.lastX = e.clientX; controlsState.lastY = e.clientY; };
  const onUp = () => { controlsState.dragging = false; };
  const onMove = (e) => {
    if (!controlsState) return;
    const dx = e.clientX - controlsState.lastX, dy = e.clientY - controlsState.lastY;
    controlsState.lastX = e.clientX; controlsState.lastY = e.clientY;
    if (controlsState.dragging) {
      if (controlsState.right) { controlsState.panX -= dx * 0.15; controlsState.panY += dy * 0.15; }
      else { controlsState.theta -= dx * 0.005; controlsState.phi = Math.max(0.1, Math.min(Math.PI - 0.1, controlsState.phi - dy * 0.005)); }
      updateCamera();
    }
    // hover picking
    doPick(e, canvas, container);
  };
  const onWheel = (e) => { e.preventDefault(); controlsState.radius = Math.max(20, Math.min(800, controlsState.radius * (e.deltaY > 0 ? 1.1 : 0.9))); updateCamera(); };
  const onCtx = (e) => e.preventDefault();
  canvas.addEventListener("mousedown", onDown);
  window.addEventListener("mouseup", onUp);
  canvas.addEventListener("mousemove", onMove);
  canvas.addEventListener("wheel", onWheel, { passive: false });
  canvas.addEventListener("contextmenu", onCtx);
  canvas._watsCleanup = () => {
    canvas.removeEventListener("mousedown", onDown);
    window.removeEventListener("mouseup", onUp);
    canvas.removeEventListener("mousemove", onMove);
    canvas.removeEventListener("wheel", onWheel);
    canvas.removeEventListener("contextmenu", onCtx);
  };
}

function doPick(e, canvas, container) {
  if (!raycaster || !camera || !mouse || !tooltipEl) return;
  const rect = canvas.getBoundingClientRect();
  mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
  mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(mouse, camera);
  const hits = raycaster.intersectObjects(pickables, false);
  if (hits.length) {
    const h = hits[0];
    const ud = h.object.userData || {};
    const d = ud.data || {};
    const lines = [];
    if (ud.cat) lines.push(`Catalog: ${ud.cat}`);
    if (d.id != null) lines.push(`ID: ${d.id}`);
    if (Number.isFinite(Number(d.ra))) lines.push(`RA: ${Number(d.ra).toFixed(6)}°`);
    if (Number.isFinite(Number(d.dec))) lines.push(`Dec: ${Number(d.dec).toFixed(6)}°`);
    if (d.mag != null) lines.push(`mag: ${Number(d.mag).toFixed(3)}`);
    if (d.w1 != null) lines.push(`W1: ${Number(d.w1).toFixed(3)}`);
    if (d.parallax != null) lines.push(`parallax: ${Number(d.parallax).toFixed(3)} mas`);
    if (d.pmra != null) lines.push(`pmRA: ${Number(d.pmra).toFixed(2)} mas/yr`);
    if (d.pmdec != null) lines.push(`pmDec: ${Number(d.pmdec).toFixed(2)} mas/yr`);
    if (ud.kind === "pm") lines.push("Proper-motion vector (extrapolated)");
    tooltipEl.hidden = false;
    tooltipEl.style.left = `${e.clientX - rect.left + 12}px`;
    tooltipEl.style.top = `${e.clientY - rect.top + 12}px`;
    tooltipEl.textContent = lines.join("\n") || "source";
  } else {
    tooltipEl.hidden = true;
  }
}

function rebuildScene(container, p) {
  if (!scene || !rootGroup || !THREE) return;
  // clear
  while (rootGroup.children.length) {
    const c = rootGroup.children.pop();
    if (c.geometry) c.geometry.dispose();
    if (c.material) { if (Array.isArray(c.material)) c.material.forEach(m=>m.dispose()); else c.material.dispose(); }
  }
  pickables = [];

  const showFrame = !!container.querySelector("#s3-frame")?.checked;
  const showGaia = !!container.querySelector("#s3-gaia")?.checked;
  const showCatwise = !!container.querySelector("#s3-catwise")?.checked;
  const showPM = !!container.querySelector("#s3-pm")?.checked;
  const years = Number(container.querySelector("#s3-pm-years")?.value || 100);
  const depthEx = Number(container.querySelector("#s3-depth")?.value || 1);

  // scale: map field-of-view degrees to scene units
  const fovDeg = Number(currentManifest?.fov_deg || p.fov || 0.15);
  const sceneScale = 100 / Math.max(fovDeg, 0.01); // 100 units across field

  // frame plane
  const img = showFrame ? pickFrameImage(currentManifest) : null;
  if (img) {
    const texLoader = new THREE.TextureLoader();
    const mat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.95, side: THREE.DoubleSide });
    const plane = new THREE.Mesh(new THREE.PlaneGeometry(fovDeg * sceneScale, fovDeg * sceneScale), mat);
    plane.rotation.x = -Math.PI / 2; // lay flat on XZ
    plane.position.set(0, -0.5, 0);
    rootGroup.add(plane);
    texLoader.load(img.url, (tex) => { mat.map = tex; mat.needsUpdate = true; }, undefined, () => { /* ignore */ });
  }

  // axes/grid and field boundary (RA/Dec tangent) - kept visible so field is never black
  const grid = new THREE.GridHelper(fovDeg * sceneScale, 8, 0x557799, 0x334455);
  grid.position.y = -0.6;
  rootGroup.add(grid);
  const b = (fovDeg * sceneScale) / 2;
  const borderGeo = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(-b,-0.55,-b), new THREE.Vector3(b,-0.55,-b),
    new THREE.Vector3(b,-0.55,b), new THREE.Vector3(-b,-0.55,b), new THREE.Vector3(-b,-0.55,-b)
  ]);
  rootGroup.add(new THREE.Line(borderGeo, new THREE.LineBasicMaterial({ color: 0x6fa8dc, transparent: true, opacity: 0.9 })));
  const axMat = new THREE.LineBasicMaterial({ color: 0x88aacc, transparent: true, opacity: 0.6 });
  rootGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-b,-0.55,0), new THREE.Vector3(b,-0.55,0)]), axMat));
  rootGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,-0.55,-b), new THREE.Vector3(0,-0.55,b)]), axMat));
  // small center marker
  const cm = new THREE.Mesh(new THREE.SphereGeometry(0.8, 12, 12), new THREE.MeshBasicMaterial({ color: 0xffcc66 }));
  cm.position.set(0, -0.5, 0); rootGroup.add(cm);

  const gaia = (currentOverlay && currentOverlay.gaia) ? currentOverlay.gaia : [];
  const catwise = (currentOverlay && currentOverlay.catwise) ? currentOverlay.catwise : [];

  const addSources = (rows, color, cat, baseSize) => {
    for (const d of rows) {
      if (d && d.inside === false) continue;
      const ra = Number(d.ra), dec = Number(d.dec);
      if (!Number.isFinite(ra) || !Number.isFinite(dec)) continue;
      const [dx, dy] = tangentXY(ra, dec, p.ra, p.dec);
      if (!Number.isFinite(dx) || !Number.isFinite(dy)) continue;
      // depth from parallax (mas): d_pc ~ 1000/parallax -> compress to scene z
      let zDepth = 0;
      const par = Number(d.parallax);
      if (Number.isFinite(par) && par > 0.05) {
        const distPc = 1000 / par; // pc
        zDepth = Math.log10(Math.max(distPc, 1)) * 8 * depthEx; // log-scaled depth exaggeration
      } else if (cat === "CatWISE") {
        zDepth = 18 * depthEx; // place unknown-parallax IR sources deeper as a visual cue
      }
      const sx = -dx * sceneScale; // RA increases to the left on sky
      const sy = zDepth;
      const sz = dy * sceneScale;  // Dec increases upward -> +z mapped visually
      const size = magSize(d.mag ?? d.w1, baseSize);
      const geo = new THREE.SphereGeometry(size * 0.5, 10, 10);
      const mat = new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: 0.35, roughness: 0.4, metalness: 0.1 });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(sx, sy, sz);
      mesh.userData = { data: d, cat, kind: "source" };
      rootGroup.add(mesh);
      pickables.push(mesh);
    }
  };

  if (showGaia) addSources(gaia, 0x4fc3ff, "Gaia", 1.2);
  if (showCatwise) addSources(catwise, 0xff9f43, "CatWISE", 1.3);

  if (showPM) {
    const pmMat = new THREE.LineBasicMaterial({ color: 0xc7f36b, transparent: true, opacity: 0.9 });
    const addPM = (rows, cat) => {
      for (const d of rows) {
        if (d && d.inside === false) continue;
        const ra = Number(d.ra), dec = Number(d.dec), pmra = Number(d.pmra), pmdec = Number(d.pmdec);
        if (![ra,dec,pmra,pmdec].every(Number.isFinite)) continue;
        if (Math.abs(pmra) < 0.01 && Math.abs(pmdec) < 0.01) continue;
        const [dx0, dy0] = tangentXY(ra, dec, p.ra, p.dec);
        const dra = (pmra / 3.6e6) * years / Math.max(Math.cos(dec * Math.PI / 180), 0.05);
        const ddec = (pmdec / 3.6e6) * years;
        const [dx1, dy1] = tangentXY(ra + dra, dec + ddec, p.ra, p.dec);
        if (![dx0,dy0,dx1,dy1].every(Number.isFinite)) continue;
        let zDepth = 0;
        const par = Number(d.parallax);
        if (Number.isFinite(par) && par > 0.05) zDepth = Math.log10(Math.max(1000 / par, 1)) * 8 * depthEx;
        else if (cat === "CatWISE") zDepth = 18 * depthEx;
        const p0 = new THREE.Vector3(-dx0*sceneScale, zDepth, dy0*sceneScale);
        const p1 = new THREE.Vector3(-dx1*sceneScale, zDepth, dy1*sceneScale);
        const geo = new THREE.BufferGeometry().setFromPoints([p0, p1]);
        const line = new THREE.Line(geo, pmMat);
        line.userData = { data: d, cat, kind: "pm" };
        rootGroup.add(line);
        // arrow head
        const head = new THREE.Mesh(new THREE.ConeGeometry(0.7, 2.2, 6), new THREE.MeshBasicMaterial({ color: 0xc7f36b }));
        const dir = new THREE.Vector3().subVectors(p1, p0); const len = dir.length() || 1; dir.normalize();
        head.position.copy(p1);
        head.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
        rootGroup.add(head);
      }
    };
    if (showGaia) addPM(gaia, "Gaia");
    if (showCatwise) addPM(catwise, "CatWISE");
  }
}

function resizeRenderer(container) {
  if (!renderer || !camera) return;
  const wrap = container.querySelector("#sky3d-wrap");
  if (!wrap) return;
  const w = wrap.clientWidth, h = wrap.clientHeight;
  if (w > 0 && h > 0) { renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
}

function startLoop() {
  const loop = () => {
    raf = requestAnimationFrame(loop);
    if (renderer && scene && camera) renderer.render(scene, camera);
  };
  loop();
}

function disposeThree() {
  if (raf) cancelAnimationFrame(raf); raf = null;
  if (renderer) { try { renderer.dispose(); } catch (_) {} renderer = null; }
  const canvas = document.querySelector("#sky3d-canvas");
  if (canvas && canvas._watsCleanup) try { canvas._watsCleanup(); } catch (_) {}
  scene = null; camera = null; rootGroup = null; pickables = []; raycaster = null; mouse = null; controlsState = null;
}

export function destroy() {
  if (resizeObs) { try { resizeObs.disconnect(); } catch (_) {} resizeObs = null; }
  disposeThree();
  tooltipEl = null; currentOverlay = null; currentManifest = null;
}
