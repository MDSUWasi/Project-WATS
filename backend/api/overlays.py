"""Overlay API blueprint."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend import spherex
from backend.spherex.catalogs import fetch_overlays
from backend.spherex.pipeline import load_manifest

bp = Blueprint("overlays", __name__)


@bp.get("/api/overlays")
def api_overlays():
    try:
        ra = spherex.parse_ra(request.args.get("ra"))
        dec = spherex.parse_dec(request.args.get("dec"))
        fov = float(request.args.get("fov", spherex.DEFAULT_FOV_DEG))
        naxis = int(request.args.get("naxis", spherex.DEFAULT_NAXIS))
    except Exception:
        return jsonify({"status": "error", "error": "Invalid overlay parameters"}), 400
    try:
        return jsonify(fetch_overlays(ra, dec, fov, naxis=naxis))
    except Exception as exc:
        return jsonify({"status": "error", "error": f"Overlay catalogues are unavailable: {exc}"}), 502


@bp.get("/api/targets/<key>/overlays")
def target_overlays(key):
    manifest = load_manifest(key)
    if not manifest:
        return jsonify({"status": "error", "error": "target not found"}), 404
    ra = float(manifest.get("ra", 0))
    dec = float(manifest.get("dec", 0))
    fov = float(manifest.get("fov_deg", spherex.DEFAULT_FOV_DEG))
    naxis = int(manifest.get("naxis", spherex.DEFAULT_NAXIS))
    try:
        return jsonify(fetch_overlays(ra, dec, fov, naxis=naxis))
    except Exception as exc:
        return jsonify({"status": "error", "error": f"Overlay catalogues are unavailable: {exc}"}), 502
