"""MEF loading: IMAGE/FLAGS/VARIANCE/ZODI/WCS-WAVE HDUs and the Cutout dataclass."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from backend.config import BAD_FLAG_BITS
from backend.spherex.query import WatError
from backend.spherex.util import _f, _to_um

warnings.filterwarnings("ignore", message=".*Inconsistent SIP.*")
warnings.filterwarnings("ignore", message=".*SIP coefficients.*")


@dataclass
class Cutout:
    image: np.ndarray
    variance: np.ndarray
    flags: np.ndarray
    zodi: np.ndarray
    wavelength: np.ndarray
    bandwidth: np.ndarray
    bad: np.ndarray
    wcs: WCS
    header: dict
    detector: int = 0
    mjd: float = 0.0
    path: str = ""


def _find_hdu(hdul, name):
    for hdu in hdul:
        if hdu.name == name:
            return hdu
    return None


def load_cutout(path: str | Path) -> Cutout:
    """Load the IMAGE/FLAGS/VARIANCE/ZODI/WCS-WAVE HDUs from a SPHEREx MEF cutout."""
    path = Path(path)
    with fits.open(path, memmap=False) as hdul:
        image_hdu = _find_hdu(hdul, "IMAGE")
        if image_hdu is None or image_hdu.data is None:
            raise WatError(f"No IMAGE data in {path.name}")
        flags_hdu = _find_hdu(hdul, "FLAGS")
        var_hdu = _find_hdu(hdul, "VARIANCE")
        zodi_hdu = _find_hdu(hdul, "ZODI")
        image = np.array(image_hdu.data, dtype=np.float64, copy=True)
        flags = (
            np.array(flags_hdu.data, dtype=np.int64, copy=True)
            if flags_hdu is not None and flags_hdu.data is not None
            else np.zeros_like(image, dtype=np.int64)
        )
        variance = (
            np.array(var_hdu.data, dtype=np.float64, copy=True)
            if var_hdu is not None and var_hdu.data is not None
            else np.full_like(image, np.nan)
        )
        zodi = (
            np.array(zodi_hdu.data, dtype=np.float64, copy=True)
            if zodi_hdu is not None and zodi_hdu.data is not None
            else np.zeros_like(image)
        )
        if flags.shape != image.shape:
            flags = np.zeros_like(image, dtype=np.int64)
        if variance.shape != image.shape:
            variance = np.full_like(image, np.nan)
        if zodi.shape != image.shape:
            zodi = np.zeros_like(image)
        header = dict(image_hdu.header)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wcs = WCS(image_hdu.header)
        # Spectral WCS: the WAVE-TAB lookup table is in the WCS-WAVE extension.
        spectral_wcs = None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                spectral_wcs = WCS(image_hdu.header, fobj=hdul, key="W")
                spectral_wcs.sip = None
        except Exception:
            spectral_wcs = None
        if spectral_wcs is not None:
            ny, nx = image.shape
            yy, xx = np.mgrid[0:ny, 0:nx]
            try:
                wl, bw = spectral_wcs.pixel_to_world(xx, yy)
                wavelength = _to_um(wl)
                bandwidth = _to_um(bw)
            except Exception:
                wavelength = np.full(image.shape, np.nan)
                bandwidth = np.full(image.shape, np.nan)
        else:
            wavelength = np.full(image.shape, np.nan)
            bandwidth = np.full(image.shape, np.nan)
        bad = np.zeros(image.shape, dtype=bool)
        for bit in BAD_FLAG_BITS:
            bad |= (flags & (1 << bit)) != 0
        det = int(header.get("DETECTOR", 0) or 0)
        mjd = _f(header, "MJD-OBS", 0.0) or 0.0
        return Cutout(
            image=image,
            variance=variance,
            flags=flags,
            zodi=zodi,
            wavelength=wavelength,
            bandwidth=bandwidth,
            bad=bad,
            wcs=wcs,
            header=header,
            detector=det,
            mjd=float(mjd),
            path=str(path),
        )
