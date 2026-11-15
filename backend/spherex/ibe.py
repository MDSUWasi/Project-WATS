"""Cutout URL building and downloading with retries and a per-cutout deadline."""

from __future__ import annotations

import hashlib
import io
import logging
import time
from pathlib import Path
from typing import Optional

from astropy.io import fits

from backend import config as _config
from backend.config import IRSA_BASE, USER_AGENT
from backend.spherex.query import WatError
from backend.spherex.util import _atomic_write_bytes, _irsa_session

logger = logging.getLogger("wats")


def cutout_url(uri: str, ra: float, dec: float, size_deg: float) -> str:
    base = str(uri)
    if not base.lower().startswith(("http://", "https://")):
        base = IRSA_BASE + base.lstrip("/")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}center={float(ra):.8f},{float(dec):.8f}&size={float(size_deg):.5f}&gzip=0"


def _s3_fallback_url(uri: str, ra: float, dec: float, size_deg: float) -> str:
    """Build a public S3 URL for the same cutout.

    IRSA URIs look like ``ibe/data/spherex/dr1/level2/2025W19_1B/...``
    The S3 bucket mirrors the same path under ``dr1/level2/...``.
    """
    base = str(uri)
    # Strip IRSA prefix if present.
    for prefix in ("ibe/data/spherex/", "https://irsa.ipac.caltech.edu/ibe/data/spherex/"):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    s3 = f"https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/{base}"
    sep = "&" if "?" in s3 else "?"
    return f"{s3}{sep}center={float(ra):.8f},{float(dec):.8f}&size={float(size_deg):.5f}&gzip=0"


def _s3_full_url(uri: str) -> str:
    """Build a public S3 URL for the full MEF file (no cutout params).

    S3 does not support the IBE cutout query format, so we download the
    full file and extract the cutout locally with astropy.
    """
    base = str(uri)
    for prefix in ("ibe/data/spherex/", "https://irsa.ipac.caltech.edu/ibe/data/spherex/"):
        if base.startswith(prefix):
            base = base[len(prefix):]
            break
    return f"https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/{base}"


def _cutout_cache_path(url: str) -> Path:
    return Path(_config.CACHE_DIR) / f"cutout_{hashlib.sha1(url.encode('utf-8')).hexdigest()[:24]}.fits"


def cached_cutout_path(uri: str, ra: float, dec: float, size_deg: float) -> Path | None:
    """Return the local path of a cached cutout if it exists, else None.

    Checks both the IRSA-style cutout cache key and the S3 full-file cache key
    so callers can prefer local data without attempting network access.
    """
    for url in (cutout_url(uri, ra, dec, size_deg), _s3_full_url(uri)):
        p = _cutout_cache_path(url)
        try:
            if p.exists() and p.stat().st_size > 1024:
                return p
        except OSError:
            pass
    return None


def cutout_size_for(fov_deg: float) -> float:
    """Cutout size that comfortably covers the output field."""
    return max(0.12, float(fov_deg) * 1.35)


