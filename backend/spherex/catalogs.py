"""Auxiliary catalogues: Gaia DR3 and CatWISE2020 overlays."""

from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
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
    stale_cached = None
    if use_cache:
        cached = _read_json(cache_path)
        if cached and isinstance(cached, dict):
            stale_cached = cached
            if (time.time() - float(cached.get("created_epoch", 0)) < DEFAULT_OVERLAY_TTL):
                return cached

    errors = {}
    gaia = []
    catwise = []

    # On restricted/offline hosts, skip live TAP catalog queries to avoid
    # request-thread hangs; return cached/stale results or empty overlays.
    import os as _os
    try:
        _net_cap = int(_os.environ.get("WATS_NETWORK_TIMEOUT_S") or 0)
    except Exception:
        _net_cap = 0
    if _net_cap > 0 and _net_cap <= 30:
        if stale_cached and isinstance(stale_cached, dict):
            stale = dict(stale_cached)
            stale["stale"] = True
            stale.setdefault("errors", {})
            stale["errors"]["offline"] = "Catalog overlays unavailable offline; showing cached/local data if any."
            return stale
        return {
            "status": "ready",
            "ra": ra, "dec": dec, "fov_deg": float(fov), "naxis": int(naxis),
            "radius_deg": radius, "gaia": [], "catwise": [],
            "errors": {"offline": "Catalog overlays unavailable offline (archive unreachable)."},
            "created": datetime.now(timezone.utc).isoformat(),
            "created_epoch": time.time(),
            "gaia_count": 0, "catwise_count": 0, "offline": True,
        }

    def _query_gaia():
        try:
            q = f"""
                SELECT TOP 600 source_id, ra, dec, phot_g_mean_mag, bp_rp, pmra, pmdec, parallax
                FROM gaia.gaia_dr3_source
                WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.8f}, {dec:.8f}, {radius:.6f})) = 1
                  AND phot_g_mean_mag < 20
                ORDER BY phot_g_mean_mag
            """
            rows = []
            for row in tap_query(q):
                rows.append(
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
            return rows
        except Exception as exc:
            errors["gaia"] = str(exc)
            logger.warning("Gaia overlay failed: %s", exc)
            return []

    def _query_catwise():
        try:
            q = f"""
                SELECT TOP 600 source_id, ra, dec, w1mpro, w2mpro, pmra, pmdec
                FROM catwise_2020
                WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.8f}, {dec:.8f}, {radius:.6f})) = 1
                  AND w1mpro < 18
                ORDER BY w1mpro
            """
            rows = []
            for row in tap_query(q):
                rows.append(
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
            return rows
        except Exception as exc:
            errors["catwise"] = str(exc)
            logger.warning("CatWISE overlay failed: %s", exc)
            return []

    # Run both catalog queries in parallel so a slow TAP endpoint does not
    # serialize the overlay fetch.
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_gaia = ex.submit(_query_gaia)
        f_catwise = ex.submit(_query_catwise)
        gaia = f_gaia.result()
        catwise = f_catwise.result()

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
    # If both catalog queries failed and we have any prior cached result,
    # keep serving it instead of caching an error-only empty response.
    if stale_cached and errors and not gaia and not catwise:
        stale = dict(stale_cached)
        stale["errors"] = errors
        stale["stale"] = True
        return stale
    _atomic_write_json(cache_path, result)
    return result
