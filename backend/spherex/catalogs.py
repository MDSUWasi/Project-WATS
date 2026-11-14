"""Auxiliary catalogues: Gaia DR3 and CatWISE2020 overlays."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone

import numpy as np

from backend.config import CACHE_DIR, DEFAULT_OVERLAY_TTL, DEFAULT_NAXIS
from backend.spherex.query import WatError, tap_query
from backend.spherex.reproject import make_output_wcs
from backend.spherex.util import _atomic_write_json, _f, _hash, _read_json
from pathlib import Path

logger = logging.getLogger("wats")


def fetch_overlays(ra: float, dec: float, fov: float, naxis: int = DEFAULT_NAXIS,
                   use_cache: bool = True) -> dict:
    """Return Gaia DR3 and CatWISE2020 sources in the field, with pixel positions."""
    ra = float(ra)
    dec = float(dec)
    fov = float(fov)
    if not (math.isfinite(ra) and math.isfinite(dec) and math.isfinite(fov) and fov > 0):
        raise WatError("Overlay coordinates and field of view must be finite")
    naxis = max(8, int(naxis))
    radius = max(0.02, fov * 0.55)
    cache_path = CACHE_DIR / f"overlay_{_hash('overlay', f'{ra:.5f}', f'{dec:.5f}', f'{radius:.4f}', int(naxis))}.json"
    if use_cache:
        cached = _read_json(cache_path)
        if cached and (time.time() - float(cached.get("created_epoch", 0)) < DEFAULT_OVERLAY_TTL):
            return cached

    errors = {}
    gaia = []
    catwise = []
    try:
        q = f"""
            SELECT TOP 600 source_id, ra, dec, phot_g_mean_mag, bp_rp, pmra, pmdec, parallax
            FROM gaia.gaia_dr3_source
            WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.8f}, {dec:.8f}, {radius:.6f})) = 1
              AND phot_g_mean_mag < 20
            ORDER BY phot_g_mean_mag
        """
        for row in tap_query(q):
            gaia.append(
                {
                    "id": str(row.get("source_id", "")),
                    "ra": _f(row, "ra"),
                    "dec": _f(row, "dec"),
                    "mag": _f(row, "phot_g_mean_mag"),
                    "bp_rp": _f(row, "bp_rp"),
                    "pmra": _f(row, "pmra"),
                    "pmdec": _f(row, "pmdec"),
                    "parallax": _f(row, "parallax"),
                }
            )
    except Exception as exc:
        errors["gaia"] = str(exc)
        logger.warning("Gaia overlay failed: %s", exc)
    try:
        q = f"""
            SELECT TOP 600 source_id, ra, dec, w1mpro, w2mpro, pmra, pmdec
            FROM catwise_2020
            WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.8f}, {dec:.8f}, {radius:.6f})) = 1
              AND w1mpro < 18
            ORDER BY w1mpro
        """
        for row in tap_query(q):
            catwise.append(
                {
                    "id": str(row.get("source_id", "")),
                    "ra": _f(row, "ra"),
                    "dec": _f(row, "dec"),
                    "w1": _f(row, "w1mpro"),
                    "w2": _f(row, "w2mpro"),
                    "pmra": _f(row, "pmra"),
                    "pmdec": _f(row, "pmdec"),
                }
            )
    except Exception as exc:
        errors["catwise"] = str(exc)
        logger.warning("CatWISE overlay failed: %s", exc)

    wcs = make_output_wcs(ra, dec, fov, naxis)

    def with_xy(items, ra_key="ra", dec_key="dec"):
        if not items:
            return items
        ra_arr = np.array([it.get(ra_key, np.nan) for it in items], dtype=float)
        dec_arr = np.array([it.get(dec_key, np.nan) for it in items], dtype=float)
        good = np.isfinite(ra_arr) & np.isfinite(dec_arr)
        x = np.full(ra_arr.shape, np.nan)
        y = np.full(dec_arr.shape, np.nan)
        if np.any(good):
            xx, yy = wcs.all_world2pix(ra_arr[good], dec_arr[good], 0)
            x[good] = xx
            y[good] = yy
        for it, xv, yv in zip(items, x, y):
            it["x"] = float(xv) if np.isfinite(xv) else None
            it["y"] = float(yv) if np.isfinite(yv) else None
            it["inside"] = bool(np.isfinite(xv) and np.isfinite(yv) and 0 <= xv < naxis and 0 <= yv < naxis)
        return items

    gaia = with_xy(gaia)
    catwise = with_xy(catwise)
    result = {
        "status": "ready",
        "ra": ra,
        "dec": dec,
        "fov_deg": float(fov),
        "naxis": int(naxis),
        "radius_deg": radius,
        "gaia": gaia,
        "catwise": catwise,
        "errors": errors,
        "created": datetime.now(timezone.utc).isoformat(),
        "created_epoch": time.time(),
        "gaia_count": len(gaia),
        "catwise_count": len(catwise),
    }
    _atomic_write_json(cache_path, result)
    return result
