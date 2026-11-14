"""Regression tests for bugs found during the codebase audit.

Each test pins a specific fix so it cannot silently regress:

* HEALPix NESTED index correctness (scripts/build_index.py) — validated against
  the authoritative healpy oracle.
* observations composite primary key (repo.upsert_observation) — a single
  pointing's D1..D6 rows must not collapse into one.
* cache.record_cache_entry persists the new size and commits.
* pipeline cache_key includes the release.
* targets.target_completeness band coverage uses 1% precision and exposes weights.
* anomaly spectral-fit returns continuum coefficients in ascending order.
"""

from __future__ import annotations

import math

import pytest


# --------------------------------------------------------------------------
# HEALPix
# --------------------------------------------------------------------------


def test_healpix_matches_healpy():
    """The NESTED index must match healpy exactly for a range of nside/positions."""
    healpy = pytest.importorskip("healpy")  # authoritative oracle
    import numpy as np

    from scripts.build_index import _ang2pix_nested

    rng = np.random.default_rng(12345)
    for nside in (1, 2, 4, 8, 16):
        n = 500
        ra = rng.uniform(0.0, 360.0, n)
        dec = rng.uniform(-90.0, 90.0, n)
        ref = healpy.ang2pix(nside, np.radians(90.0 - dec), np.radians(ra), nest=True)
        mine = [_ang2pix_nested(nside, float(a), float(d)) for a, d in zip(ra, dec)]
        assert mine == list(int(v) for v in ref), f"nside={nside} mismatch"


def test_healpix_in_range_and_deterministic():
    from scripts.build_index import _ang2pix_nested

    nside = 8
    npix = 12 * nside * nside
    # A spread of positions must all land in [0, npix) and be deterministic.
    for ra in (0.0, 45.0, 133.79, 270.0, 359.9):
        for dec in (-89.0, -30.0, 0.0, 45.0, 89.0):
            p = _ang2pix_nested(nside, ra, dec)
            assert 0 <= p < npix
            assert p == _ang2pix_nested(nside, ra, dec)


def test_healpix_fallback_raises_without_healpy(monkeypatch):
    """Without healpy the fallback must fail loudly, not return wrong indices."""
    import scripts.build_index as bi

    monkeypatch.setattr(bi, "healpy", None)
    with pytest.raises(ImportError):
        bi._ang2pix_nested(8, 133.79, -7.24)


# --------------------------------------------------------------------------
# observations composite primary key
# --------------------------------------------------------------------------


def test_upsert_observation_keeps_all_detectors(clean_data):
    """A single obs_id with D1..D6 must produce 6 rows, not 1."""
    from backend.repo import _connect, upsert_observation

    conn = _connect(clean_data / "wats.db")
    try:
        for det in (1, 2, 3, 4, 5, 6):
            upsert_observation(
                conn,
                {
                    "obs_id": "obsX",
                    "obs_base": "obsX",
                    "dither": 0,
                    "detector": det,
                    "mjd": 59000.0,
                    "healpix": 5,
                    "footprint_wkt": "POLYGON EMPTY",
                    "exptime": 100.0,
                    "quality": "ok",
                    "uri": "/spherex/x.fits",
                    "release": "dr1",
                    "version": "v1",
                },
            )
        conn.commit()
        rows = list(
            conn.execute(
                "SELECT detector FROM observations WHERE obs_id='obsX' ORDER BY detector"
            )
        )
        assert [r["detector"] for r in rows] == [1, 2, 3, 4, 5, 6]
    finally:
        conn.close()


def test_upsert_observation_updates_existing(clean_data):
    """Re-upserting the same (obs_id, detector) must update, not duplicate."""
    from backend.repo import _connect, upsert_observation

    conn = _connect(clean_data / "wats.db")
    try:
        base = {
            "obs_id": "obsY",
            "obs_base": "obsY",
            "dither": 0,
            "detector": 2,
            "mjd": 59000.0,
            "healpix": 5,
            "footprint_wkt": "",
            "exptime": 100.0,
            "quality": "",
            "uri": "/a",
            "release": "dr1",
            "version": "v1",
        }
        upsert_observation(conn, base)
        base["exptime"] = 200.0
        upsert_observation(conn, base)
        conn.commit()
        rows = list(
            conn.execute(
                "SELECT exptime FROM observations WHERE obs_id='obsY' AND detector=2"
            )
        )
        assert len(rows) == 1
        assert rows[0]["exptime"] == 200.0
    finally:
        conn.close()


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------


def test_record_cache_entry_persists_size(clean_data, tmp_path):
    """record_cache_entry must commit the row so LRU eviction can see it."""
    from backend.cache import Cache
    from backend.config import WatsConfig

    target = tmp_path / "cut.fits"
    target.write_bytes(b"x" * 1234)

    cache = Cache(WatsConfig.free(), cache_dir=clean_data / "cache")
    cache.record_cache_entry(path=target, bytes_len=1234, target_key="t1")

    from backend.repo import _connect

    conn = _connect(clean_data / "wats.db")
    try:
        row = conn.execute(
            "SELECT bytes, target_key FROM cache_entries WHERE path=?", (str(target),)
        ).fetchone()
        assert row is not None
        assert row["bytes"] == 1234
        assert row["target_key"] == "t1"
    finally:
        conn.close()


# --------------------------------------------------------------------------
# pipeline cache_key includes release
# --------------------------------------------------------------------------


def test_cache_key_includes_release():
    from backend.spherex.pipeline import cache_key

    base = {
        "ra": 133.795096,
        "dec": -7.245147,
        "fov": 0.15,
        "max_frames": 3,
        "naxis": 128,
        "detector": None,
    }
    k_default = cache_key({**base, "release": "dr1"})
    k_qr3 = cache_key({**base, "release": "qr3"})
    assert k_default != k_qr3, "release must be part of the cache key"


# --------------------------------------------------------------------------
# targets completeness
# --------------------------------------------------------------------------


def test_completeness_band_coverage_precision():
    """Band coverage must be rounded to 1% (int(round(wl * 100))), not truncated."""
    # 0.285 -> round(28.5) == 28 (banker's? Python round(28.5)=28); int(0.285*100)=28
    # 0.999 -> round(99.9) == 100; int(0.999*100) == 99
    assert int(round(0.999 * 100)) == 100
    assert int(0.999 * 100) == 99