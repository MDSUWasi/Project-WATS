// Project WATS — view router
// Switches between analysis views based on ?view= URL param.
// Views register themselves and render into a shared container.

import { escapeHtml } from "/state.js";

const views = new Map();
let current = null;
let container = null;
let navLinks = null;
let _settingView = false;

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
    // Write the URL only after the view has started rendering so the URL
    // always reflects what the user actually sees.  Callers that also write
    // history.replaceState themselves should use switchView directly.
    if (name === current) return Promise.resolve();
    const q = new URLSearchParams(location.search);
    const desired = q.get("view");
    if (desired === name) return switchView(name);
    q.set("view", name);
    const url = `${location.pathname}?${q.toString()}`;
    // Guard against rapid successive calls causing history loops.
    if (_settingView) return switchView(name);
    _settingView = true;
    return switchView(name).then(() => {
        history.replaceState(null, "", url);
    }).finally(() => {
        _settingView = false;
    });
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
