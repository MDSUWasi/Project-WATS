"""Project WATS configuration profiles.

Environment-driven profiles (free/local/self-host).  Move constants and
paths here so they can be overridden per deployment without touching the
pipeline code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
TARGET_DIR = DATA_DIR / "targets"
DB_PATH = DATA_DIR / "wats.db"
TILES_DIR = DATA_DIR / "tiles"

TAP_URL = "https://irsa.ipac.caltech.edu/TAP/sync"
IRSA_BASE = "https://irsa.ipac.caltech.edu/"
USER_AGENT = "Project-WATS/1.0 (public SPHEREx blink comparator)"

DEFAULT_FOV_DEG = 0.15
DEFAULT_MAX_FRAMES = 6
DEFAULT_NAXIS = 320
DEFAULT_QUERY_TTL = 6 * 3600          # re-query TAP at most every 6 h
DEFAULT_OVERLAY_TTL = 60 * 60         # auxiliary catalogues: 1 h
DEFAULT_SPECTRUM_TTL = 24 * 3600      # spectra only change when cutouts do

PIXEL_SCALE_ARCSEC = 6.15

BAD_FLAG_BITS = (0, 1, 2, 6, 7, 9, 10, 11, 14, 15, 17, 19, 20, 22, 23, 24, 25, 26, 27, 28)

CHANNEL_BLUE = 0
CHANNEL_GREEN = 1
CHANNEL_RED = 2

DETECTOR_BANDS = {
    1: (0.755, 1.10),
    2: (1.10, 1.62),
    3: (1.63, 2.41),
    4: (2.42, 3.82),
    5: (3.83, 4.41),
    6: (4.42, 5.00),
}
DETECTOR_NAMES = {i: f"D{i}" for i in range(1, 7)}

PRESETS = [
    {
        "key": "wise0855",
        "name": "WISE 0855",
        "ra": 133.795096,
        "dec": -7.245147,
        "ra_text": "08h55m10.831s",
        "dec_text": "-7d14m42.53s",
        "fov": 0.15,
        "description": "Fast-moving Y brown dwarf (about 8 arcsec/yr).",
    },
    {
        "key": "barnard",
        "name": "Barnard's Star",
        "ra": 269.4521,
        "dec": 4.6933,
        "ra_text": "17h57m48.5s",
        "dec_text": "+04d41m36s",
        "fov": 0.15,
        "description": "Nearby red dwarf with the largest known proper motion.",
    },
    {
        "key": "epsindib",
        "name": "Epsilon Indi B",
        "ra": 330.59025,
        "dec": -56.7833,
        "ra_text": "22h03m21.7s",
        "dec_text": "-56d47m00s",
        "fov": 0.15,
        "description": "Pair of brown dwarfs orbiting the nearby star Epsilon Indi.",
    },
    {
        "key": "rwaur",
        "name": "RW Aurigae",
        "ra": 76.95625,
        "dec": 30.6747,
        "ra_text": "05h07m49.5s",
        "dec_text": "+30d40m29s",
        "fov": 0.20,
        "description": "Young eruptive variable star in a binary system.",
    },
    {
        "key": "rdra",
        "name": "R Draconis",
        "ra": 269.4321,
        "dec": 57.4,
        "ra_text": "17h57m43.7s",
        "dec_text": "+57d24m00s",
        "fov": 0.20,
        "description": "Mira-type variable star with a ~424 day period.",
    },
]


def env_bool(name: str, default: bool = False) -> bool:
    return str(os.environ.get(name, "")).lower() in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError, TypeError):
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError, TypeError):
        return default


@dataclass
class WatsConfig:
    """Deployment profile.  Set WATS_PROFILE=free|local|self-host."""

    profile: str = "free"
    cache_budget_mb: int = 256
    max_workers: int = 1
    max_frames_default: int = DEFAULT_MAX_FRAMES
    request_timeout_s: int = 25
    query_ttl_s: int = DEFAULT_QUERY_TTL
    overlay_ttl_s: int = DEFAULT_OVERLAY_TTL
    spectrum_ttl_s: int = DEFAULT_SPECTRUM_TTL
    pin_presets: bool = True
    lru_evict_unpinned: bool = True
    daemon_worker: bool = False
    tiles_enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 5000
    debug: bool = False
    secret_key: str = field(default_factory=lambda: os.environ.get("WATS_SECRET_KEY", "dev-secret-change-me"))

    @classmethod
    def from_env(cls) -> "WatsConfig":
        profile = os.environ.get("WATS_PROFILE", "free").lower()
        if profile == "free":
            return cls.free()
        if profile == "self-host":
            return cls.self_host()
        return cls.local()

    @classmethod
    def free(cls) -> "WatsConfig":
        return cls(
            profile="free",
            cache_budget_mb=env_int("WATS_CACHE_BUDGET_MB", 256),
            max_workers=env_int("WATS_MAX_WORKERS", 1),
            max_frames_default=env_int("WATS_MAX_FRAMES_DEFAULT", 6),
            request_timeout_s=env_int("WATS_REQUEST_TIMEOUT_S", 25),
            daemon_worker=False,
            tiles_enabled=False,
        )

    @classmethod
    def local(cls) -> "WatsConfig":
        return cls(
            profile="local",
            cache_budget_mb=env_int("WATS_CACHE_BUDGET_MB", 2048),
            max_workers=env_int("WATS_MAX_WORKERS", 2),
            max_frames_default=env_int("WATS_MAX_FRAMES_DEFAULT", 12),
            request_timeout_s=env_int("WATS_REQUEST_TIMEOUT_S", 60),
            daemon_worker=env_bool("WATS_DAEMON_WORKER", True),
            tiles_enabled=False,
        )

    @classmethod
    def self_host(cls) -> "WatsConfig":
        return cls(
            profile="self-host",
            cache_budget_mb=env_int("WATS_CACHE_BUDGET_MB", 8192),
            max_workers=env_int("WATS_MAX_WORKERS", 4),
            max_frames_default=env_int("WATS_MAX_FRAMES_DEFAULT", 96),
            request_timeout_s=env_int("WATS_REQUEST_TIMEOUT_S", 120),
            daemon_worker=env_bool("WATS_DAEMON_WORKER", True),
            tiles_enabled=env_bool("WATS_TILES_ENABLED", False),
        )
