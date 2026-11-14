#!/usr/bin/env python3
"""Project WATS core library.

Project WATS turns SPHEREx spectral-image observations into a blink
comparator: a time series of sky images that makes moving and variable
objects easy to spot.

The module deliberately talks to public services only:

* SPHEREx metadata and images: NASA/IPAC IRSA TAP + IBE cutout service
* Auxiliary catalogues: Gaia DR3 and CatWISE2020 (also mirrored at IRSA)

No API keys, logins, or user data are required.

Main entry points
-----------------
query_spherex(ra, dec)              metadata for every SPHEREx MEF covering a position
fetch_cutout(uri, ra, dec, size)    download + cache one image cutout
build_target(params)                full pipeline: query -> cutouts -> frames -> manifest
extract_spectrum(params)            on-the-fly spectrum at the target position
fetch_overlays(ra, dec, fov)        Gaia / CatWISE sources for the same field

The data products are SPHEREx Level-2 "Spectral Image MEFs".  Each MEF is
one detector array (D1-D6).  Within an image every pixel has its own central
wavelength (the SPHEREx linear-variable filters), so a single exposure is
already a 2-D image of the sky with a built-in spectral dimension.  The
pipeline reprojects every exposure onto a common north-up TAN grid, groups
the six detector arrays into three colour channels, and writes aligned PNG
frames plus a JSON manifest.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

import numpy as np
import requests
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS
from PIL import Image
from scipy.ndimage import map_coordinates, median_filter

# SPHEREx headers carry SIP distortion terms on an alternate WCS; astropy prints
# an informational warning that is harmless for our purposes.
warnings.filterwarnings("ignore", message=".*Inconsistent SIP.*")
warnings.filterwarnings("ignore", message=".*SIP coefficients.*")
logging.getLogger("astropy").setLevel(logging.ERROR)

# --------------------------------------------------------------------------
# Paths / constants
# --------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
TARGET_DIR = DATA_DIR / "targets"

TAP_URL = "https://irsa.ipac.caltech.edu/TAP/sync"
IRSA_BASE = "https://irsa.ipac.caltech.edu/"
USER_AGENT = "Project-WATS/1.0 (public SPHEREx blink comparator)"

DEFAULT_FOV_DEG = 0.15
DEFAULT_MAX_FRAMES = 6
DEFAULT_NAXIS = 320
DEFAULT_QUERY_TTL = 6 * 3600          # re-query TAP at most every 6 h
DEFAULT_OVERLAY_TTL = 60 * 60         # auxiliary catalogues: 1 h
DEFAULT_SPECTRUM_TTL = 24 * 3600      # spectra only change when cutouts do

# SPHEREx detector bandpasses (microns), from the IRSA documentation.
DETECTOR_BANDS = {
    1: (0.755, 1.10),
    2: (1.10, 1.62),
    3: (1.63, 2.41),
    4: (2.42, 3.82),
    5: (3.83, 4.41),
    6: (4.42, 5.00),
}
DETECTOR_NAMES = {i: f"D{i}" for i in range(1, 7)}

# Broad colour channels used for composites.
CHANNEL_BLUE = 0
CHANNEL_GREEN = 1
CHANNEL_RED = 2
CHANNEL_NAMES = {CHANNEL_BLUE: "blue (0.75-1.6 um)", CHANNEL_GREEN: "green (1.6-2.4 um)", CHANNEL_RED: "red (2.4-5.0 um)"}

# Bits in the SPHEREx FLAGS extension that mark unusable pixels.
# Bit 21 (SOURCE) is intentionally excluded: it merely flags known sources.
BAD_FLAG_BITS = (0, 1, 2, 6, 7, 9, 10, 11, 14, 15, 17, 19, 20, 22, 23, 24, 25, 26, 27, 28)

# SPHEREx image pixel scale is ~6.15 arcsec (IRSA s_pixel_scale).
PIXEL_SCALE_ARCSEC = 6.15

logger = logging.getLogger("wats")


class WatError(RuntimeError):
    """A user-facing error from the WATS pipeline."""


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, obj) -> None:
    _atomic_write_bytes(path, json.dumps(obj, indent=2, default=_json_default).encode("utf-8"))


def _json_default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    return str(obj)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _hash(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _as_float_array(value) -> np.ndarray:
    if isinstance(value, u.Quantity):
        return np.asarray(value.value, dtype=float)
    return np.asarray(value, dtype=float)


def _to_um(value) -> np.ndarray:
    """Convert an astropy spectral quantity/array to microns."""
    if isinstance(value, u.Quantity):
        try:
            return np.asarray(value.to(u.um).value, dtype=float)
        except Exception:
            pass
    if hasattr(value, "to"):
        try:
            return np.asarray(value.to(u.um).value, dtype=float)
        except Exception:
            pass
    return np.asarray(value, dtype=float)


def mjd_to_iso(mjd) -> str:
    try:
        dt = datetime.fromtimestamp((float(mjd) - 40587.0) * 86400.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return str(mjd)


def mjd_to_parts(mjd):
    try:
        dt = datetime.fromtimestamp((float(mjd) - 40587.0) * 86400.0, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
    except Exception:
        return str(mjd), ""


def slugify(value: str, max_len: int = 48) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "custom")).strip("_")
    return (value[:max_len] or "custom")


def parse_ra(text) -> float:
    """Parse an RA in decimal degrees or sexagesimal hours."""
    if text is None:
        raise ValueError("RA is required")
    if isinstance(text, (int, float)):
        ra = float(text)
    else:
        s = str(text).strip()
        m = re.match(r"^([+-]?\d+(?:\.\d+)?)$", s)
        if m:
            ra = float(m.group(1))
        else:
            m = re.match(r"^(\d{1,2})[hH:\s]+(\d{1,2})[mM:\s]+(\d+(?:\.\d+)?)[sS]?$", s)
            if not m:
                m = re.match(r"^(\d{1,2})[hH:\s]+(\d{1,2})[mM]?$", s)
                if not m:
                    raise ValueError("RA must be decimal degrees or e.g. 08h55m10.8s")
                ra = 15.0 * (float(m.group(1)) + float(m.group(2)) / 60.0)
            else:
                ra = 15.0 * (float(m.group(1)) + float(m.group(2)) / 60.0 + float(m.group(3)) / 3600.0)
    if not math.isfinite(ra):
        raise ValueError("RA is not a finite number")
    return ra % 360.0


def parse_dec(text) -> float:
    """Parse a Dec in decimal degrees or sexagesimal degrees."""
    if text is None:
        raise ValueError("Dec is required")
    if isinstance(text, (int, float)):
        dec = float(text)
    else:
        s = str(text).strip()
        m = re.match(r"^([+-]?\d+(?:\.\d+)?)$", s)
        if m:
            dec = float(m.group(1))
        else:
            m = re.match(r"^([+-]?\d{1,3})[dD:\s]+(\d{1,2})[mM:\s]+(\d+(?:\.\d+)?)[sS]?$", s)
            if not m:
                m = re.match(r"^([+-]?\d{1,3})[dD:\s]+(\d{1,2})[mM]?$", s)
                if not m:
                    raise ValueError("Dec must be decimal degrees or e.g. -7d14m42.5s")
                dec = float(m.group(1)) + math.copysign(float(m.group(2)) / 60.0, float(m.group(1)))
            else:
                dec = float(m.group(1)) + math.copysign(
                    float(m.group(2)) / 60.0 + float(m.group(3)) / 3600.0, float(m.group(1))
                )
    if not math.isfinite(dec):
        raise ValueError("Dec is not a finite number")
    if dec < -90.0 or dec > 90.0:
        raise ValueError("Dec must be between -90 and +90 degrees")
    return dec


# --------------------------------------------------------------------------
# TAP
# --------------------------------------------------------------------------


def _tap_error(text: str) -> Optional[str]:
    m = re.search(r'QUERY_STATUS"\s+value="ERROR">(.*?)</INFO>', text, re.S | re.I)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return None


def tap_query(query: str, timeout: int = 240, retries: int = 4) -> list[dict]:
    """Run an ADQL query against IRSA TAP and return a list of dict rows."""
    if not query or not query.strip():
        raise WatError("Empty TAP query")
    last = None
    for attempt in range(retries):
        try:
            resp = requests.post(
                TAP_URL,
                data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query},
                headers={"User-Agent": USER_AGENT},
                timeout=(15, timeout),
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                last = f"TAP HTTP {resp.status_code}"
                if attempt < retries - 1:
                    time.sleep(min(2 ** attempt, 15))
                continue
            if resp.status_code != 200:
                raise WatError(f"TAP HTTP {resp.status_code}: {resp.text[:300]}")
            text = resp.text.strip()
            if not text:
                last = "empty TAP response"
                if attempt < retries - 1:
                    time.sleep(min(2 ** attempt, 10))
                continue
            if text.startswith("<") or text.startswith("<?xml"):
                err = _tap_error(text)
                if err:
                    raise WatError(f"TAP query error: {err}")
                return []
            if text.lower().startswith("error") or "<html" in text.lower()[:500]:
                raise WatError(f"TAP error response: {text[:300]}")
            reader = csv.DictReader(io.StringIO(text))
            return [dict(row) for row in reader]
        except WatError:
            raise
        except Exception as exc:  # network / timeout / parse
            last = str(exc)
            logger.warning("TAP attempt %d failed: %s", attempt + 1, exc)
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 15))
    raise WatError(f"IRSA TAP is unavailable right now ({last}). Please try again.")


def _f(row: dict, key: str, default=None):
    try:
        value = row.get(key)
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def parse_observation(uri: str) -> dict:
    """Extract observation id, dither, detector, release and version from a URI."""
    name = str(uri).rstrip("/").split("/")[-1]
    m = re.search(r"level2_(.+?)_(\d+)D(\d)_spx_", name)
    if not m:
        return {"obs_id": name, "obs_base": name, "dither": 0, "detector": 0, "release": "", "version": ""}
    obs_id = m.group(1)
    dither = int(m.group(2))
    detector = int(m.group(3))
    obs_base = obs_id.rsplit("_", 1)[0]
    release = "dr1" if "/dr1/" in uri else ("qr3" if "/qr3/" in uri else ("qr2" if "/qr2/" in uri else ""))
    vm = re.search(r"l2b(?:_retry)?-v(\d+)-(\d{4}-\d+)", name)
    version = f"v{vm.group(1)}-{vm.group(2)}" if vm else ""
    return {
        "obs_id": obs_id,
        "obs_base": obs_base,
        "dither": dither,
        "detector": detector,
        "release": release,
        "version": version,
    }


def query_spherex(ra: float, dec: float, max_rows: int = 5000, refresh: bool = False,
                  min_mjd: Optional[float] = None, max_mjd: Optional[float] = None) -> list[dict]:
    """Return every SPHEREx detector MEF whose footprint contains (ra, dec)."""
    ra = float(ra)
    dec = float(dec)
    cache_key = _hash("spherex", f"{ra:.7f}", f"{dec:.7f}", max_rows, min_mjd, max_mjd)
    cache_path = CACHE_DIR / f"query_{cache_key}.json"
    if not refresh:
        cached = _read_json(cache_path)
        if cached and isinstance(cached.get("rows"), list):
            age = time.time() - float(cached.get("fetched_at", 0))
            if age < DEFAULT_QUERY_TTL:
                return cached["rows"]

    where = [f"1 = CONTAINS(POINT('ICRS', {ra:.8f}, {dec:.8f}), p.poly)"]
    if min_mjd is not None:
        where.append(f"p.time_bounds_lower >= {float(min_mjd):.6f}")
    if max_mjd is not None:
        where.append(f"p.time_bounds_lower <= {float(max_mjd):.6f}")
    query = f"""
        SELECT TOP {int(max_rows)}
               a.uri,
               p.time_bounds_lower,
               p.time_bounds_upper,
               p.energy_bandpassname,
               p.energy_bounds_lower,
               p.energy_bounds_upper,
               p.time_exposure
        FROM spherex.artifact a
        JOIN spherex.plane p ON a.planeid = p.planeid
        WHERE {' AND '.join(where)}
        ORDER BY p.time_bounds_lower
    """
    raw = tap_query(query)
    rows = []
    seen = set()
    for row in raw:
        uri = str(row.get("uri") or "").strip()
        if not uri or uri in seen:
            continue
        seen.add(uri)
        info = parse_observation(uri)
        mjd = _f(row, "time_bounds_lower")
        if mjd is None:
            continue
        em_lo = _f(row, "energy_bounds_lower")
        em_hi = _f(row, "energy_bounds_upper")
        detector = info["detector"]
        if detector in DETECTOR_BANDS:
            band_lo, band_hi = DETECTOR_BANDS[detector]
        else:
            band_lo = (em_lo * 1e6) if em_lo else 0.0
            band_hi = (em_hi * 1e6) if em_hi else 0.0
        rows.append(
            {
                "uri": uri,
                "url": IRSA_BASE + uri.lstrip("/"),
                "mjd": float(mjd),
                "time_bounds_lower": float(mjd),
                "time_bounds_upper": _f(row, "time_bounds_upper", mjd),
                "time": mjd_to_iso(mjd),
                "detector": detector,
                "detector_name": DETECTOR_NAMES.get(detector, f"D{detector}"),
                "bandpass": str(row.get("energy_bandpassname") or ""),
                "wavelength_min_um": float(band_lo),
                "wavelength_max_um": float(band_hi),
                "exptime": _f(row, "time_exposure", 0.0),
                "obs_id": info["obs_id"],
                "obs_base": info["obs_base"],
                "dither": info["dither"],
                "release": info["release"],
                "version": info["version"],
            }
        )
    rows.sort(key=lambda r: r["mjd"])
    _atomic_write_json(
        cache_path,
        {
            "query": query,
            "fetched_at": time.time(),
            "ra": ra,
            "dec": dec,
            "rows": rows,
        },
    )
    return rows


# --------------------------------------------------------------------------
# Cutout download / loading
# --------------------------------------------------------------------------


def cutout_url(uri: str, ra: float, dec: float, size_deg: float) -> str:
    base = str(uri)
    if not base.lower().startswith(("http://", "https://")):
        base = IRSA_BASE + base.lstrip("/")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}center={float(ra):.8f},{float(dec):.8f}&size={float(size_deg):.5f}&gzip=0"


def _cutout_cache_path(url: str) -> Path:
    return CACHE_DIR / f"cutout_{hashlib.sha1(url.encode('utf-8')).hexdigest()[:24]}.fits"


def cutout_size_for(fov_deg: float) -> float:
    """Cutout size that comfortably covers the output field.

    Used by both the frame builder and the spectrum extractor so that they
    download (and cache) exactly the same cutout.
    """
    return max(0.12, float(fov_deg) * 1.35)


def fetch_cutout(uri: str, ra: float, dec: float, size_deg: float, retries: int = 4) -> Path:
    """Download a SPHEREx MEF cutout and cache it.  Returns the local path."""
    url = cutout_url(uri, ra, dec, size_deg)
    path = _cutout_cache_path(url)
    if path.exists() and path.stat().st_size > 1024:
        return path
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    last = None
    deadline = time.time() + 150
    for attempt in range(retries):
        if time.time() > deadline:
            break
        try:
            time.sleep(0.35)
            t0 = time.time()
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=(10, 60))
            if resp.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {resp.status_code}"
                logger.warning("Cutout %s attempt %d: %s", path.name, attempt + 1, last)
                if attempt < retries - 1:
                    time.sleep(min(2 ** attempt, 25))
                continue
            if resp.status_code != 200:
                raise WatError(f"IRSA cutout HTTP {resp.status_code}: {resp.text[:250]}")
            data = resp.content
            if len(data) < 1024:
                raise WatError("IRSA returned an empty cutout")
            # Validate before committing to cache.
            with fits.open(io.BytesIO(data), memmap=False) as hdul:
                if "IMAGE" not in [h.name for h in hdul]:
                    raise WatError("Cutout is missing an IMAGE extension")
            _atomic_write_bytes(path, data)
            logger.info("Cutout %s (%.1f KB) in %.1fs", path.name, len(data) / 1024.0, time.time() - t0)
            return path
        except WatError:
            raise
        except Exception as exc:
            last = str(exc)
            logger.warning("Cutout %s attempt %d failed: %s", path.name, attempt + 1, exc)
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 25))
    raise WatError(f"Could not download a SPHEREx cutout ({last}). IRSA may be busy; try again.")


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
        flags = np.array(flags_hdu.data, dtype=np.int64, copy=True) if flags_hdu is not None and flags_hdu.data is not None else np.zeros_like(image, dtype=np.int64)
        variance = np.array(var_hdu.data, dtype=np.float64, copy=True) if var_hdu is not None and var_hdu.data is not None else np.full_like(image, np.nan)
        zodi = np.array(zodi_hdu.data, dtype=np.float64, copy=True) if zodi_hdu is not None and zodi_hdu.data is not None else np.zeros_like(image)
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


# --------------------------------------------------------------------------
# Reprojection / rendering
# --------------------------------------------------------------------------


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


def reproject_cutout(cut: Cutout, ra: float, dec: float, fov_deg: float, naxis: int) -> Reprojected:
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
    image = map_coordinates(cut.image, coords, order=1, mode="constant", cval=np.nan)
    variance = map_coordinates(cut.variance, coords, order=1, mode="constant", cval=np.nan)
    zodi = map_coordinates(cut.zodi, coords, order=1, mode="constant", cval=np.nan)
    wavelength = map_coordinates(cut.wavelength, coords, order=1, mode="constant", cval=np.nan)
    bandwidth = map_coordinates(cut.bandwidth, coords, order=1, mode="constant", cval=np.nan)
    flags = map_coordinates(cut.flags.astype(np.float64), coords, order=0, mode="constant", cval=0).astype(np.int64)
    bad = np.zeros(image.shape, dtype=bool)
    for bit in BAD_FLAG_BITS:
        bad |= (flags & (1 << bit)) != 0
    # Flagged pixels (dead/hot/cosmic/transient) must not contribute to the
    # mean stack or the rendered frames.  Keep `bad` for the metadata counts.
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


def _nanmean_stack(arrays: list[np.ndarray]) -> np.ndarray:
    if not arrays:
        return np.full(arrays[0].shape if arrays else (1, 1), np.nan)
    if len(arrays) == 1:
        return arrays[0].astype(float, copy=True)
    stack = np.stack(arrays, axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return np.nanmean(stack, axis=0)


def _nanmedian_stack(arrays: list[np.ndarray]) -> np.ndarray:
    if not arrays:
        return np.full((1, 1), np.nan)
    if len(arrays) == 1:
        return arrays[0].astype(float, copy=True)
    stack = np.stack(arrays, axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return np.nanmedian(stack, axis=0)


def _subtract_background(img: np.ndarray, size: int = 15) -> np.ndarray:
    """Flatten large-scale detector/zodiacal structure for display.

    A median filter is a cheap, robust background estimate.  It removes the
    smooth LVF/zodiacal gradient while leaving point sources intact.
    """
    if img is None:
        return img
    finite = np.isfinite(img)
    if not np.any(finite):
        return img
    fill = float(np.nanmedian(img[finite]))
    filled = np.where(finite, img, fill)
    size = max(5, int(size) | 1)  # odd
    bg = median_filter(filled, size=size, mode="nearest")
    return img - bg


def compute_scale(images: Iterable[np.ndarray], sample: int = 60000) -> dict:
    """Robust global display scale from a set of images."""
    vals = []
    for img in images:
        if img is None:
            continue
        arr = np.asarray(img, dtype=float).ravel()
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            continue
        if arr.size > sample:
            idx = np.linspace(0, arr.size - 1, sample).astype(int)
            arr = arr[idx]
        vals.append(arr)
    if not vals:
        return {"bg": 0.0, "mad": 1.0, "vmin": -1.0, "vmax": 1.0}
    allv = np.concatenate(vals)
    bg = float(np.median(allv))
    mad = float(np.median(np.abs(allv - bg)) * 1.4826)
    if not math.isfinite(mad) or mad <= 0:
        mad = float(np.std(allv)) or 1.0
    hi = float(np.nanpercentile(allv, 99.0))
    lo = float(np.nanpercentile(allv, 1.0))
    if not math.isfinite(hi) or hi <= bg:
        hi = float(np.max(allv))
    if hi <= bg:
        hi = bg + 5.0 * mad
    vmin = min(lo, bg - 3.0 * mad)
    return {"bg": bg, "mad": mad, "vmin": vmin, "vmax": hi}


def _norm_channel(img: np.ndarray, scale: dict, gamma: float = 1.0) -> np.ndarray:
    bg = float(scale.get("bg", 0.0))
    vmax = float(scale.get("vmax", 1.0))
    if not math.isfinite(vmax) or vmax <= bg:
        vmax = bg + 1.0
    v = (np.asarray(img, dtype=float) - bg) / (vmax - bg)
    v = np.clip(v, 0.0, 1.0)
    if gamma and gamma != 1.0:
        v = np.power(v, 1.0 / float(gamma))
    return v


def render_gray(img: np.ndarray, scale: dict, gamma: float = 2.0) -> Image.Image:
    v = _norm_channel(img, scale, gamma)
    v = np.nan_to_num(v, nan=0.0, posinf=1.0, neginf=0.0)
    return Image.fromarray((np.clip(v, 0, 1) * 255.0 + 0.5).astype(np.uint8), mode="L")


def render_color(channels: dict, scales: dict, gamma: float = 2.0) -> Image.Image:
    """channels: {0: blue_image, 1: green_image, 2: red_image}."""
    out = np.zeros((320, 320, 3), dtype=float)
    # Determine shape from the first available channel.
    for ch in (CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED):
        img = channels.get(ch)
        if img is not None:
            out = np.zeros((img.shape[0], img.shape[1], 3), dtype=float)
            break
    for ch in (CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED):
        img = channels.get(ch)
        if img is None:
            continue
        scale = scales.get(str(ch)) or scales.get(ch) or {"bg": 0.0, "vmax": 1.0}
        v = _norm_channel(img, scale, gamma)
        out[..., ch] = np.nan_to_num(v, nan=0.0)
    rgb = np.clip(out, 0, 1)
    return Image.fromarray((rgb * 255.0 + 0.5).astype(np.uint8), mode="RGB")


# --------------------------------------------------------------------------
# Target build
# --------------------------------------------------------------------------


def cache_key(params: dict) -> str:
    detector = str(params.get("detector") or "all").strip().upper()
    if detector in ("", "ALL", "ANY"):
        detector = "all"
    elif not detector.startswith("D"):
        detector = "D" + detector
    return _hash(
        "target",
        f"{float(params['ra']):.7f}",
        f"{float(params['dec']):.7f}",
        f"{float(params.get('fov', DEFAULT_FOV_DEG)):.5f}",
        max(1, int(params.get("max_frames", DEFAULT_MAX_FRAMES))),
        detector,
        int(params.get("naxis", DEFAULT_NAXIS)),
    )


def target_dir(key: str) -> Path:
    return TARGET_DIR / key


def load_manifest(key: str):
    path = target_dir(key) / "manifest.json"
    return _read_json(path)


def save_manifest(key: str, manifest: dict) -> None:
    _atomic_write_json(target_dir(key) / "manifest.json", manifest)


def clear_target(key: str) -> None:
    import shutil

    path = target_dir(key)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _select_epochs(rows: list[dict], max_frames: int, detector: Optional[str]) -> list[dict]:
    """Group MEFs by visit, pick one row per detector, and thin to max_frames."""
    if detector:
        try:
            det = int(str(detector).upper().lstrip("D"))
        except Exception:
            det = 0
        if det:
            rows = [r for r in rows if int(r.get("detector", 0)) == det]
    groups: dict[str, dict] = {}
    for row in rows:
        # Group by the full visit id (obs_id), not obs_base.  obs_base is the
        # pointing/week, and several visits share it; merging them would mix
        # images taken at different times into one "frame" and would cap the
        # time series at the number of pointings.
        visit = str(row.get("obs_id") or row.get("obs_base") or row.get("uri", ""))
        group = groups.setdefault(visit, {"base": visit, "mjd": float(row.get("mjd", 0)), "rows": {}, "all_rows": []})
        group["all_rows"].append(row)
        det = int(row.get("detector", 0))
        # One MEF per detector per visit; prefer the longest exposure / earliest dither.
        prev = group["rows"].get(det)
        if prev is None or float(row.get("exptime", 0)) > float(prev.get("exptime", 0)):
            group["rows"][det] = row
    epochs = []
    for base, group in groups.items():
        if not group["rows"]:
            continue
        selected = [group["rows"][det] for det in sorted(group["rows"])]
        epochs.append(
            {
                "base": base,
                "mjd": min(float(r.get("mjd", 0)) for r in selected),
                "rows": selected,
                "n_rows": len(group["all_rows"]),
            }
        )
    epochs.sort(key=lambda e: e["mjd"])
    if max_frames and len(epochs) > int(max_frames):
        n = int(max_frames)
        idx = np.unique(np.linspace(0, len(epochs) - 1, n).round().astype(int))
        epochs = [epochs[i] for i in idx]
    return epochs


def _combine_epoch(items: list[dict], fov: float, naxis: int) -> dict:
    """Combine all detector re-projections for one visit into gray + RGB images."""
    channels: dict[int, list[dict]] = {CHANNEL_BLUE: [], CHANNEL_GREEN: [], CHANNEL_RED: []}
    for item in items:
        ch = channel_for_detector(int(item["detector"]))
        channels[ch].append(item)
    combined = {}
    for ch, group in channels.items():
        if not group:
            combined[ch] = None
            continue
        images = [g["reproj"].image for g in group]
        variances = [g["reproj"].variance for g in group]
        wavelengths = [g["reproj"].wavelength for g in group]
        img = _nanmean_stack(images)
        var = _nanmean_stack(variances)
        wl = _nanmean_stack(wavelengths)
        combined[ch] = {"image": img, "variance": var, "wavelength": wl, "n": len(group), "rows": group}
    available = [combined[ch]["image"] for ch in (CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED) if combined.get(ch)]
    gray = _nanmean_stack(available) if available else np.full((naxis, naxis), np.nan)
    bg_size = max(9, int(naxis / 16))
    for ch in (CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED):
        if combined.get(ch):
            combined[ch]["image_sub"] = _subtract_background(combined[ch]["image"], bg_size)
    gray_sub = _subtract_background(gray, bg_size)
    return {
        "channels": combined,
        "gray": gray,
        "gray_sub": gray_sub,
        "n_channels": sum(1 for ch in (CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED) if combined.get(ch)),
    }


def _frame_metadata(epoch: dict, combined: dict, fov: float, naxis: int) -> dict:
    rows = epoch["rows"]
    # Only report detectors whose cutouts were actually downloaded and used.
    # A row can fail (IRSA 5xx/timeout) and must not appear in the metadata.
    used_items = [
        i
        for ch in (CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED)
        for i in (combined["channels"].get(ch) or {}).get("rows", [])
    ]
    detectors = sorted({int(i.get("detector", 0)) for i in used_items}) or sorted(
        {int(r.get("detector", 0)) for r in rows}
    )
    wavelengths_by_det = {}
    exptimes = []
    obs_ids = []
    releases = []
    versions = []
    sources = []
    flags_total = 0
    flags_bad = 0
    cy, cx = naxis // 2, naxis // 2
    for item in used_items:
        det = int(item.get("detector", 0))
        name = DETECTOR_NAMES.get(det, f"D{det}")
        try:
            wl = float(item["reproj"].wavelength[cy, cx])
            if math.isfinite(wl) and wl > 0:
                wavelengths_by_det[name] = wl
        except Exception:
            pass
        exptimes.append(float(item.get("exptime", 0) or 0))
        obs_ids.append(str(item.get("obs_id", "")))
        releases.append(str(item.get("release", "")))
        versions.append(str(item.get("version", "")))
        sources.append(str(item.get("uri", "")))
        flags_total += int(np.size(item["reproj"].flags))
        flags_bad += int(np.count_nonzero(item["reproj"].bad))
    if not used_items:
        # Defensive fallback: keep the metadata useful even if no cutout worked.
        obs_ids = [str(r.get("obs_id", "")) for r in rows]
        releases = [str(r.get("release", "")) for r in rows]
        versions = [str(r.get("version", "")) for r in rows]
    center_wl = float(np.mean(list(wavelengths_by_det.values()))) if wavelengths_by_det else None
    # Background / peak from the gray image.
    gray = combined["gray"]
    finite = gray[np.isfinite(gray)]
    bg = float(np.median(finite)) if finite.size else 0.0
    peak = float(np.nanmax(finite)) if finite.size else 0.0
    mad = float(np.median(np.abs(finite - bg)) * 1.4826) if finite.size else 0.0
    return {
        "mjd": float(epoch["mjd"]),
        "time": mjd_to_iso(epoch["mjd"]),
        "obs_id": ", ".join(sorted(set(obs_ids)))[:200],
        "obs_base": epoch.get("base", ""),
        "detectors": [DETECTOR_NAMES.get(d, f"D{d}") for d in detectors],
        "detector": DETECTOR_NAMES.get(detectors[0], "") if len(detectors) == 1 else "combined",
        "wavelength_um": center_wl,
        "wavelengths_um": wavelengths_by_det,
        "bandpass_um": [min(DETECTOR_BANDS.get(d, (0, 0))[0] for d in detectors), max(DETECTOR_BANDS.get(d, (0, 0))[1] for d in detectors)] if detectors else None,
        "exptime": float(np.mean(exptimes)) if exptimes else 0.0,
        "n_detectors": len(detectors),
        "n_channels": combined["n_channels"],
        "release": ", ".join(sorted({r for r in releases if r}))[:80],
        "version": ", ".join(sorted({v for v in versions if v}))[:80],
        "background_mjy_sr": bg,
        "noise_mjy_sr": mad,
        "peak_mjy_sr": peak,
        "flags_total": flags_total,
        "flags_bad": flags_bad,
        "flags_bad_fraction": (flags_bad / flags_total) if flags_total else 0.0,
        "sources": sources,
        "fov_deg": fov,
        "naxis": naxis,
    }


def build_target(params: dict, progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
                 force: bool = False) -> dict:
    """Run the complete query -> cutout -> reproject -> render -> manifest pipeline."""
    ra = float(params["ra"])
    dec = float(params["dec"])
    fov = float(params.get("fov", DEFAULT_FOV_DEG))
    max_frames = int(params.get("max_frames", DEFAULT_MAX_FRAMES))
    naxis = int(params.get("naxis", DEFAULT_NAXIS))
    detector = params.get("detector") or None
    refresh = bool(params.get("refresh", False))
    name = str(params.get("name") or "custom")

    if not (math.isfinite(ra) and math.isfinite(dec)):
        raise WatError("RA and Dec must be finite numbers")
    if not math.isfinite(fov) or fov <= 0:
        raise WatError("fov must be a positive number of degrees")
    if naxis < 8:
        raise WatError("naxis must be at least 8 pixels")
    max_frames = max(1, int(max_frames))
    if detector:
        det_text = str(detector).strip().upper()
        if det_text in ("ALL", "ANY"):
            detector = None
        else:
            det_text = det_text if det_text.startswith("D") else "D" + det_text
            if det_text not in {f"D{i}" for i in DETECTOR_BANDS}:
                raise WatError("detector must be D1..D6 or 'all'")
            detector = det_text

    key = cache_key(params)
    out_dir = target_dir(key)
    manifest_path = out_dir / "manifest.json"

    if not force and manifest_path.exists() and not refresh:
        cached = _read_json(manifest_path)
        if cached:
            cached["key"] = key
            return cached

    def report(stage, done, total, message):
        if progress_cb:
            try:
                progress_cb(stage, done, total, message)
            except Exception:
                pass

    report("query", 0, 1, "Querying IRSA TAP for SPHEREx images ...")
    rows = query_spherex(ra, dec, refresh=refresh)
    if not rows:
        raise WatError("No SPHEREx images cover that position. Try a slightly different coordinate or a later data release.")
    epochs = _select_epochs(rows, max_frames, detector)
    if not epochs:
        raise WatError("No usable SPHEREx detector images for that position.")
    report("query", 1, 1, f"Found {len(rows)} detector images in {len(epochs)} visits.")

    out_dir.mkdir(parents=True, exist_ok=True)
    cutout_size = cutout_size_for(fov)
    combined_epochs = []
    for i, epoch in enumerate(epochs, 1):
        report("fetch", i - 1, len(epochs), f"Fetching cutouts for visit {i}/{len(epochs)} ({epoch['mjd']:.2f} MJD) ...")
        items = []

        def process_row(row):
            uri = str(row["uri"])
            path = fetch_cutout(uri, ra, dec, cutout_size)
            cut = load_cutout(path)
            reproj = reproject_cutout(cut, ra, dec, fov, naxis)
            return {
                "row": row,
                "detector": int(row.get("detector") or cut.detector),
                "uri": uri,
                "path": str(path),
                "cutout": cut,
                "reproj": reproj,
                "exptime": float(row.get("exptime", 0) or 0),
                "obs_id": str(row.get("obs_id", "")),
                "release": str(row.get("release", "")),
                "version": str(row.get("version", "")),
            }

        workers = min(2, max(1, len(epoch["rows"])))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process_row, row): row for row in epoch["rows"]}
            for fut in as_completed(futures):
                row = futures[fut]
                try:
                    items.append(fut.result())
                except Exception as exc:
                    logger.warning("Skipping %s: %s", row.get("uri"), exc)
        items.sort(key=lambda it: it["detector"])
        if not items:
            report("fetch", i, len(epochs), f"Visit {i}/{len(epochs)}: no usable cutouts, skipping.")
            continue
        combined = _combine_epoch(items, fov, naxis)
        combined_epochs.append({"epoch": epoch, "items": items, "combined": combined})
        report("fetch", i, len(epochs), f"Visit {i}/{len(epochs)}: {combined['n_channels']} channel(s), {len(items)} image(s).")

    if not combined_epochs:
        raise WatError("No SPHEREx cutouts could be downloaded. IRSA may be busy; try again.")

    # Global display scales (per channel + gray) so that blinking does not flicker.
    gray_scales = compute_scale([e["combined"]["gray"] for e in combined_epochs])
    channel_scales = {}
    for ch in (CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED):
        imgs = [e["combined"]["channels"][ch]["image"] for e in combined_epochs if e["combined"]["channels"].get(ch)]
        channel_scales[str(ch)] = compute_scale(imgs) if imgs else {"bg": 0.0, "mad": 1.0, "vmin": -1.0, "vmax": 1.0}
    gray_sub_scales = compute_scale([e["combined"]["gray_sub"] for e in combined_epochs])
    channel_sub_scales = {}
    for ch in (CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED):
        imgs = [e["combined"]["channels"][ch]["image_sub"] for e in combined_epochs if e["combined"]["channels"].get(ch)]
        channel_sub_scales[str(ch)] = compute_scale(imgs) if imgs else {"bg": 0.0, "mad": 1.0, "vmin": -1.0, "vmax": 1.0}

    manifest_frames = []
    for i, entry in enumerate(combined_epochs):
        epoch = entry["epoch"]
        combined = entry["combined"]
        meta = _frame_metadata(epoch, combined, fov, naxis)
        gray_name = f"{i:03d}_gray.png"
        color_name = f"{i:03d}_color.png"
        gray_sub_name = f"{i:03d}_gray_sub.png"
        color_sub_name = f"{i:03d}_color_sub.png"
        render_gray(combined["gray"], gray_scales).save(out_dir / gray_name)
        render_gray(combined["gray_sub"], gray_sub_scales).save(out_dir / gray_sub_name)
        plain_channels = {ch: combined["channels"][ch]["image"] for ch in (CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED) if combined["channels"].get(ch)}
        sub_channels = {ch: combined["channels"][ch]["image_sub"] for ch in (CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED) if combined["channels"].get(ch)}
        render_color(plain_channels, channel_scales).save(out_dir / color_name)
        render_color(sub_channels, channel_sub_scales).save(out_dir / color_sub_name)
        meta.update(
            {
                "index": i,
                "gray": gray_name,
                "color": color_name,
                "gray_sub": gray_sub_name,
                "color_sub": color_sub_name,
                "gray_url": f"/frames/{key}/{gray_name}",
                "color_url": f"/frames/{key}/{color_name}",
                "gray_sub_url": f"/frames/{key}/{gray_sub_name}",
                "color_sub_url": f"/frames/{key}/{color_sub_name}",
            }
        )
        manifest_frames.append(meta)
        report("render", i + 1, len(combined_epochs), f"Rendered frame {i + 1}/{len(combined_epochs)}.")

    # Summary information.
    mjds = [f["mjd"] for f in manifest_frames]
    all_detectors = sorted({d for f in manifest_frames for d in f["detectors"]})
    manifest = {
        "key": key,
        "name": name,
        "ra": ra,
        "dec": dec,
        "fov_deg": fov,
        "naxis": naxis,
        "detector_filter": detector or "all",
        "max_frames": max_frames,
        "created": datetime.now(timezone.utc).isoformat(),
        "updated": datetime.now(timezone.utc).isoformat(),
        "data_release": "SPHEREx DR1 / QR3 (IRSA)",
        "tap_url": TAP_URL,
        "query_rows": len(rows),
        "visits": len(manifest_frames),
        "detectors": all_detectors,
        "time_range": [mjd_to_iso(min(mjds)), mjd_to_iso(max(mjds))] if mjds else None,
        "wcs": wcs_to_dict(make_output_wcs(ra, dec, fov, naxis), naxis, fov),
        "scale": {
            "plain": {"gray": gray_scales, "channels": channel_scales},
            "sub": {"gray": gray_sub_scales, "channels": channel_sub_scales},
        },
        "frames": manifest_frames,
    }
    save_manifest(key, manifest)
    report("done", len(combined_epochs), len(combined_epochs), f"Done: {len(manifest_frames)} frames.")
    manifest["key"] = key
    return manifest


# --------------------------------------------------------------------------
# Spectrum
# --------------------------------------------------------------------------


def _aperture_photometry(cut: Cutout, ra: float, dec: float, radius: float = 2.5,
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
    # If the exact pixel is flagged, search a small neighbourhood for a good pixel.
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
        wl = 0.5 * (DETECTOR_BANDS.get(cut.detector, (0, 0))[0] + DETECTOR_BANDS.get(cut.detector, (0, 0))[1])
    if not np.isfinite(bw):
        band = DETECTOR_BANDS.get(cut.detector, (0, 0))
        bw = max(0.0, band[1] - band[0])
    # Pixel solid angle (sr) from the spatial WCS.
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


def extract_spectrum(params: dict, use_cache: bool = True) -> dict:
    """Extract an on-the-fly spectrum at the target position from cached cutouts."""
    key = cache_key(params)
    manifest = load_manifest(key)
    if not manifest:
        raise WatError("Frames are not ready yet; load the target first.")
    ra = float(params["ra"])
    dec = float(params["dec"])
    fov = float(params.get("fov", manifest.get("fov_deg", DEFAULT_FOV_DEG)))
    if not math.isfinite(fov) or fov <= 0:
        raise WatError("fov must be a positive number of degrees")
    cache_path = target_dir(key) / "spectrum.json"
    # Invalidate when the target was rebuilt (a refresh can change the rows).
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
    for frame in manifest.get("frames", []):
        for uri in frame.get("sources", []):
            if uri in seen:
                continue
            seen.add(uri)
            try:
                path = fetch_cutout(uri, ra, dec, cutout_size_for(fov))
                cut = load_cutout(path)
                point = _aperture_photometry(cut, ra, dec)
                if point:
                    point["frame"] = int(frame.get("index", 0))
                    points.append(point)
            except Exception as exc:
                logger.warning("Spectrum: skipping %s: %s", uri, exc)
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
        }
    else:
        result = {"status": "empty", "key": key, "points": [], "n_points": 0,
                  "manifest_updated": manifest_updated,
                  "message": "No usable cutout pixels at the target position."}
    _atomic_write_json(cache_path, result)
    return result


# --------------------------------------------------------------------------
# Auxiliary catalogues (Gaia / CatWISE)
# --------------------------------------------------------------------------


def _overlay_cache_path(ra: float, dec: float, radius: float, naxis: int = DEFAULT_NAXIS) -> Path:
    # Pixel positions depend on the output WCS, so naxis is part of the key.
    return CACHE_DIR / f"overlay_{_hash('overlay', f'{ra:.5f}', f'{dec:.5f}', f'{radius:.4f}', int(naxis))}.json"


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
    cache_path = _overlay_cache_path(ra, dec, radius, naxis)
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


# --------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------


def health() -> dict:
    return {
        "status": "ok",
        "service": "Project WATS",
        "version": "1.0",
        "data": "SPHEREx DR1 / QR3 via NASA/IPAC IRSA",
        "tap": TAP_URL,
        "time": datetime.now(timezone.utc).isoformat(),
    }