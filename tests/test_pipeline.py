"""Offline integration tests for the target build pipeline and job API."""

from __future__ import annotations

import time


def _wait_for_job(client, job_id, timeout=15.0):
    deadline = time.time() + timeout
    job = None
    while time.time() < deadline:
        resp = client.get(f"/api/jobs/{job_id}")
        if resp.status_code == 200:
            job = (resp.get_json() or {}).get("job")
            if job and job["status"] in ("done", "error", "cancelled", "stale"):
                return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {job}")


def test_build_target_offline(clean_data, fake_tap_rows, fake_cutout):
    from backend.spherex.pipeline import build_target, cache_key, load_manifest

    params = {
        "ra": 133.795096,
        "dec": -7.245147,
        "fov": 0.15,
        "max_frames": 3,
        "naxis": 128,
        "detector": None,
        "name": "offline test",
        "refresh": False,
    }
    key = cache_key(params)
    manifest = build_target(params)
    assert manifest["key"] == key
    assert len(manifest["frames"]) == 3
    assert load_manifest(key)["key"] == key
    for frame in manifest["frames"]:
        for field in ("gray", "color", "gray_sub", "color_sub"):
            assert (clean_data / "targets" / key / frame[field]).is_file()


def test_job_api_offline(client, fake_tap_rows, fake_cutout):
    resp = client.post(
        "/api/targets/jobs?ra=133.795096&dec=-7.245147&fov=0.15&max_frames=3&naxis=128"
    )
    assert resp.status_code == 202, resp.get_json()
    payload = resp.get_json()
    job = payload["job"]
    assert job["target_key"]
    assert isinstance(job["params"], dict)
    done = _wait_for_job(client, job["id"])
    assert done["status"] == "done", done
    assert done["total"] >= 3


def test_target_spectrum_offline(client, fake_tap_rows, fake_cutout):
    start = client.post(
        "/api/targets/jobs?ra=133.795096&dec=-7.245147&fov=0.15&max_frames=3&naxis=128"
    )
    assert start.status_code == 202, start.get_json()
    job = _wait_for_job(client, start.get_json()["job"]["id"])
    assert job["status"] == "done", job
    key = job["target_key"]
    spec = client.get(f"/api/targets/{key}/spectrum")
    assert spec.status_code == 200, spec.get_json()
    data = spec.get_json()
    assert data["status"] in ("ready", "empty")
    assert data["key"] == key