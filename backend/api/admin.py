"""Admin API blueprint.

Endpoints:
  GET /api/admin/cache           cache statistics
  GET /api/admin/prefetch        prefetch pinned targets (best-effort)
  GET /api/admin/index           global observation index status
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify

from backend.cache import Cache
from backend.config import TILES_DIR, WatsConfig
from backend.jobs import JobRunner
from backend.repo import _connect, cache_stats, get_observation_count

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.get("/cache")
def cache_stats_endpoint():
    config = WatsConfig.from_env()
    cache = Cache(config)
    evicted = cache.evict_to_budget()
    conn = _connect()
    try:
        stats = cache_stats(conn)
    finally:
        conn.close()
    return jsonify({
        "status": "ready",
        "config": config.profile,
        "budget_mb": config.cache_budget_mb,
        "evicted": evicted,
        **stats,
    })


@bp.get("/prefetch")
def prefetch():
    runner = current_app.extensions.get("wats_runner")
    if runner is None:
        runner = JobRunner()
        current_app.extensions["wats_runner"] = runner
    # Best-effort: queue prefetch jobs for pinned presets without blocking.
    # Skip presets that already have a queued/running job so repeated calls
    # don't pile up duplicate fetches.
    from backend import spherex
    queued = []
    for preset in spherex.PRESETS:
        params = {
            "ra": preset["ra"], "dec": preset["dec"], "fov": preset["fov"],
            "max_frames": 6, "naxis": 320, "detector": None, "name": preset["name"],
            "refresh": False,
        }
        params["key"] = spherex.cache_key(params)
        key = params["key"]
        if runner.find_active(target_key=key, job_type="prefetch") is not None:
            queued.append({"target_key": key, "name": preset["name"], "job_id": None, "deduplicated": True})
            continue
        job_id = runner.create(job_type="prefetch", params=params, target_key=key)
        runner.run_threaded(
            job_id,
            lambda progress, params=params: spherex.build_target(params, progress_cb=progress),
            total_hint=params["max_frames"],
        )
        queued.append({"target_key": key, "name": preset["name"], "job_id": job_id})
    return jsonify({"status": "accepted", "queued": queued})


@bp.get("/index")
def index_status():
    conn = _connect()
    try:
        obs_count = get_observation_count(conn)
    finally:
        conn.close()
    # Check for built tiles.
    tiles_built = 0
    if TILES_DIR.is_dir():
        for d in TILES_DIR.iterdir():
            if d.is_dir() and d.name.isdigit():
                tiles_built += len(list(d.glob("*.png")))
    return jsonify({
        "status": "ready",
        "global_index": "built" if obs_count > 0 else "not built",
        "indexed_observations": obs_count,
        "tiles_built": tiles_built,
        "tiles_dir": str(TILES_DIR),
        "message": "run scripts/build_index.py to build the global index; scripts/build_tiles.py for tiles",
    })
