// Project WATS — view router
// Switches between analysis views based on ?view= URL param.
// Views register themselves and render into a shared container.

import { escapeHtml } from "/state.js";

const views = new Map();
let current = null;
let container = null;
let navLinks = null;

export function registerView(name, { render, destroy, title }) {
  views.set(name, { render, destroy, title });
}

export function getCurrentView() {
  return current;
}

export function initRouter(el, navEl) {
  container = el;
  navLinks = navEl;
  window.addEventListener("popstate", () => {
    const name = readView();
    if (name !== current) switchView(name);
  });
  const initial = readView();
  if (initial) switchView(initial);
}

function readView() {
  const q = new URLSearchParams(location.search);
  return q.get("view") || "sky";
}

export function setView(name) {
  const q = new URLSearchParams(location.search);
  q.set("view", name);
  history.replaceState(null, "", `${location.pathname}?${q.toString()}`);
  return switchView(name);
}

async function switchView(name) {
  if (current) {
    const prev = views.get(current);
    if (prev && prev.destroy) prev.destroy();
  }
  current = name;
  updateNav();
  const view = views.get(name);
  if (!view) {
    container.innerHTML = `<div class="panel error">Unknown view: ${escapeHtml(name)}</div>`;
    return;
  }
  container.innerHTML = "";
  try {
    if (view.render) await view.render(container);
  } catch (err) {
    container.innerHTML = `<div class="panel error">View failed: ${escapeHtml(String(err))}</div>`;
  }
}

function updateNav() {
  if (!navLinks) return;
  for (const a of navLinks) {
    const viewName = a.dataset.view;
    if (viewName === current) {
      a.setAttribute("aria-current", "true");
      a.classList.add("active");
    } else {
      a.removeAttribute("aria-current");
      a.classList.remove("active");
    }
  }
}
