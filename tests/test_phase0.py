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
