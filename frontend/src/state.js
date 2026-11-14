// Project WATS — application state
// Extracted from frontend/app.js (Phase 0: no behavior change)

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
export const lerp = (a, b, t) => a + (b - a) * t;

export function fmt(v, digits = 3) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
  return Number(v).toFixed(digits);
}

export function fmtSci(v) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return "—";
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 0.01 && a < 1000) return Number(v).toFixed(3);
  return Number(v).toExponential(2);
}

export function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").replace("Z", " UTC");
}

export function fmtDeg(ra, dec) {
  return `RA ${Number(ra).toFixed(5)}°  ·  Dec ${Number(dec).toFixed(5)}°`;
}

export function parseRa(text) {
  const s = String(text).trim();
  if (/^[+-]?\d+(\.\d+)?$/.test(s)) return Number(s) % 360;
  let m = s.match(/^(\d{1,2})[hH:\s]+(\d{1,2})[mM:\s]+(\d+(?:\.\d+)?)[sS]?$/);
  if (m) return (Number(m[1]) + Number(m[2]) / 60 + Number(m[3]) / 3600) * 15;
  m = s.match(/^(\d{1,2})[hH:\s]+(\d{1,2})[mM]?$/);
  if (m) return (Number(m[1]) + Number(m[2]) / 60) * 15;
  return NaN;
}

export function parseDec(text) {
  const s = String(text).trim();
  if (/^[+-]?\d+(\.\d+)?$/.test(s)) return Number(s);
  let m = s.match(/^([+-]?\d{1,3})[dD:\s]+(\d{1,2})[mM:\s]+(\d+(?:\.\d+)?)[sS]?$/);
  if (m) {
    const deg = Number(m[1]);
    const sign = deg < 0 ? -1 : 1;
    return deg + sign * (Number(m[2]) / 60 + Number(m[3]) / 3600);
  }
  m = s.match(/^([+-]?\d{1,3})[dD:\s]+(\d{1,2})[mM]?$/);
  if (m) {
    const deg = Number(m[1]);
    return deg + (deg < 0 ? -1 : 1) * Number(m[2]) / 60;
  }
  return NaN;
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export const FALLBACK_PRESETS = [
  { name: "WISE 0855", ra: 133.795096, dec: -7.245147, fov: 0.15, description: "Fast-moving Y brown dwarf" },
  { name: "Barnard's Star", ra: 269.4521, dec: 4.6933, fov: 0.15, description: "High proper-motion red dwarf" },
  { name: "Epsilon Indi B", ra: 330.59025, dec: -56.7833, fov: 0.15, description: "Brown-dwarf binary" },
  { name: "RW Aurigae", ra: 76.95625, dec: 30.6747, fov: 0.2, description: "Eruptive variable star" },
  { name: "R Draconis", ra: 269.4321, dec: 57.4, fov: 0.2, description: "Mira variable" },
];
