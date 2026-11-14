// Project WATS — annotations view (public, no accounts)
// Keyed by target_key; visible to all users.

import { fetchJson } from "/api.js";
import { escapeHtml } from "/state.js";

let abortCtrl = null;

export function render(container) {
  const q = new URLSearchParams(location.search);
  const key = q.get("key");
  const ra = q.get("ra"); const dec = q.get("dec");
  const name = q.get("name") || "Custom field";

  container.innerHTML = `
    <div class="card">
      <div class="card-head">
        <h2>Annotations: ${escapeHtml(name)}</h2>
        <div class="coords-meta">${escapeHtml(key ? `key ${key}` : "no target")}</div>
      </div>
      <div id="ann-wrap"></div>
    </div>
  `;

  const wrap = container.querySelector("#ann-wrap");

  if (!key) {
    wrap.innerHTML = `<div class="panel muted">Load a target (Sky view) to add annotations.</div>`;
    return;
  }

  // Annotation form
  wrap.innerHTML = `
    <div class="ann-form" style="margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;">
      <input type="text" id="ann-body" placeholder="Add a note…" style="flex:1;padding:8px 11px;background:#0c141d;border:1px solid #2a3a4d;border-radius:9px;color:var(--text);">
      <input type="text" id="ann-author" placeholder="Your name (optional)" style="width:200px;padding:8px 11px;background:#0c141d;border:1px solid #2a3a4d;border-radius:9px;color:var(--text);">
      <button class="primary" id="ann-add">Add</button>
    </div>
    <div id="ann-list"></div>
  `;

  const bodyEl = wrap.querySelector("#ann-body");
  const authorEl = wrap.querySelector("#ann-author");
  const addBtn = wrap.querySelector("#ann-add");
  const listEl = wrap.querySelector("#ann-list");

  async function load() {
    listEl.innerHTML = `<div class="panel loading"><span class="spinner" aria-hidden="true"></span> Loading annotations…</div>`;
    const res = await fetchJson(`/api/public/targets/${encodeURIComponent(key)}/annotations`);
    if (!res.ok || !res.data) {
      listEl.innerHTML = `<div class="panel error">Error: ${escapeHtml(String(res.data?.error))}</div>`;
      return;
    }
    const anns = res.data.annotations || [];
    if (!anns.length) {
      listEl.innerHTML = `<div class="muted">No annotations yet. Be the first!</div>`;
      return;
    }
    listEl.innerHTML = anns.map((a) => `
      <div class="ann-item" style="background:#0f1821;border:1px solid var(--line-soft);border-radius:9px;padding:10px 12px;margin-bottom:8px;">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:4px;">
          <span style="font-weight:600;color:var(--accent-2);">${escapeHtml(String(a.author || "anonymous"))}</span>
          <span style="font-size:0.72rem;color:var(--muted-2);">${new Date(a.created).toLocaleString()}</span>
          ${a.id ? `<button class="ghost" style="font-size:0.7rem;padding:2px 6px;" data-id="${a.id}">Delete</button>` : ""}
        </div>
        <div style="color:var(--text);font-size:0.88rem;white-space:pre-wrap;">${escapeHtml(String(a.body))}</div>
      </div>
    `).join("");

    // Delete listeners
    for (const btn of listEl.querySelectorAll("button[data-id]")) {
      btn.addEventListener("click", async () => {
        const id = Number(btn.dataset.id);
        if (abortCtrl) abortCtrl.abort();
        abortCtrl = new AbortController();
        const res = await fetchJson(`/api/public/annotations/${id}`, { method: "DELETE", signal: abortCtrl.signal });
        if (res.ok) load();
      });
    }
  }

  addBtn.addEventListener("click", async () => {
    const body = bodyEl.value.trim();
    if (!body) return;
    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();
    const res = await fetchJson(`/api/public/targets/${encodeURIComponent(key)}/annotations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body, author: authorEl.value.trim() || "anonymous" }),
      signal: abortCtrl.signal,
    });
    if (res.ok && res.data) {
      bodyEl.value = "";
      load();
    } else {
      alert(res.data?.error || "Failed to add annotation");
    }
  });

  bodyEl.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); addBtn.click(); } });

  load();
}

export function destroy() {
  if (abortCtrl) abortCtrl.abort();
  abortCtrl = null;
}
