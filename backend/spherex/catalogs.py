"""Auxiliary catalogues: Gaia DR3 and CatWISE2020 overlays."""

from __future__ import annotations

import logging
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from backend.config import CACHE_DIR, DEFAULT_OVERLAY_TTL, DEFAULT_NAXIS
from backend.spherex.query import WatError, tap_query
from backend.spherex.reproject import make_output_wcs
from backend.spherex.util import _atomic_write_json, _hash, _read_json

logger = logging.getLogger("wats")


def _query_gaia(ra: float, dec: float, radius: float):
    query = (
        "SELECT TOP 600 source_id, ra, dec, phot_g_mean_mag, bp_rp, pmra, pmdec, parallax "
        "FROM gaia.gaia_dr3_source "
        f"WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius:.6f})) = 1 "
        "AND phot_g_mean_mag < 20 "
        "ORDER BY phot_g_mean_mag"
    )
    rows = tap_query(query)
    out = []
    for r in rows:
        out.append({
            "id": r.get("source_id"),
            "ra": r.get("ra"),
            "dec": r.get("dec"),
            "mag": r.get("phot_g_mean_mag"),
            "bp_rp": r.get("bp_rp"),
            "pmra": r.get("pmra"),
            "pmdec": r.get("pmdec"),
            "parallax": r.get("parallax"),
            "catalog": "gaia",
        })
    return out


def _query_catwise(ra: float, dec: float, radius: float):
    query = (
        "SELECT TOP 600 source_id, ra, dec, w1mpro, w2mpro, pmra, pmdec "
        "FROM catwise_2020 "
        f"WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius:.6f})) = 1 "
        "AND w1mpro < 18 "
        "ORDER BY w1mpro"
    )
    rows = tap_query(query)
    out = []
    for r in rows:
        out.append({
            "id": r.get("source_id"),
            "ra": r.get("ra"),
            "dec": r.get("dec"),
            "w1": r.get("w1mpro"),
            "w2": r.get("w2mpro"),
            "mag": r.get("w1mpro"),
            "pmra": r.get("pmra"),
            "pmdec": r.get("pmdec"),
            "catalog": "catwise",
        })
    return out


def _with_xy(rows, ra_key, dec_key, wcs, naxis):
    if not rows:
        return []
    ra_arr = np.array([float(r.get(ra_key) or 0.0) for r in rows], dtype=float)
    dec_arr = np.array([float(r.get(dec_key) or 0.0) for r in rows], dtype=float)
    good = np.isfinite(ra_arr) & np.isfinite(dec_arr)
    x = np.full(len(rows), np.nan, dtype=float)
    y = np.full(len(rows), np.nan, dtype=float)
    if np.any(good):
        try:
            xx, yy = wcs.all_world2pix(ra_arr[good], dec_arr[good], 0)
            x[good] = xx
            y[good] = yy
        except Exception:
            pass
    out = []
    for i, r in enumerate(rows):
        item = dict(r)
        xi = float(x[i]) if np.isfinite(x[i]) else None
        yi = float(y[i]) if np.isfinite(y[i]) else None
        inside = bool(xi is not None and yi is not None and 0 <= xi < naxis and 0 <= yi < naxis)
        item["x"] = xi
        item["y"] = yi
        item["inside"] = inside
        out.append(item)
    return out


def fetch_overlays(ra: float, dec: float, fov_deg: float, naxis: int = DEFAULT_NAXIS, use_cache: bool = True):
    """Return Gaia DR3 and CatWISE2020 sources in the field, with pixel positions."""
    if not (math.isfinite(ra) and math.isfinite(dec) and math.isfinite(fov_deg)):
        raise WatError("Overlay coordinates and field of view must be finite")
    radius = float(fov_deg) * 0.75
    key = _hash(["overlay", f"{ra:.5f}", f"{dec:.5f}", f"{radius:.4f}", int(naxis)])
    cache_path = Path(CACHE_DIR) / f"overlay_{key}.json"
    ttl = int(DEFAULT_OVERLAY_TTL)
    stale_cached = None
    if cache_path.is_file():
        cached = _read_json(cache_path)
        if isinstance(cached, dict):
            age = time.time() - cache_path.stat().st_mtime
            if use_cache and age < ttl:
                cached.setdefault("status", "ready")
                cached.setdefault("stale", False)
                cached.setdefault("errors", {})
                return cached
            stale_cached = cached

    wcs = make_output_wcs(ra, dec, fov_deg, int(naxis))
    errors = {}
    gaia_rows = []
    cat_rows = []
    _net_cap = float(os.environ.get("WATS_NETWORK_TIMEOUT_S", "0") or 0)

    def do_gaia():
        try:
            return _query_gaia(ra, dec, radius)
        except Exception as exc:
            logger.warning("Gaia overlay failed: %s", exc)
            errors["gaia"] = str(exc)
            return []

    def do_cat():
        try:
            return _query_catwise(ra, dec, radius)
        except Exception as exc:
            logger.warning("CatWISE overlay failed: %s", exc)
            errors["catwise"] = str(exc)
            return []

    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_gaia = ex.submit(do_gaia)
            f_cat = ex.submit(do_cat)
            timeout = _net_cap if _net_cap > 0 else None
            gaia_rows = f_gaia.result(timeout=timeout) if timeout else f_gaia.result()
            cat_rows = f_cat.result(timeout=timeout) if timeout else f_cat.result()
    except Exception as exc:
        # ensure we at least tried sequentially if pool failed badly
        if not gaia_rows and "gaia" not in errors:
            gaia_rows = do_gaia()
        if not cat_rows and "catwise" not in errors:
            cat_rows = do_cat()
        if not gaia_rows and not cat_rows and not errors:
            errors["catalogs"] = str(exc)

    gaia = _with_xy(gaia_rows, "ra", "dec", wcs, int(naxis))
    catwise = _with_xy(cat_rows, "ra", "dec", wcs, int(naxis))

    if not gaia and not catwise and errors and stale_cached:
        stale_cached.setdefault("status", "ready")
        stale_cached["stale"] = True
        stale_cached["errors"] = errors
        stale_cached["message"] = "Catalog services unavailable; showing previously cached data."
        return stale_cached

    result = {
        "status": "ready",
        "ra": float(ra),
        "dec": float(dec),
        "fov_deg": float(fov_deg),
        "radius_deg": float(radius),
        "naxis": int(naxis),
        "created": datetime.now(timezone.utc).isoformat(),
        "created_epoch": time.time(),
        "gaia": gaia,
        "catwise": catwise,
        "gaia_count": len(gaia),
        "catwise_count": len(catwise),
        "errors": errors,
        "stale": False,
    }
    try:
        _atomic_write_json(cache_path, result)
    except Exception:
        pass
    return result
