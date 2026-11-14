"""Aperture photometry at the target position."""

from __future__ import annotations

import math

import numpy as np

from backend.config import DETECTOR_BANDS, DETECTOR_NAMES, PIXEL_SCALE_ARCSEC
from backend.spherex.query import WatError, mjd_to_iso
from backend.spherex.util import _clamp, _f, _to_um


def _aperture_photometry(cut, ra: float, dec: float, radius: float = 2.5,
                         r_in: float = 5.0, r_out: float = 10.0) -> dict:
    wcs = cut.wcs
    try:
        x, y = wcs.all_world2pix(float(ra), float(dec), 0)
    except Exception:
        x, y = wcs.wcs_world2pix(float(ra), float(dec), 0)
    x, y = float(x), float(y)
    ny, nx = cut.image.shape
    xi, yi = int(round(x)), int(round(y))
    xi = _clamp(xi, 0, nx - 1)
    yi = _clamp(yi, 0, ny - 1)
    if cut.bad[yi, xi]:
        best = None
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                xx, yy = xi + dx, yi + dy
                if 0 <= xx < nx and 0 <= yy < ny and not cut.bad[yy, xx] and np.isfinite(cut.image[yy, xx]):
                    val = cut.image[yy, xx]
                    if best is None or val > best[0]:
                        best = (val, xx, yy)
        if best is not None:
            xi, yi = best[1], best[2]
    yy, xx = np.mgrid[0:ny, 0:nx]
    r = np.hypot(xx - x, yy - y)
    ap = r <= radius
    ann = (r >= r_in) & (r <= r_out)
    ann &= np.isfinite(cut.image)
    ann &= ~cut.bad
    img = np.where(np.isfinite(cut.image) & ~cut.bad, cut.image, np.nan)
    if not np.any(np.isfinite(img[ap])):
        return {}
    bg = float(np.nanmedian(img[ann])) if np.any(np.isfinite(img[ann])) else 0.0
    bg_n = max(1, int(np.count_nonzero(np.isfinite(img[ann]))))
    bg_std = float(np.nanstd(img[ann]) / math.sqrt(bg_n)) if bg_n > 1 else 0.0
    ap_vals = img[ap]
    flux = float(np.nansum(ap_vals - bg))
    n_ap = max(1, int(np.count_nonzero(np.isfinite(ap_vals))))
    var_ap = float(np.nansum(cut.variance[ap])) if np.any(np.isfinite(cut.variance[ap])) else 0.0
    err = math.sqrt(max(var_ap, 0.0) + n_ap * bg_std * bg_std)
    wl = cut.wavelength[yi, xi]
    bw = cut.bandwidth[yi, xi]
    if not np.isfinite(wl):
        band = DETECTOR_BANDS.get(cut.detector, (0, 0))
        wl = 0.5 * (band[0] + band[1])
    if not np.isfinite(bw):
        band = DETECTOR_BANDS.get(cut.detector, (0, 0))
        bw = max(0.0, band[1] - band[0])
    try:
        cd = np.asarray(wcs.wcs.cd, dtype=float)
        pix_area = abs(float(np.linalg.det(cd))) * (math.pi / 180.0) ** 2
    except Exception:
        pix_area = (PIXEL_SCALE_ARCSEC / 206264.806) ** 2
    return {
        "wavelength_um": float(wl),
        "bandwidth_um": float(bw),
        "flux_mjy_sr": flux,
        "flux_jy": flux * 1e6 * pix_area,
        "error_mjy_sr": err,
        "error_jy": err * 1e6 * pix_area,
        "background_mjy_sr": bg,
        "peak_mjy_sr": float(np.nanmax(ap_vals)),
        "mjd": float(cut.mjd),
        "time": mjd_to_iso(cut.mjd),
        "detector": DETECTOR_NAMES.get(cut.detector, f"D{cut.detector}"),
        "obs_id": str(cut.header.get("OBSID", "")),
        "x": xi,
        "y": yi,
    }
