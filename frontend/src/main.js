// Project WATS — main application entry point (Phase 2)
// View router: Sky/Blink, Coverage, Spectrum, Light curve, Sources, Quality, Export.

import { fetchJson } from "/api.js";
import {
  clamp, lerp, fmt, fmtSci, fmtTime, fmtDeg, parseRa, parseDec,
  escapeHtml, FALLBACK_PRESETS,
} from "/state.js";
import { initRouter, registerView, setView } from "/router.js";
import { render as renderSky, destroy as destroySky } from "/views/sky.js";
import { render as renderCoverage, destroy as destroyCoverage } from "/views/coverage.js";
import { render as renderSpectrum, destroy as destroySpectrum } from "/views/spectrum.js";
import { render as renderLightCurve, destroy as destroyLightCurve } from "/views/lightcurve.js";
import { render as renderSources, destroy as destroySources } from "/views/sources.js";
import { render as renderQuality, destroy as destroyQuality } from "/views/quality.js";
import { render as renderExport, destroy as destroyExport } from "/views/export.js";
import { render as renderSpectralCube, destroy as destroySpectralCube } from "/views/spectral_cube.js";
import { render as renderSkyDeckgl, destroy as destroySkyDeckgl } from "/views/sky_deckgl.js";
import { render as renderAnnotations, destroy as destroyAnnotations } from "/views/annotations.js";
import { render as renderCollections, destroy as destroyCollections } from "/views/collections.js";
import { render as renderActivity, destroy as destroyActivity } from "/views/activity.js";
import { render as renderMovingObjects, destroy as destroyMovingObjects } from "/views/moving_objects.js";
import { render as renderSpectralFit, destroy as destroySpectralFit } from "/views/spectral_fit.js";
import { render as renderWatchlist, destroy as destroyWatchlist } from "/views/watchlist.js";

const viewModules = {
  sky: { render: renderSky, destroy: destroySky },
  coverage: { render: renderCoverage, destroy: destroyCoverage },
  spectrum: { render: renderSpectrum, destroy: destroySpectrum },
  lightcurve: { render: renderLightCurve, destroy: destroyLightCurve },
  sources: { render: renderSources, destroy: destroySources },
  quality: { render: renderQuality, destroy: destroyQuality },
  export: { render: renderExport, destroy: destroyExport },
  cube: { render: renderSpectralCube, destroy: destroySpectralCube },
  skydeck: { render: renderSkyDeckgl, destroy: destroySkyDeckgl },
  annotations: { render: renderAnnotations, destroy: destroyAnnotations },
  collections: { render: renderCollections, destroy: destroyCollections },
  activity: { render: renderActivity, destroy: destroyActivity },
  movingobjects: { render: renderMovingObjects, destroy: destroyMovingObjects },
  spectralfit: { render: renderSpectralFit, destroy: destroySpectralFit },
  watchlist: { render: renderWatchlist, destroy: destroyWatchlist },
};

for (const [name, mod] of Object.entries(viewModules)) {
  registerView(name, mod);
}

(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const viewContainer = $("#view-container");
  const navLinks = $$(".nav-link");
  const form = $("#query-form");
  const presetsWrap = $("#presets");
  const copyLink = $("#copy-link");

  initRouter(viewContainer, navLinks);

  function readForm() {
    return {
      ra: $("#ra").value.trim(),
      dec: $("#dec").value.trim(),
      fov: Number($("#fov").value),
      max_frames: Number($("#max-frames").value),
      detector: $("#detector").value || "",
      name: "custom",
    };
  }

  function paramsFromForm() {
    const f = readForm();
    const ra = parseRa(f.ra);
    const dec = parseDec(f.dec);
    if (!Number.isFinite(ra) || !Number.isFinite(dec)) return null;
    return { ra, dec, fov: f.fov, max_frames: f.max_frames, detector: f.detector || "", name: f.name || "custom" };
  }

  function setFormFromParams(params) {
    if (!params) return;
    $("#ra").value = Number(params.ra).toFixed(6);
    $("#dec").value = Number(params.dec).toFixed(6);
    const fovEl = $("#fov");
    if ([...fovEl.options].some((o) => Number(o.value) === params.fov)) fovEl.value = String(params.fov);
    const mfEl = $("#max-frames");
    if (![...mfEl.options].some((o) => o.value === String(params.max_frames))) {
      const opt = document.createElement("option"); opt.value = String(params.max_frames); opt.textContent = String(params.max_frames);
      mfEl.appendChild(opt);
    }
    mfEl.value = String(params.max_frames);
    if (params.detector) {
      const detEl = $("#detector");
      if (![...detEl.options].some((o) => o.value === params.detector)) {
        const opt = document.createElement("option"); opt.value = params.detector; opt.textContent = params.detector;
        detEl.appendChild(opt);
      }
      detEl.value = params.detector;
    }
  }

  async function startLoad(useRefresh = false) {
    const params = paramsFromForm();
    if (!params) {
      showError("Enter a valid Right Ascension and Declination.");
      return;
    }
    const q = new URLSearchParams(location.search);
    q.set("view", "sky");
    q.delete("key");
    q.set("ra", Number(params.ra).toFixed(7));
    q.set("dec", Number(params.dec).toFixed(7));
    q.set("fov", String(params.fov));
    q.set("max_frames", String(params.max_frames));
    if (params.detector) q.set("detector", params.detector); else q.delete("detector");
    if (useRefresh) q.set("refresh", "1"); else q.delete("refresh");
    history.replaceState(null, "", `${location.pathname}?${q.toString()}`);
    await setView("sky");
  }

  function showError(message) {
    const box = $("#error-box");
    const text = $("#error-text");
    if (box) { text.textContent = message || "Unknown error."; box.hidden = false; }
    else { console.error(message); }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    startLoad(false);
  });

  for (const id of ["#ra", "#dec"]) {
    const el = $(id);
    if (el) el.addEventListener("input", () => {
      const t = $("#target-title-display");
      if (t && t.textContent && t.textContent !== "No target loaded" && t.textContent !== "Loading…") t.textContent = "Custom field";
    });
  }

  $("#refresh-btn").addEventListener("click", () => startLoad(true));

