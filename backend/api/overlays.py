"""Overlay API blueprint (compatibility + new endpoints).

Endpoints:
  GET /api/overlays?ra&dec&fov&naxis   Gaia/CatWISE sources (legacy alias)
  GET /api/targets/<key>/overlays      overlays for a stored target
"""

from __future__ import annotations

import math

from flask import Blueprint, jsonify, request

from backend import spherex

bp = Blueprint("overlays", __name__, url_prefix="/api")


@bp.get("/overlays")
def overlays():
    try:
        ra = spherex.parse_ra(request.args.get("ra"))
        dec = spherex.parse_dec(request.args.get("dec"))
        fov = float(request.args.get("fov", spherex.DEFAULT_FOV_DEG))
        naxis = int(request.args.get("naxis", spherex.DEFAULT_NAXIS))
    except (TypeError, ValueError) as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    if not math.isfinite(fov):
        return jsonify({"status": "error", "error": "fov must be a finite number"}), 400
    fov = min(max(fov, 0.02), 2.0)
    naxis = min(max(naxis, 128), 512)
    try:
        return jsonify(spherex.fetch_overlays(ra, dec, fov, naxis))
    except Exception as exc:
        return jsonify({"status": "error", "error": f"Overlay catalogues are unavailable: {exc}"}), 502
