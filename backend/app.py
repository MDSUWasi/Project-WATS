"""Project WATS web API — application factory + blueprint registration.

Run:
    source .venv/bin/activate
    python3 backend/app.py

The module still exposes a module-level ``app`` (created with default config)
so existing entry points (`python3 app.py`, `python3 backend/app.py`) keep
working.  For production use ``create_app()`` with an explicit config.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend import spherex  # noqa: E402
from backend.api.admin import bp as admin_bp
from backend.api.anomaly import bp as anomaly_bp
from backend.api.jobs import bp as jobs_bp
from backend.api.overlays import bp as overlays_bp
from backend.api.public import bp as public_bp
from backend.api.tiles import bp as tiles_bp
from backend.api.targets import bp as targets_bp
from backend.config import WatsConfig, DEFAULT_RELEASE, SPHEREx_RELEASES

FRONTEND_DIR = BASE_DIR / "frontend" / "src"

FRAME_KEY_RE = __import__("re").compile(r"^[0-9a-f]{16}$")


def _json_error(message: str, status: int = 400, **extra):
    payload = {"status": "error", "error": message}
    payload.update(extra)
    return jsonify(payload), status


def _parse_params(args) -> dict:
    ra = spherex.parse_ra(args.get("ra"))
    dec = spherex.parse_dec(args.get("dec"))
    try:
        fov = float(args.get("fov", spherex.DEFAULT_FOV_DEG))
    except (TypeError, ValueError):
        raise ValueError("fov must be a number")
    if not math.isfinite(fov):
        raise ValueError("fov must be a finite number")
    fov = min(max(fov, 0.02), 2.0)
    try:
        max_frames = int(args.get("max_frames", spherex.DEFAULT_MAX_FRAMES))
    except (TypeError, ValueError):
        raise ValueError("max_frames must be an integer")
    max_frames = min(max(max_frames, 1), 200)
    try:
        naxis = int(args.get("naxis", spherex.DEFAULT_NAXIS))
    except (TypeError, ValueError):
        raise ValueError("naxis must be an integer")
    naxis = min(max(naxis, 128), 512)
    detector = (args.get("detector") or "").strip() or None
    if detector and detector.lower() not in ("all", "any", ""):
        det = detector.upper()
        if not det.startswith("D"):
            det = "D" + det
        if det not in {f"D{i}" for i in range(1, 7)}:
            raise ValueError("detector must be D1..D6 or 'all'")
        detector = det
    else:
        detector = None
    name = (args.get("name") or "custom").strip()[:80] or "custom"
    refresh = str(args.get("refresh", "")).lower() in ("1", "true", "yes", "on")
    release = (args.get("release") or DEFAULT_RELEASE).strip().lower()
    if release and release not in {r["key"] for r in SPHEREx_RELEASES}:
        release = DEFAULT_RELEASE
    return {
        "ra": ra, "dec": dec, "fov": fov, "max_frames": max_frames,
        "naxis": naxis, "detector": detector, "name": name, "refresh": refresh,
        "release": release,
    }


def create_app(config: WatsConfig | None = None) -> Flask:
    """Application factory.  Registers blueprints and returns the Flask app."""
    if config is None:
        config = WatsConfig.from_env()

    app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
    app.json.sort_keys = False
    app.config["WATS_CONFIG"] = config

    logging.basicConfig(
        level=logging.DEBUG if config.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger("wats.api")

    # ------------------------------------------------------------------
    # Job runner (SQLite-backed, resumable).  Replaces the old in-memory
    # FETCHES registry; kept attributes for backward-compatible imports.
    # ------------------------------------------------------------------
    from backend.jobs import JobRunner, STATE_DONE, STATE_ERROR, STATE_CANCELLED

    runner = JobRunner()
    app.extensions["wats_runner"] = runner
    app.extensions["wats_config"] = config

    def _status_for(key: str, params: dict) -> dict:
        # Find an existing job for this key, or fall back to manifest on disk.
        job = None
        for j in runner.list():
            if j.get("target_key") == key or (j.get("params") or {}).get("key") == key:
                job = j
                break
        manifest = spherex.load_manifest(key)
        if job is None:
            if manifest:
                return {"status": "ready", "key": key, "manifest": manifest}
            return {"status": "idle", "key": key}
        status_text = job["status"]
        if manifest and status_text not in ("queued", "running"):
            status_text = "ready"
        return {
            "status": status_text,
            "key": key,
            "job_id": job["id"],
            "stage": job.get("stage", ""),
            "progress": job.get("progress", 0),
            "total": job.get("total", 0),
            "message": job.get("message", ""),
            "error": job.get("error"),
            "manifest": manifest if status_text == "ready" else None,
        }

    def _run_fetch_job(job_id: int, params: dict) -> None:
        from backend.jobs import run_build_job
        run_build_job(runner, job_id, params)

    def _start_fetch(params: dict) -> dict:
        key = spherex.cache_key(params)
        # Reuse an existing running/queued job for this key.
        for j in runner.list():
            pj = j.get("params") or {}
            if pj.get("key") == key and j["status"] in ("queued", "running"):
                return j
        job_id = runner.create(job_type="quick", params={**params, "key": key}, target_key=key)
        runner.run_threaded(
            job_id,
            lambda progress: spherex.build_target(params, progress_cb=progress),
            total_hint=max(1, int(params.get("max_frames", 1))),
        )
        return runner.get(job_id)

    # ------------------------------------------------------------------
    # middleware
    # ------------------------------------------------------------------
    @app.after_request
    def _cors(resp):
        resp.headers.setdefault("Access-Control-Allow-Origin", "*")
        resp.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        resp.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp

    # ------------------------------------------------------------------
    # blueprints
    # ------------------------------------------------------------------
    app.register_blueprint(targets_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(overlays_bp)
    app.register_blueprint(tiles_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(anomaly_bp)

    # Serve Vite dist in production, fall back to the unbundled ES modules.
    dist_dir = BASE_DIR / "frontend" / "dist"
    if dist_dir.is_dir():
        app.static_folder = str(dist_dir)
        app.static_url_path = ""

    # ------------------------------------------------------------------
    # legacy routes (kept as thin wrappers around the registry)
    # ------------------------------------------------------------------
    @app.get("/")
    def index():
        # Prefer dist/index.html if built, otherwise frontend/index.html.
        if dist_dir.is_dir():
            return send_from_directory(str(dist_dir), "index.html")
        return send_from_directory(str(FRONTEND_DIR), "index.html")

    @app.get("/api/frames")
    def frames():
        try:
            params = _parse_params(request.args)
        except ValueError as exc:
            return _json_error(str(exc), 400)
        key = spherex.cache_key(params)
        manifest = spherex.load_manifest(key)
        if manifest and not params["refresh"]:
            return jsonify({"status": "ready", "key": key, **manifest})
        status = _status_for(key, params)
        if status.get("status") == "running":
            return jsonify({
                "status": "fetching", "key": key,
                "stage": status.get("stage"),
                "progress": status.get("progress", 0),
                "total": status.get("total", 1),
                "message": status.get("message", "Fetching from IRSA ..."),
            }), 202
        if manifest and status.get("status") == "error":
            return jsonify({"status": "ready", "key": key, **manifest})
        status = _start_fetch(params)
        return jsonify({
            "status": "fetching", "key": key,
            "stage": status.get("stage", "queued"),
            "progress": 0, "total": 1,
            "message": status.get("message", "Starting fetch ..."),
        }), 202

    @app.get("/api/status")
    def status():
        key = (request.args.get("key") or "").strip()
        if not key:
            try:
                params = _parse_params(request.args)
            except ValueError as exc:
                return _json_error(str(exc), 400)
            key = spherex.cache_key(params)
        status = _status_for(key, {})
        return jsonify(status)

    @app.get("/api/cancel")
    def cancel():
        key = (request.args.get("key") or "").strip()
        if not key:
            try:
                params = _parse_params(request.args)
            except ValueError as exc:
                return _json_error(str(exc), 400)
            key = spherex.cache_key(params)
        cancelled = False
        for j in runner.list():
            pj = j.get("params") or {}
            if pj.get("key") == key and j["status"] in ("queued", "running"):
                runner.cancel(j["id"])
                cancelled = True
        return jsonify({"status": "cancelled" if cancelled else "not_found", "key": key,
                        "message": "Fetch cancelled." if cancelled else "No running fetch for this key."})

    @app.get("/api/spectrum")
    def spectrum():
        try:
            params = _parse_params(request.args)
        except ValueError as exc:
            return _json_error(str(exc), 400)
        key = spherex.cache_key(params)
        if not spherex.load_manifest(key):
            return jsonify({"status": "pending", "key": key, "message": "Frames are not ready yet."}), 202
        try:
            return jsonify(spherex.extract_spectrum({**params, "key": key}))
        except Exception as exc:
            logger.error("Spectrum failed: %s", exc)
            return _json_error(f"Spectrum could not be extracted: {exc}", 500)

    @app.get("/frames/<key>/<path:filename>")
    def serve_frame(key, filename):
        if not FRAME_KEY_RE.fullmatch(key):
            abort(404)
        base = spherex.target_dir(key).resolve()
        full = (base / filename).resolve()
        if base not in full.parents and full != base:
            abort(404)
        if not full.is_file():
            abort(404)
        resp = send_from_directory(base, filename)
        # Baked preset frames are immutable; dynamic frames are re-validating.
        # must-revalidate keeps "Refresh data" working for all targets.
        resp.headers["Cache-Control"] = "public, max-age=86400, must-revalidate"
        return resp

    @app.errorhandler(404)
    def not_found(exc):
        return _json_error("Not found", 404)

    @app.errorhandler(500)
    def server_error(exc):
        logger.error("Unhandled error: %s", exc)
        if request.path.startswith("/api/"):
            return _json_error("Internal server error", 500)
        return "Internal server error", 500

    # expose for backward-compatible imports
    app._wats_runner = runner
    app._wats_start_fetch = _start_fetch
    app._wats_run_fetch_job = _run_fetch_job
    app._wats_status_for = _status_for
    app._wats_parse_params = _parse_params
    # Legacy shims (jobs_bp imported these; kept for import compatibility).
    app._wats_fetches = {}
    app._wats_fetch_lock = threading.Lock()

    return app


# Module-level app for `python3 backend/app.py` and `from backend.app import app`.
app = create_app()

if __name__ == "__main__":
    try:
        port = int(os.environ.get("PORT", "5000"))
    except ValueError:
        port = 5000
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
