// Project WATS — Watchlist / alerts view
// Saved coordinates; poll for new SPHEREx visits.

import { fetchJson } from "/api.js";
import { escapeHtml } from "/state.js";

let abortCtrl = null;
let pollTimer = null;

export function render(container) {
  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Watchlist &amp; Alerts</h2>
        <div class="coords-meta">Saved coordinates — alerts when new SPHEREx data appears</div>
      </div>
      <div id="watch-wrap"></div>
    </div>
  `;
  const wrap = container.querySelector("#watch-wrap");
  wrap.innerHTML = `
    <div class="watch-toolbar" style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap;">
      <button class="primary" id="watch-add-current">Save current view</button>
      <button class="ghost" id="watch-check-all">Check all now</button>
      <span id="watch-info" class="muted" style="margin-left:auto;font-size:12px;"></span>
    </div>
    <div id="watch-list"></div>
  `;
  const addBtn = wrap.querySelector("#watch-add-current");
  const checkBtn = wrap.querySelector("#watch-check-all");
  const infoEl = wrap.querySelector("#watch-info");
  const listEl = wrap.querySelector("#watch-list");

  async function load() {
    const res = await fetchJson("/api/public/watchlist");
    if (!res.ok || !res.data) { listEl.innerHTML = `<div class="panel error">Error loading watchlist.</div>`; return; }
    const items = res.data.watchlist || [];
    if (!items.length) {
      listEl.innerHTML = `<div class="muted">No saved coordinates yet. Load a target in the Sky view and click "Save current view".</div>`;
      return;
    }
    listEl.innerHTML = `
      <table class="visit-table">
        <thead><tr><th>Name</th><th>RA</th><th>Dec</th><th>FOV</th><th>Release</th><th>Last check</th><th>New visits</th><th></th></tr></thead>
        <tbody>
          ${items.map((w) => `
            <tr>
              <td>${escapeHtml(w.name || "custom")}</td>
              <td>${Number(w.ra).toFixed(5)}</td>
              <td>${Number(w.dec).toFixed(5)}</td>
              <td>${Number(w.fov).toFixed(3)}°</td>
              <td>${escapeHtml(w.release || "dr1")}</td>
              <td>${w.last_check ? new Date(w.last_check).toLocaleString() : "never"}</td>
              <td id="new-${w.id}">${w.last_visit_mjd ? "—" : "—"}</td>
              <td>
                <button class="ghost" style="font-size:0.7rem;padding:2px 6px;" data-check="${w.id}">Check</button>
                <button class="ghost" style="font-size:0.7rem;padding:2px 6px;" data-del="${w.id}">Delete</button>
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
    for (const btn of listEl.querySelectorAll("button[data-del]")) {
      btn.addEventListener("click", async () => {
        const id = Number(btn.dataset.del);
        await fetchJson(`/api/public/watchlist/${id}`, { method: "DELETE" });
        load();
      });
    }
    for (const btn of listEl.querySelectorAll("button[data-check]")) {
      btn.addEventListener("click", () => checkOne(Number(btn.dataset.check)));
    }
  }

  async function checkOne(id) {
    const cell = listEl.querySelector(`#new-${id}`);
    if (cell) cell.textContent = "checking…";
    const res = await fetchJson(`/api/public/watchlist/${id}/check`);
    if (res.ok && res.data) {
      if (cell) {
        if (res.data.alert) {
          cell.innerHTML = `<span style="color:#ffa94d;font-weight:600;">${res.data.new_visits} new</span>`;
        } else {
          cell.textContent = "0 new";
        }
      }
    } else {
      if (cell) cell.textContent = "error";
    }
  }

  addBtn.addEventListener("click", () => {
    const q = new URLSearchParams(location.search);
    const ra = q.get("ra"); const dec = q.get("dec");
    if (!ra || !dec) { alert("Load a target in the Sky view first (need RA/Dec in the URL)."); return; }
    const fov = Number(q.get("fov") || 0.15);
    const name = q.get("name") || "custom";
    const release = q.get("release") || "dr1";
    const max_frames = Number(q.get("max_frames") || 6);
    fetchJson("/api/public/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ra: Number(ra), dec: Number(dec), fov, name, release, max_frames }),
    }).then((res) => {
      if (res.ok) { load(); infoEl.textContent = "Saved!"; }
      else { alert(res.data?.error || "Failed to save"); }
    });
  });

  checkBtn.addEventListener("click", async () => {
    const res = await fetchJson("/api/public/watchlist");
    if (!res.ok || !res.data) return;
    const items = res.data.watchlist || [];
    for (const w of items) await checkOne(w.id);
  });

  // Auto-poll every 60s.
  pollTimer = setInterval(() => {
    fetchJson("/api/public/watchlist").then((res) => {
      if (res.ok && res.data && res.data.watchlist.length) {
        // Light auto-check: just refresh the list silently.
        load();
      }
    });
  }, 60000);

  load();
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}