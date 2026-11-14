// Project WATS — API helpers
// Extracted from frontend/app.js (Phase 0: no behavior change)

export async function fetchJson(url, opts = {}) {
  try {
    opts = opts || {};
    if (opts && typeof opts === "object" && typeof opts.aborted === "boolean" && typeof opts.addEventListener === "function") {
      opts = { signal: opts };
    }
    const headers = { Accept: "application/json", ...(opts.headers || {}) };
    const init = { ...opts, headers };
    delete init.signal;
    if (opts.signal) init.signal = opts.signal;
    const resp = await fetch(url, init);
    let data = null;
    try { data = await resp.json(); } catch (_) { /* no body */ }
    return { ok: resp.ok, status: resp.status, data };
  } catch (err) {
    return { ok: false, status: 0, data: { error: String(err) } };
  }
}