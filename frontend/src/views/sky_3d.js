// Project WATS — three.js sky view
// Whole-sky / field visualization using WebGL (three.js).
// Loads three.js from a CDN at runtime with graceful fallback if the
// module is unreachable or WebGL is unavailable.

import { escapeHtml } from "/state.js";

let raf = null;
let scene = null;
let camera = null;
let renderer = null;
let starField = null;
let orbitPath = null;
let isVisible = false;

function ensureStyle() {
  if (document.getElementById("sky-3d-style")) return;
  const s = document.createElement("style");
  s.id = "sky-3d-style";
  s.textContent = `
    .sky-3d-wrap { position: relative; width: 100%; height: 560px; background: #02040a; border: 1px solid var(--line); border-radius: var(--radius-sm); overflow: hidden; display: flex; align-items: center; justify-content: center; }
    .sky-3d-canvas { width: 100%; height: 100%; display: block; }
    .sky-3d-fallback { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--muted); text-align: center; padding: 24px; background: rgba(2, 4, 10, 0.9); }
    .sky-3d-fallback .retry { margin-top: 12px; padding: 8px 14px; border: 1px solid var(--line); background: var(--panel-solid); color: var(--text); border-radius: var(--radius-sm); cursor: pointer; }
    .sky-3d-fallback .retry:hover { background: var(--bg-2); border-color: var(--terracotta); }
  `;
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
        <h2>three.js / Sky (3D): ${escapeHtml(name)}</h2>
        <div class="coords-meta">${escapeHtml(ra ? `RA ${Number(ra).toFixed(5)}° · Dec ${Number(dec).toFixed(5)}°` : "—")}</div>
      </div>
      <div class="sky-3d-wrap" id="sky-3d-wrap">
        <canvas class="sky-3d-canvas" id="sky-3d-canvas"></canvas>
        <div class="sky-3d-fallback" id="sky-3d-fallback" hidden>
          <div style="font-size:1.1rem;color:var(--text);margin-bottom:8px;">WebGL / three.js</div>
          <div style="font-size:0.86rem;max-width:340px;">Loading three.js or WebGL unavailable.</div>
          <button class="retry" id="sky-3d-retry">Retry loading</button>
        </div>
      </div>
      <div style="margin-top:8px;font-size:12px;color:var(--muted-2);">three.js provides a WebGL 3D star field background behind the blink canvas.</div>
    </div>
  `;

  const wrap = container.querySelector("#sky-3d-wrap");
  const canvas = container.querySelector("#sky-3d-canvas");
  const fallback = container.querySelector("#sky-3d-fallback");
  const retryBtn = container.querySelector("#sky-3d-retry");
  if (retryBtn) retryBtn.addEventListener("click", () => { initThree(canvas, wrap, fallback); });
  if (canvas) initThree(canvas, wrap, fallback);
  isVisible = true;
}

function initThree(canvas, wrap, fallback) {
  if (typeof THREE === "undefined") {
    fallback.hidden = false;
    loadThree().then(() => { initThree(canvas, wrap, fallback); }).catch(() => {});
    return;
  }
  try {
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x02040a);
    camera = new THREE.PerspectiveCamera(55, canvas.clientWidth / Math.max(canvas.clientHeight,1), 0.1, 2000);
    camera.position.set(0, 0, 300);
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
    renderer.setSize(canvas.clientWidth, canvas.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    const starCount = 4096;
    const positions = new Float32Array(starCount * 3);
    const colors = new Float32Array(starCount * 3);
    for (let i = 0; i < starCount; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(1 - Math.random() * 2);
      const r = 150 + Math.random() * 200;
      positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
      positions[i * 3 + 2] = r * Math.cos(phi);
      const b = 0.4 + Math.random() * 0.6;
      colors[i * 3] = 0.5 + b * 0.5;
      colors[i * 3 + 1] = 0.3 + b * 0.4;
      colors[i * 3 + 2] = 0.6 + b * 0.4;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    const mat = new THREE.PointsMaterial({ size: 1.2, vertexColors: true, transparent: true, opacity: 0.85, sizeAttenuation: true });
    starField = new THREE.Points(geo, mat);
    scene.add(starField);
    const ringGeo = new THREE.RingGeometry(120, 128, 64);
    const ringMat = new THREE.MeshBasicMaterial({ color: 0x333344, transparent: true, opacity: 0.25, side: THREE.DoubleSide });
    orbitPath = new THREE.Mesh(ringGeo, ringMat);
    orbitPath.rotation.x = Math.PI / 2;
    scene.add(orbitPath);
    animate();
    wrap.classList.add("loaded");
    if (fallback) fallback.hidden = true;
  } catch (err) {
    console.error("three.js init failed", err);
    if (fallback) fallback.hidden = false;
  }
}

function animate() {
  if (!isVisible) return;
  raf = requestAnimationFrame(animate);
  if (starField) starField.rotation.y += 0.0006;
  if (orbitPath) orbitPath.rotation.z += 0.001;
  if (renderer && scene && camera) renderer.render(scene, camera);
}

export function destroy() {
  isVisible = false;
  if (raf) cancelAnimationFrame(raf);
  raf = null;
  if (renderer) { try { renderer.dispose(); } catch (_) {} renderer = null; }
  scene = null; camera = null; starField = null; orbitPath = null;
}

let loadPromise = null;
function loadThree() {
  // three.js is loaded as a regular script tag in index.html.
  // If it hasn't loaded yet, inject it dynamically as a fallback.
  if (typeof THREE !== "undefined") return Promise.resolve();
  if (loadPromise) return loadPromise;
  loadPromise = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "./lib/three.min.js";
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("three.js CDN load failed"));
    document.head.appendChild(s);
  });
  return loadPromise;
}
