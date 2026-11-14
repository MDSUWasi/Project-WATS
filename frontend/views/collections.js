// Project WATS — collections view (public, no accounts)
// Shareable saved collections of targets.

import { fetchJson } from "/api.js";
import { escapeHtml } from "/state.js";

let abortCtrl = null;

export function render(container) {
  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Saved Collections</h2>
        <div class="coords-meta">Shareable target groups, no account required</div>
      </div>
      <div id="col-wrap"></div>
    </div>
  `;

  const wrap = container.querySelector("#col-wrap");

  // Create form
  wrap.innerHTML = `
    <div class="col-form" style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;">
      <input type="text" id="col-name" placeholder="Collection name" style="flex:1;padding:8px 11px;background:#0c141d;border:1px solid #2a3a4d;border-radius:9px;color:var(--text);">
      <input type="text" id="col-keys" placeholder="target keys (comma-separated)" style="flex:2;padding:8px 11px;background:#0c141d;border:1px solid #2a3a4d;border-radius:9px;color:var(--text);">
      <button class="primary" id="col-add">Create</button>
    </div>
    <div id="col-list"></div>
  `;

  const nameEl = wrap.querySelector("#col-name");
  const keysEl = wrap.querySelector("#col-keys");
  const addBtn = wrap.querySelector("#col-add");
  const listEl = wrap.querySelector("#col-list");

  async function load() {
    listEl.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Loading collections…</div>`;
    const res = await fetchJson("/api/public/collections");
    if (!res.ok || !res.data) {
      listEl.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(res.data?.error))}</div>`;
      return;
    }
    const cols = res.data.collections || [];
    if (!cols.length) {
      listEl.innerHTML = `<div class="muted">No collections yet.</div>`;
      return;
    }
    listEl.innerHTML = cols.map((c) => {
      const keys = Array.isArray(c.target_keys) ? c.target_keys : [];
      return `
        <div class="col-item" style="background:#0f1821;border:1px solid var(--line-soft);border-radius:9px;padding:10px 12px;margin-bottom:8px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:4px;">
            <span style="font-weight:600;color:var(--accent-2);">${escapeHtml(String(c.name || "Untitled"))}</span>
            <span style="font-size:0.72rem;color:var(--muted-2);">${keys.length} target${keys.length === 1 ? "" : "s"}</span>
            ${c.id ? `<button class="ghost" style="font-size:0.7rem;padding:2px 6px;" data-id="${c.id}">Delete</button>` : ""}
          </div>
          <div style="color:var(--muted);font-size:0.8rem;margin-bottom:6px;word-break:break-all;">${keys.map((k) => `<code style="color:var(--accent-2);">${escapeHtml(String(k))}</code>`).join(" ")}</div>
          <div style="display:flex;gap:8px;">
            <button class="ghost" data-id="${c.id}" style="font-size:0.75rem;padding:4px 8px;">Load targets</button>
            <span style="font-size:0.72rem;color:var(--muted-2);">updated ${new Date(c.updated).toLocaleDateString()}</span>
          </div>
        </div>
      `;
    }).join("");

    for (const btn of listEl.querySelectorAll("button[data-id]")) {
      btn.addEventListener("click", async () => {
        const id = Number(btn.dataset.id);
        const res = await fetchJson(`/api/public/collections/${id}`);
        if (res.ok && res.data && res.data.collection) {
          const c = res.data.collection;
          const keys = Array.isArray(c.target_keys) ? c.target_keys : [];
          if (keys.length) {
            // Load first target into sky view
            const qp = new URLSearchParams(location.search);
            qp.set("view", "sky");
            qp.set("key", keys[0]);
            history.replaceState(null, "", `${location.pathname}?${qp.toString()}`);
            alert(`Loaded ${keys.length} targets. First target: ${keys[0]}\n(Use URL ?key= to load specific targets)`);
          }
        }
      });
      if (btn.textContent.trim() === "Delete") {
        btn.addEventListener("click", async () => {
          const id = Number(btn.dataset.id);
          if (abortCtrl) abortCtrl.abort();
          abortCtrl = new AbortController();
          const res = await fetchJson(`/api/public/collections/${id}`, { method: "DELETE", signal: abortCtrl.signal });
          if (res.ok) load();
        });
      }
    }
  }

  addBtn.addEventListener("click", async () => {
    const name = nameEl.value.trim();
    const keys = keysEl.value.trim().split(",").map((k) => k.trim()).filter(Boolean);
    if (!name || !keys.length) return;
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    const res = await fetchJson("/api/public/collections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, target_keys: keys }),
      signal: abortCtrl.signal,
    });
    if (res.ok && res.data) {
      nameEl.value = "";
      keysEl.value = "";
      load();
    } else {
      alert(res.data?.error || "Failed to create collection");
    }
  });

  load();
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
}
