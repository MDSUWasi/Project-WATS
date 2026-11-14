"""On-the-fly spectrum extraction at the target position using WCS-WAVE."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone

import numpy as np

from backend.config import DEFAULT_SPECTRUM_TTL, DETECTOR_BANDS, DETECTOR_NAMES
from backend.spherex.ibe import fetch_cutout as _fetch_cutout, cutout_size_for as _cutout_size_for
from backend.spherex.fits import load_cutout
from backend.spherex.pipeline import cache_key, load_manifest, target_dir
from backend.spherex.query import WatError, mjd_to_iso
from backend.spherex.util import _atomic_write_json, _read_json, _f
from backend.spherex.photometry import _aperture_photometry
from backend.repo import _connect, upsert_spectrum

APERTURE_RADIUS = 2.5
BG_IN = 5.0
BG_OUT = 10.0
NBANDS = 102


def _pixel_spectrum(cut, ra: float, dec: float, radius: float = APERTURE_RADIUS):
    """Extract per-wavelength flux points from a cutout using WCS-WAVE.

    Instead of a single detector-bandpass point, histogram aperture pixels by
    their per-pixel WCS-WAVE wavelength so each cutout contributes several
    wavelength points across its detector bandpass.
    """
    wcs = cut.wcs
    try:
        x, y = wcs.all_world2pix(float(ra), float(dec), 0)
    except Exception:
        try:
            x, y = wcs.wcs_world2pix(float(ra), float(dec), 0)
        except Exception:
            return []
    x, y = float(x), float(y)
    ny, nx = cut.image.shape
    img = np.where(np.isfinite(cut.image) & ~cut.bad, cut.image, np.nan)
    var = cut.variance if np.any(np.isfinite(cut.variance)) else None
    wl = cut.wavelength
    if not np.any(np.isfinite(wl)):
        return []
    # Background from annulus.
    yy, xx = np.mgrid[0:ny, 0:nx]
    r = np.hypot(xx - x, yy - y)
    ap = r <= radius
    ann = (r >= BG_IN) & (r <= BG_OUT)
    ann &= np.isfinite(img) & ~cut.bad
    bg = float(np.nanmedian(img[ann])) if np.any(np.isfinite(img[ann])) else 0.0
    ap_mask = ap & np.isfinite(img) & ~cut.bad
    if not np.any(ap_mask):
        return []
    ap_wl = wl[ap_mask]
    ap_flux = img[ap_mask] - bg
    # Bin aperture pixels by wavelength into NBANDS bins across the detector range.
    wl_min, wl_max = float(np.nanmin(ap_wl)), float(np.nanmax(ap_wl))
    if not np.isfinite(wl_min) or not np.isfinite(wl_max) or wl_max <= wl_min:
        band = DETECTOR_BANDS.get(cut.detector, (0.0, 0.0))
        wl_min, wl_max = band
    if wl_max <= wl_min:
        return []
    bins = np.linspace(wl_min, wl_max, NBANDS + 1)
    points = []
    for i in range(NBANDS):
        lo, hi = bins[i], bins[i + 1]
        sel = (ap_wl >= lo) & (ap_wl < hi)
        if i == NBANDS - 1:
            sel = (ap_wl >= lo) & (ap_wl <= hi)
        if not np.any(sel):
            continue
        wl_c = float(0.5 * (lo + hi))
        flux = float(np.nansum(ap_flux[sel]))
        n = int(np.count_nonzero(sel))
        var_ap = 0.0
        if var is not None:
            var_sel = var[ap_mask][sel]
            var_ap = float(np.nansum(var_sel[np.isfinite(var_sel)]))
        err = math.sqrt(max(var_ap, 0.0))
        points.append({
            "wavelength_um": round(wl_c, 4),
            "bandwidth_um": round(float(bins[1] - bins[0]), 4),
            "flux_mjy_sr": round(flux, 6),
            "flux_jy": round(flux * 1e6, 6),
            "error_mjy_sr": round(err, 6),
            "error_jy": round(err * 1e6, 6),
            "background_mjy_sr": round(bg, 6),
            "mjd": float(cut.mjd),
            "time": mjd_to_iso(cut.mjd),
            "detector": DETECTOR_NAMES.get(cut.detector, str(cut.detector)),
            "n_pixels": n,
        })
    return points


def extract_spectrum(params: dict, use_cache: bool = True) -> dict:
    """Extract a spectrum at the target position from cached cutouts.

    Uses per-pixel WCS-WAVE wavelengths where available, falling back to
    detector bandpass midpoints. Stores per-frame spectra rows in the DB.
    """
    key = str(params.get("key") or cache_key(params))
    manifest = load_manifest(key)
    if not manifest:
        raise WatError("Frames are not ready yet; load the target first.")
    ra = float(params["ra"])
    dec = float(params["dec"])
    fov = float(params.get("fov", manifest.get("fov_deg", 0.15)))
    if not math.isfinite(fov) or fov <= 0:
        raise WatError("fov must be a positive number of degrees")
    cache_path = target_dir(key) / "spectrum.json"
    manifest_updated = str(manifest.get("updated") or manifest.get("created") or "")
    if use_cache and cache_path.exists():
        cached = _read_json(cache_path)
        if (
            cached
            and cached.get("manifest_updated") == manifest_updated
            and (time.time() - float(cached.get("created_epoch", 0)) < DEFAULT_SPECTRUM_TTL)
        ):
            return cached
    points = []
    seen = set()
    conn = _connect()
    try:
        for frame in manifest.get("frames", []):
            for uri in frame.get("sources", []):
                if uri in seen:
                    continue
                seen.add(uri)
                try:
                    path = _fetch_cutout(uri, ra, dec, _cutout_size_for(fov))
                    cut = load_cutout(path)
                    # Per-pixel WCS-WAVE extraction.
                    pts = _pixel_spectrum(cut, ra, dec)
                    if pts:
                        for p in pts:
                            p["frame"] = int(frame.get("index", 0))
                            p["uri"] = uri
                            # Persist a spectra row.  ``detector`` is INTEGER in
                            # the schema, so normalise the display name ("D3")
                            # back to the numeric id before writing.
                            try:
                                det_id = int(str(p.get("detector", cut.detector)).upper().lstrip("D") or 0)
                            except (TypeError, ValueError):
                                det_id = int(cut.detector or 0)
                            try:
                                upsert_spectrum(conn, key, {
                                    "frame_index": p["frame"],
                                    "detector": det_id,
                                    "detector_name": p.get("detector", f"D{det_id}"),
                                    "wavelength_um": p["wavelength_um"],
                                    "bandwidth_um": p["bandwidth_um"],
                                    "flux_jy": p["flux_jy"],
                                    "error_jy": p["error_jy"],
                                    "x": p.get("x", 0),
                                    "y": p.get("y", 0),
                                    "mjd": p["mjd"],
                                })
                            except Exception:
                                pass
                        points.extend(pts)
                    else:
                        # Fall back to single aperture point using detector bandpass.
                        point = _aperture_photometry(cut, ra, dec)
                        if point:
                            point["frame"] = int(frame.get("index", 0))
                            point["uri"] = uri
                            points.append(point)
                except Exception as exc:
                    logging.getLogger("wats").warning("Spectrum: skipping %s: %s", uri, exc)
        conn.commit()
    finally:
        conn.close()
    points.sort(key=lambda p: (p.get("wavelength_um", 0), p.get("mjd", 0)))
    if points:
        wl = [p["wavelength_um"] for p in points if p.get("wavelength_um")]
        result = {
            "status": "ready",
            "key": key,
            "points": points,
            "n_points": len(points),
            "wavelength_range_um": [min(wl), max(wl)] if wl else None,
            "created": datetime.now(timezone.utc).isoformat(),
            "created_epoch": time.time(),
            "manifest_updated": manifest_updated,
            "detector_bands_um": {k: list(v) for k, v in DETECTOR_BANDS.items()},
            "method": "wcs_wave",
        }
    else:
        result = {"status": "empty", "key": key, "points": [], "n_points": 0,
                  "manifest_updated": manifest_updated,
                  "message": "No usable cutout pixels at the target position.",
                  "method": "wcs_wave"}
    _atomic_write_json(cache_path, result)
    return result