async function loadPresets() {
        let presets = [];
        let serverPresets = [];
        let usedFallback = false;
        try {
            const res = await fetchJson("/api/presets");
            if (res.ok && res.data && Array.isArray(res.data.presets) && res.data.presets.length) {
                serverPresets = res.data.presets;
            }
        } catch (_) { /* fall back to built-in */ }
        // If the server returned presets, trust them and skip the fallback
        // entirely so the API can override any built-in entry.
        if (serverPresets.length) {
            presets = serverPresets;
        } else {
            presets = FALLBACK_PRESETS.slice();
            usedFallback = true;
        }
        presetsWrap.innerHTML = '<span class="label">Try</span>';
        for (const p of presets) {
            const b = document.createElement("button");
            b.type = "button"; b.className = "preset";
            b.innerHTML = `${escapeHtml(p.name)}<small>${escapeHtml(p.description || "")}</small>`;
            b.addEventListener("click", () => {
                $("#ra").value = typeof p.ra === "number" ? p.ra.toFixed(6) : String(p.ra_text || p.ra);
                $("#dec").value = typeof p.dec === "number" ? p.dec.toFixed(6) : String(p.dec_text || p.dec);
                $("#fov").value = String(p.fov || 0.15);
                startLoad(false);
            });
            presetsWrap.appendChild(b);
        }
        if (usedFallback) {
            const note = document.createElement("div");
            note.className = "muted";
            note.style.cssText = "font-size:0.72rem;margin-top:6px;";
            note.textContent = "⚠ Server presets unavailable — showing built-in list.";
            presetsWrap.appendChild(note);
        }
    }
  const q = new URLSearchParams(location.search);
  if (q.has("ra") && q.has("dec")) {
    setFormFromParams({ ra: q.get("ra"), dec: q.get("dec"), fov: q.get("fov") || 0.15, max_frames: q.get("max_frames") || 6, name: q.get("name") || "custom" });
  }

  // ---- Sidebar navigation ----
  const menuToggle = $("#menu-toggle");
  const sidebar = $("#sidebar");
  const overlay = $("#sidebar-overlay");
  const copyLinkSidebar = $("#copy-link-sidebar");
  const viewEls = document.body;

  function openSidebar() {
    sidebar.classList.add("open");
    overlay.classList.add("open");
    viewEls.classList.add("sidebar-open");
    menuToggle.setAttribute("aria-expanded", "true");
  }
  function closeSidebar() {
    sidebar.classList.remove("open");
    overlay.classList.remove("open");
    viewEls.classList.remove("sidebar-open");
    menuToggle.setAttribute("aria-expanded", "false");
  }
  function toggleSidebar() {
    if (sidebar.classList.contains("open")) closeSidebar(); else openSidebar();
  }

  if (menuToggle) menuToggle.addEventListener("click", toggleSidebar);
  if (overlay) overlay.addEventListener("click", closeSidebar);

  // Sidebar nav links switch views
  for (const link of $$(".nav-link")) {
    link.addEventListener("click", () => {
      const view = link.dataset.view;
      if (view) setView(view);
      closeSidebar();
    });
  }

  // ---- Display mode toggle (Default / IR grayscale / High contrast) ----
  const DISPLAY_MODES = [
    { key: "default", label: "Default" },
    { key: "ir-gray", label: "IR grayscale" },
    { key: "hc", label: "High contrast" },
  ];
  const displayBtn = $("#display-mode");
  let displayIdx = 0;
  function applyDisplayMode(idx) {
    const mode = DISPLAY_MODES[idx];
    if (mode.key === "default") document.documentElement.removeAttribute("data-display");
    else document.documentElement.setAttribute("data-display", mode.key);
    if (displayBtn) displayBtn.textContent = `Display: ${mode.label}`;
    try { localStorage.setItem("wats-display-mode", mode.key); } catch (_) { /* ignore */ }
  }
  try {
    const saved = localStorage.getItem("wats-display-mode");
    const idx = DISPLAY_MODES.findIndex((m) => m.key === saved);
    if (idx >= 0) displayIdx = idx;
  } catch (_) { /* ignore */ }
  applyDisplayMode(displayIdx);
  if (displayBtn) displayBtn.addEventListener("click", () => {
    displayIdx = (displayIdx + 1) % DISPLAY_MODES.length;
    applyDisplayMode(displayIdx);
  });

  // Copy link (sidebar + topbar)
  const doCopy = async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      const targets = [copyLink, copyLinkSidebar].filter(Boolean);
      const olds = targets.map((b) => b.textContent);
      targets.forEach((b) => { b.textContent = "Copied!"; });
      setTimeout(() => { targets.forEach((b, i) => { b.textContent = olds[i]; }); }, 1400);
    } catch (_) {
      [copyLink, copyLinkSidebar].filter(Boolean).forEach((b) => { b.textContent = location.href; });
    }
  };
  if (copyLink) copyLink.addEventListener("click", doCopy);
  if (copyLinkSidebar) copyLinkSidebar.addEventListener("click", doCopy);

  // Close sidebar when a route/view changes (popstate)
  window.addEventListener("popstate", closeSidebar);

})();
