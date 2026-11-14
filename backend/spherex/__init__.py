"""Project WATS core library (package).

This package replaces the old monolithic ``backend/spherex.py``.  All public
names are re-exported here so existing imports (``from backend import spherex``)
continue to work unchanged.

Sub-modules:
    query       - IRSA TAP queries, observation parsing
    ibe         - cutout URL building and downloading
    fits        - MEF loading, Cutout dataclass
    reproject   - common TAN grid reprojection
    render      - gray/color PNG rendering
    spectrum    - on-the-fly spectrum extraction
    photometry  - aperture photometry
    catalogs    - Gaia/CatWISE overlays
    quality     - quality flags and completeness
    tiles       - tile builder (stub)
"""

from __future__ import annotations

from backend.config import PRESETS

from backend.spherex.query import (
    TAP_URL,
    IRSA_BASE,
    USER_AGENT,
    DEFAULT_FOV_DEG,
    DEFAULT_MAX_FRAMES,
    DEFAULT_NAXIS,
    DEFAULT_QUERY_TTL,
    DEFAULT_OVERLAY_TTL,
    DEFAULT_SPECTRUM_TTL,
    DETECTOR_BANDS,
    DETECTOR_NAMES,
    CHANNEL_BLUE,
    CHANNEL_GREEN,
    CHANNEL_RED,
    BAD_FLAG_BITS,
    PIXEL_SCALE_ARCSEC,
    WatError,
    parse_ra,
    parse_dec,
    slugify,
    mjd_to_iso,
    mjd_to_parts,
    tap_query,
    parse_observation,
    query_spherex,
)
from backend.spherex.ibe import cutout_url, fetch_cutout, cutout_size_for
from backend.spherex.fits import Cutout, load_cutout
from backend.spherex.reproject import (
    make_output_wcs,
    wcs_to_dict,
    reproject_cutout,
    Reprojected,
    channel_for_detector,
)
from backend.spherex.render import render_gray, render_color, compute_scale
from backend.spherex.spectrum import extract_spectrum
from backend.spherex.catalogs import fetch_overlays
from backend.spherex.quality import health
from backend.spherex.pipeline import (
    cache_key,
    target_dir,
    load_manifest,
    save_manifest,
    clear_target,
    build_target,
)

__all__ = [
    "PRESETS",
    "TAP_URL", "IRSA_BASE", "USER_AGENT",
    "DEFAULT_FOV_DEG", "DEFAULT_MAX_FRAMES", "DEFAULT_NAXIS",
    "DEFAULT_QUERY_TTL", "DEFAULT_OVERLAY_TTL", "DEFAULT_SPECTRUM_TTL",
    "DETECTOR_BANDS", "DETECTOR_NAMES",
    "CHANNEL_BLUE", "CHANNEL_GREEN", "CHANNEL_RED",
    "BAD_FLAG_BITS", "PIXEL_SCALE_ARCSEC",
    "WatError",
    "parse_ra", "parse_dec", "slugify", "mjd_to_iso", "mjd_to_parts",
    "tap_query", "parse_observation", "query_spherex",
    "cutout_url", "fetch_cutout", "cutout_size_for",
    "Cutout", "load_cutout",
    "make_output_wcs", "wcs_to_dict", "reproject_cutout", "Reprojected",
    "channel_for_detector",
    "render_gray", "render_color", "compute_scale",
    "extract_spectrum",
    "fetch_overlays",
    "health",
    "cache_key", "target_dir", "load_manifest", "save_manifest", "clear_target",
    "build_target",
]
