"""Cutout URL building and downloading with retries and a per-cutout deadline."""

from __future__ import annotations

import hashlib
import io
import logging
import time
from pathlib import Path
from typing import Optional

import requests
from astropy.io import fits

from backend import config as _config
from backend.config import IRSA_BASE, USER_AGENT
from backend.spherex.query import WatError
from backend.spherex.util import _atomic_write_bytes

logger = logging.getLogger("wats")


def cutout_url(uri: str, ra: float, dec: float, size_deg: float) -> str:
    base = str(uri)
    if not base.lower().startswith(("http://", "https://")):
        base = IRSA_BASE + base.lstrip("/")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}center={float(ra):.8f},{float(dec):.8f}&size={float(size_deg):.5f}&gzip=0"


def _cutout_cache_path(url: str) -> Path:
    return Path(_config.CACHE_DIR) / f"cutout_{hashlib.sha1(url.encode('utf-8')).hexdigest()[:24]}.fits"


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
    """
    url = cutout_url(uri, ra, dec, size_deg)
    path = _cutout_cache_path(url)
    if path.exists() and path.stat().st_size > 1024:
        _touch_cutout(path, target_key=target_key, data_release=data_release)
        return path
    CACHE_DIR = Path(_config.CACHE_DIR)
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
            _touch_cutout(path, target_key=target_key, data_release=data_release)
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
