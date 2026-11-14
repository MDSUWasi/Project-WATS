"""Common TAN grid reprojection and channel mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from astropy.wcs import WCS

from backend.config import BAD_FLAG_BITS, CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend.spherex.fits import Cutout


def make_output_wcs(ra: float, dec: float, fov_deg: float, naxis: int) -> WCS:
    """North-up, east-left TAN WCS for the output frame."""
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [float(ra), float(dec)]
    w.wcs.crpix = [(float(naxis) + 1.0) / 2.0, (float(naxis) + 1.0) / 2.0]
    scale = float(fov_deg) / float(naxis)
    w.wcs.cd = np.array([[-scale, 0.0], [0.0, scale]], dtype=float)
    w.wcs.cunit = ["deg", "deg"]
    w.wcs.set()
    return w


def wcs_to_dict(wcs: WCS, naxis: int, fov_deg: float) -> dict:
    return {
        "ctype": list(wcs.wcs.ctype),
        "crval": [float(v) for v in wcs.wcs.crval],
        "crpix": [float(v) for v in wcs.wcs.crpix],
        "cd": [[float(v) for v in row] for row in wcs.wcs.cd],
        "naxis": int(naxis),
        "fov_deg": float(fov_deg),
    }


@dataclass
class Reprojected:
    image: np.ndarray
    variance: np.ndarray
    flags: np.ndarray
    zodi: np.ndarray
    wavelength: np.ndarray
    bandwidth: np.ndarray
    bad: np.ndarray
    valid: np.ndarray
    wcs: WCS


def reproject_cutout(cut: "Cutout", ra: float, dec: float, fov_deg: float, naxis: int) -> Reprojected:
    """Resample a cutout onto a common north-up TAN grid."""
    out_wcs = make_output_wcs(ra, dec, fov_deg, naxis)
    ny, nx = cut.image.shape
    x_out, y_out = np.meshgrid(np.arange(naxis, dtype=float), np.arange(naxis, dtype=float))
    ra_out, dec_out = out_wcs.all_pix2world(x_out, y_out, 0)
    try:
        x_in, y_in = cut.wcs.all_world2pix(ra_out, dec_out, 0)
    except Exception:
        x_in, y_in = cut.wcs.wcs_world2pix(ra_out, dec_out, 0)
    x_in = np.asarray(x_in, dtype=float)
    y_in = np.asarray(y_in, dtype=float)
    valid = (
        np.isfinite(x_in)
        & np.isfinite(y_in)
        & (x_in >= 0)
        & (x_in <= nx - 1)
        & (y_in >= 0)
        & (y_in <= ny - 1)
    )
    coords = [y_in, x_in]
    from scipy.ndimage import map_coordinates

    image = map_coordinates(cut.image, coords, order=1, mode="constant", cval=np.nan)
    variance = map_coordinates(cut.variance, coords, order=1, mode="constant", cval=np.nan)
    zodi = map_coordinates(cut.zodi, coords, order=1, mode="constant", cval=np.nan)
    wavelength = map_coordinates(cut.wavelength, coords, order=1, mode="constant", cval=np.nan)
    bandwidth = map_coordinates(cut.bandwidth, coords, order=1, mode="constant", cval=np.nan)
    flags = map_coordinates(cut.flags.astype(np.float64), coords, order=0, mode="constant", cval=0).astype(np.int64)
    bad = np.zeros(image.shape, dtype=bool)
    for bit in BAD_FLAG_BITS:
        bad |= (flags & (1 << bit)) != 0
    valid = valid & ~bad
    image = np.where(valid, image, np.nan)
    variance = np.where(valid, variance, np.nan)
    zodi = np.where(valid, zodi, np.nan)
    wavelength = np.where(valid, wavelength, np.nan)
    bandwidth = np.where(valid, bandwidth, np.nan)
    return Reprojected(
        image=image,
        variance=variance,
        flags=flags,
        zodi=zodi,
        wavelength=wavelength,
        bandwidth=bandwidth,
        bad=bad,
        valid=valid,
        wcs=out_wcs,
    )


def channel_for_detector(detector: int) -> int:
    if detector in (1, 2):
        return CHANNEL_BLUE
    if detector == 3:
        return CHANNEL_GREEN
    return CHANNEL_RED