def fetch_cutout(
    uri: str,
    ra: float,
    dec: float,
    size_deg: float,
    retries: int = 4,
    target_key: Optional[str] = None,
    data_release: Optional[str] = None,
    cache: Optional[object] = None,
) -> Path:
    """Download a SPHEREx MEF cutout and cache it.  Returns the local path.

    Every stored cutout is registered in ``cache_entries`` so the LRU eviction
    loop can reclaim it later; without that registration the on-disk cache grows
    without bound because eviction only ever looks at the database.

    When IRSA is unreachable (e.g. free-tier outbound internet blocked) the
    function transparently falls back to the public AWS S3 mirror at
    ``nasa-irsa-spherex.s3.us-east-1.amazonaws.com``.

    On restricted hosts, timeouts and retries are capped so a missing network
    path fails in seconds instead of hanging a request/job thread for minutes.
    """
    import os

    url = cutout_url(uri, ra, dec, size_deg)
    path = _cutout_cache_path(url)
    if path.exists() and path.stat().st_size > 1024:
        _touch_cutout(path, target_key=target_key, data_release=data_release)
        return path
    CACHE_DIR = Path(_config.CACHE_DIR)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        net_cap = int(os.environ.get("WATS_NETWORK_TIMEOUT_S") or os.environ.get("WATS_REQUEST_TIMEOUT_S") or 0)
    except Exception:
        net_cap = 0
    if net_cap > 0:
        retries = min(retries, 2)
        per_timeout = max(5, min(60, net_cap))
        deadline = time.time() + max(10, min(net_cap * retries, 45))
    else:
        per_timeout = 60
        deadline = time.time() + 120  # bound wall time even without explicit cap

    last = None
    sess = _irsa_session()
    for attempt in range(retries):
        if time.time() > deadline:
            break
        try:
            t0 = time.time()
            resp = sess.get(url, timeout=(10, per_timeout))
            if resp.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {resp.status_code}"
                logger.warning("Cutout %s attempt %d: %s", path.name, attempt + 1, last)
                if attempt < retries - 1 and time.time() < deadline - 2:
                    time.sleep(min(2 ** attempt, 5))
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
            _touch_cutout(path, target_key=target_key, data_release=data_release)
            logger.info("Cutout %s (%.1f KB) in %.1fs", path.name, len(data) / 1024.0, time.time() - t0)
            return path
        except WatError:
            raise
        except Exception as exc:
            last = str(exc)
            logger.warning("Cutout %s attempt %d failed: %s", path.name, attempt + 1, exc)
            if attempt < retries - 1 and time.time() < deadline - 2:
                time.sleep(min(2 ** attempt, 5))
    # IRSA failed — try the public AWS S3 mirror as a fallback.
    # S3 does NOT support the IBE cutout query format, so we download the
    # full MEF and extract the cutout locally with astropy.
    s3_url = _s3_full_url(uri)
    if s3_url and time.time() < deadline + 30:
        try:
            import urllib.request
            s3_path = _cutout_cache_path(s3_url)
            if s3_path.exists() and s3_path.stat().st_size > 1024:
                _touch_cutout(s3_path, target_key=target_key, data_release=data_release)
                return s3_path
            t0 = time.time()
            req = urllib.request.Request(s3_url, headers={"User-Agent": _config.USER_AGENT})
            s3_timeout = max(10, min(per_timeout, int(max(5, deadline + 30 - time.time())))) if net_cap > 0 else 60
            with urllib.request.urlopen(req, timeout=s3_timeout) as resp:
                data = resp.read()
            if len(data) < 1024:
                raise WatError("S3 returned an empty cutout")
            with fits.open(io.BytesIO(data), memmap=False) as hdul:
                if "IMAGE" not in [h.name for h in hdul]:
                    raise WatError("S3 cutout is missing an IMAGE extension")
            _atomic_write_bytes(s3_path, data)
            _touch_cutout(s3_path, target_key=target_key, data_release=data_release)
            logger.info("S3 cutout %s (%.1f KB) in %.1fs", s3_path.name, len(data) / 1024.0, time.time() - t0)
            return s3_path
        except Exception as exc:
            logger.warning("S3 fallback failed: %s", exc)
    raise WatError(f"Could not download a SPHEREx cutout ({last}). IRSA may be busy; try again.")


def _touch_cutout(
    path: Path,
    target_key: Optional[str] = None,
    data_release: Optional[str] = None,
    pin: bool = False,
) -> None:
    """Register/refresh a cached cutout in the ``cache_entries`` table.

    Failures here must never break a fetch — the on-disk file is already valid,
    so a bookkeeping error only degrades eviction, not correctness.
    """
    try:
        from backend.cache import Cache
        from backend.config import WatsConfig

        Cache(WatsConfig.from_env()).record_cache_entry(
            path,
            path.stat().st_size if path.exists() else 0,
            target_key=target_key,
            pin=pin,
            data_release=data_release,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Could not record cache entry for %s: %s", path, exc)
