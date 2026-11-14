// Project WATS — API helpers
// Extracted from frontend/app.js (Phase 0: no behavior change)

export async function fetchJson(url, opts = {}) {
  try {
    const headers = { Accept: "application/json", ...(opts.headers || {}) };
    const resp = await fetch(url, { headers, signal: opts.signal });
    let data = null;
    try { data = await resp.json(); } catch (_) { /* no body */ }
    return { ok: resp.ok, status: resp.status, data };
  } catch (err) {
    return { ok: false, status: 0, data: { error: String(err) } };
  }
}
