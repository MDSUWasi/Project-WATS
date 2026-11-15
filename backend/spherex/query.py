"""IRSA TAP queries, observation parsing, and coordinate parsing."""

from __future__ import annotations

import csv
import io
import math
import re
import time
from datetime import datetime, timezone
from typing import Optional

from backend.config import CACHE_DIR, IRSA_BASE, TAP_URL, USER_AGENT, DEFAULT_QUERY_TTL, DEFAULT_FOV_DEG, DEFAULT_MAX_FRAMES, DEFAULT_NAXIS, DEFAULT_OVERLAY_TTL, DEFAULT_SPECTRUM_TTL, DETECTOR_BANDS, DETECTOR_NAMES, CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED, BAD_FLAG_BITS, PIXEL_SCALE_ARCSEC, SPHEREx_RELEASES, DEFAULT_RELEASE, release_uri_pattern
from backend.spherex.util import _atomic_write_json, _read_json, _hash, _f, _json_default, _irsa_session

# SPHEREx headers carry SIP distortion terms on an alternate WCS; astropy prints
# an informational warning that is harmless for our purposes.
import warnings

warnings.filterwarnings("ignore", message=".*Inconsistent SIP.*")
warnings.filterwarnings("ignore", message=".*SIP coefficients.*")


class WatError(RuntimeError):
    """A user-facing error from the WATS pipeline."""


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


def slugify(value: str, max_len: int = 48) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "custom")).strip("_")
    return (value[:max_len] or "custom")


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


def _tap_error(text: str) -> Optional[str]:
    m = re.search(r'QUERY_STATUS"\s+value="ERROR">(.*?)</INFO>', text, re.S | re.I)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(1)).strip()
    return None


def tap_query(query: str, timeout: int = 300, retries: int = 6) -> list[dict]:
    """Run an ADQL query against IRSA TAP and return a list of dict rows.

    Respects WATS_REQUEST_TIMEOUT_S / WATS_NETWORK_TIMEOUT_S so deployments on
    restricted hosts (e.g. PythonAnywhere free tier with no outbound internet)
    fail fast instead of hanging for minutes per request.
    """
    import os

    if not query or not query.strip():
        raise WatError("Empty TAP query")

    # Cap timeout and retries for fast failure on restricted networks.
    try:
        cap = int(os.environ.get("WATS_NETWORK_TIMEOUT_S") or os.environ.get("WATS_REQUEST_TIMEOUT_S") or 0)
    except Exception:
        cap = 0
    if cap > 0:
        timeout = min(timeout, max(5, cap))
        retries = min(retries, 2)
    else:
        # Even without explicit config, avoid multi-minute hangs: keep each
        # attempt reasonable and bound total wall time.
        timeout = min(timeout, 30)
    # Hard wall-clock budget so we never block a request thread indefinitely.
    deadline = time.time() + max(10, min(timeout * max(1, retries), 45))

    last = None
    sess = _irsa_session()
    for attempt in range(retries):
        if time.time() > deadline:
            break
        remaining = max(5, int(deadline - time.time()))
        attempt_timeout = min(timeout, remaining)
        try:
            resp = sess.post(
                TAP_URL,
                data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": query},
                timeout=attempt_timeout,
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                last = f"TAP HTTP {resp.status_code}"
                if attempt < retries - 1 and time.time() < deadline - 2:
                    time.sleep(min(2 ** attempt, 5))
                continue
            if resp.status_code != 200:
                raise WatError(f"TAP HTTP {resp.status_code}: {resp.text[:300]}")
            text = resp.text.strip()
            if not text:
                last = "empty TAP response"
                if attempt < retries - 1 and time.time() < deadline - 2:
                    time.sleep(min(2 ** attempt, 3))
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
            if attempt < retries - 1 and time.time() < deadline - 2:
                time.sleep(min(2 ** attempt, 5))
    raise WatError(f"IRSA TAP is unavailable right now ({last}). Please try again.")


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


def query_spherex(
    ra: float,
    dec: float,
    max_rows: int = 5000,
    refresh: bool = False,
    min_mjd: Optional[float] = None,
    max_mjd: Optional[float] = None,
    release: Optional[str] = None,
) -> list[dict]:
    """Return every SPHEREx detector MEF whose footprint contains (ra, dec).

    If ``release`` is given (e.g. ``"dr1"``), only rows whose IBE URI matches
    that release's URI pattern are returned.  Pass ``None`` for all releases.
    """
    ra = float(ra)
    dec = float(dec)
    pattern = release_uri_pattern(release) if release else None
    cache_key = _hash("spherex", f"{ra:.7f}", f"{dec:.7f}", max_rows, min_mjd, max_mjd, release or "all")
    cache_path = CACHE_DIR / f"query_{cache_key}.json"
    stale_cached = None
    if not refresh:
        cached = _read_json(cache_path)
        if cached and isinstance(cached.get("rows"), list):
            stale_cached = cached["rows"]
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
    try:
        raw = tap_query(query)
    except Exception:
        # On restricted/offline hosts, serve stale cached query results if any.
        if stale_cached is not None:
            return stale_cached
        raise
    rows = []
    seen = set()
    for row in raw:
        uri = str(row.get("uri") or "").strip()
        if not uri or uri in seen:
            continue
        if pattern and pattern not in uri:
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
            "release": release or "all",
            "rows": rows,
        },
    )
    return rows
