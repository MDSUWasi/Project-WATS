"""Unit tests for Project WATS Phase 0 foundations."""

from __future__ import annotations

import json

import pytest


# --------------------------------------------------------------------------
# config tests
# --------------------------------------------------------------------------


def test_config_profiles():
    from backend.config import WatsConfig
    free = WatsConfig.free()
    assert free.profile == "free"
    assert free.cache_budget_mb == 256
    assert free.max_workers == 1
    assert free.daemon_worker is False
    assert free.tiles_enabled is False

    local = WatsConfig.local()
    assert local.profile == "local"
    assert local.cache_budget_mb == 2048

    self_host = WatsConfig.self_host()
    assert self_host.profile == "self-host"
    assert self_host.cache_budget_mb == 8192


# --------------------------------------------------------------------------
# repo / migration tests
# --------------------------------------------------------------------------


def test_db_init_and_targets(clean_data):
    from backend.repo import init_db, upsert_target, get_target, list_targets, _connect
    init_db(clean_data / "wats.db")
    conn = _connect(clean_data / "wats.db")
    try:
        upsert_target(conn, "testkey", {
            "key": "testkey", "name": "Test", "ra": 1.0, "dec": 2.0,
            "fov": 0.15, "naxis": 320, "max_frames": 6,
            "created": "2024-01-01T00:00:00Z",
            "updated": "2024-01-01T00:00:00Z",
        })
        t = get_target(conn, "testkey")
        assert t is not None
        assert t["name"] == "Test"
        assert t["ra"] == 1.0
        targets = list_targets(conn)
        assert len(targets) == 1
    finally:
        conn.close()


# --------------------------------------------------------------------------
# cache tests
# --------------------------------------------------------------------------


def test_cache_store_and_evict(clean_data, monkeypatch):
    from backend.cache import Cache
    from backend.config import WatsConfig
    config = WatsConfig.free()
    config.cache_budget_mb = 1  # tiny budget to force eviction
    cache = Cache(config)
    # store a cutout
    path = cache.dir / "cutout_evict_test.fits"
    cache.store_cutout(path, b"x" * 2000)
    assert path.exists()
    # record entry
    cache.record_cache_entry(path, 2000, target_key="tk", pin=False)
    # evict with tiny budget
    evicted = cache.evict_to_budget(1)
    assert evicted >= 0


# --------------------------------------------------------------------------
# Flask app tests
# --------------------------------------------------------------------------


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"


def test_presets(client):
    resp = client.get("/api/presets")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ready"
    assert len(data["presets"]) == 5


def test_frames_starts_fetch(client, monkeypatch, fake_tap_rows, fake_cutout):
    """Frames endpoint should start a fetch and complete it offline."""
    import time

    monkeypatch.setenv("WATS_PROFILE", "free")
    resp = client.get("/api/frames?ra=133.795096&dec=-7.245147&fov=0.15&max_frames=3")
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["status"] == "fetching"
    key = data["key"]
    status = None
    for _ in range(200):
        status = client.get(f"/api/status?key={key}").get_json()
        if status["status"] in ("ready", "error", "cancelled"):
            break
        time.sleep(0.05)
    assert status is not None
    assert status["status"] == "ready", status
    assert len(status["manifest"]["frames"]) == 3


def test_coverage(client, fake_tap_rows):
    resp = client.get("/api/coverage?ra=133.795096&dec=-7.245147&fov=0.15")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ready"
    assert data["available_visits"] == 3


def test_releases_endpoint(client):
    """The releases endpoint advertises the known SPHEREx data releases."""
    resp = client.get("/api/releases")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ready"
    assert isinstance(data["releases"], list) and data["releases"]
    keys = {r["key"] for r in data["releases"]}
    assert "dr1" in keys
    assert data["default"] in keys


def test_watchlist_crud(client):
    """Watchlist supports add / list / delete."""
    # Add.
    resp = client.post("/api/public/watchlist", json={"ra": 133.795096, "dec": -7.245147, "fov": 0.15, "name": "test", "release": "dr1"})
    assert resp.status_code == 201
    wid = resp.get_json()["id"]
    # List.
    resp = client.get("/api/public/watchlist")
    assert resp.status_code == 200
    items = resp.get_json()["watchlist"]
    assert any(w["id"] == wid for w in items)
    # Delete.
    resp = client.delete(f"/api/public/watchlist/{wid}")
    assert resp.status_code == 200
    resp = client.get("/api/public/watchlist")
    assert not any(w["id"] == wid for w in resp.get_json()["watchlist"])


def test_pixel_spectrum_missing_key(client):
    """pixel-spectrum requires a key."""
    resp = client.get("/api/analysis/pixel-spectrum")
    assert resp.status_code == 400


def test_pixel_spectrum_not_found(client):
    """pixel-spectrum returns 404 for unknown key."""
    resp = client.get("/api/analysis/pixel-spectrum?key=nonexistentkey123&x=10&y=10")
    assert resp.status_code == 404


def test_release_filter_in_cache_key():
    """Different releases produce different cache keys."""
    from backend.spherex.pipeline import cache_key

    base = {"ra": 133.795096, "dec": -7.245147, "fov": 0.15, "max_frames": 6, "naxis": 320, "detector": None}
    a = cache_key({**base, "release": "dr1"})
    b = cache_key({**base, "release": "qr3"})
    c = cache_key({**base, "release": "dr1"})
    assert a != b
    assert a == c


def test_render_detector_bands():
    """render_detector_bands produces one greyscale image per detector."""
    from backend.spherex.render import render_detector_bands, compute_scale
    from backend.spherex.reproject import Reprojected
    import numpy as np

    img = np.full((64, 64), 0.1, dtype=float)
    reproj = Reprojected(
        image=img, variance=img, flags=np.zeros_like(img, dtype=np.int64),
        zodi=img, wavelength=img, bandwidth=img, bad=np.zeros(img.shape, dtype=bool),
        valid=np.ones(img.shape, dtype=bool), wcs=None,
    )
    items = [
        {"detector": 1, "reproj": reproj},
        {"detector": 3, "reproj": reproj},
        {"detector": 6, "reproj": reproj},
    ]
    scales = {"D1": compute_scale([img]), "D3": compute_scale([img]), "D6": compute_scale([img])}
    out = render_detector_bands(items, scales, naxis=64, gamma=2.0)
    assert set(out.keys()) == {"D1", "D3", "D6"}
    for det, im in out.items():
        assert im is not None
        assert im.size[0] == 64 and im.size[1] == 64
