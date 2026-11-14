#!/usr/bin/env python3
"""Project WATS web API.

Public, read-only web service for the SPHEREx blink comparator.  The API is
deliberately small and JSON-only so the tool can be embedded in other sites.

Endpoints
---------
GET /api/presets                     curated test targets
GET /api/frames?ra=&dec=&fov=...     manifest + frame URLs (starts a fetch if needed)
GET /api/status?key=...              progress for a running fetch
GET /api/spectrum?ra=&dec=&fov=...   on-the-fly spectrum at the target position
GET /api/overlays?ra=&dec=&fov=...   Gaia DR3 + CatWISE2020 sources
GET /api/health                      service metadata
GET /frames/<key>/<file>             rendered PNG frames

Run:
    source .venv/bin/activate
    python3 backend/app.py
"""

from __future__ import annotations

import logging
import math
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend import spherex

FRONTEND_DIR = BASE_DIR / "frontend"

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
# Flask 3 removed the JSON_SORT_KEYS config key; this is the supported switch.
app.json.sort_keys = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("wats.api")

# In-process fetch registry: key -> status dict.  A file lock is not needed
# because a single Flask process owns the registry; the CLI is separate.
FETCHES: dict[str, dict] = {}
FETCH_LOCK = threading.Lock()
FRAME_KEY_RE = re.compile(r"^[0-9a-f]{16}$")

PRESETS = [
    {
        "name": "WISE 0855",
        "ra": 133.795096,
        "dec": -7.245147,
        "ra_text": "08h55m10.831s",
        "dec_text": "-7d14m42.53s",
        "fov": 0.15,
        "description": "Fast-moving Y brown dwarf (about 8 arcsec/yr).",
    },
    {
        "name": "Barnard's Star",
        "ra": 269.4521,
        "dec": 4.6933,
        "ra_text": "17h57m48.5s",
        "dec_text": "+04d41m36s",
        "fov": 0.15,
        "description": "Nearby red dwarf with the largest known proper motion.",
    },
    {
        "name": "Epsilon Indi B",
        "ra": 330.59025,
        "dec": -56.7833,
        "ra_text": "22h03m21.7s",
        "dec_text": "-56d47m00s",
        "fov": 0.15,
        "description": "Pair of brown dwarfs orbiting the nearby star Epsilon Indi.",
    },
    {
        "name": "RW Aurigae",
        "ra": 76.95625,
        "dec": 30.6747,
        "ra_text": "05h07m49.5s",
        "dec_text": "+30d40m29s",
        "fov": 0.20,
        "description": "Young eruptive variable star in a binary system.",
    },
    {
        "name": "R Draconis",
        "ra": 269.4321,
        "dec": 57.4,
        "ra_text": "17h57m43.7s",
        "dec_text": "+57d24m00s",
        "fov": 0.20,
        "description": "Mira-type variable star with a ~424 day period.",
    },
]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


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
    return {
        "ra": ra,
        "dec": dec,
        "fov": fov,
        "max_frames": max_frames,
        "naxis": naxis,
        "detector": detector,
        "name": name,
        "refresh": refresh,
    }


def _status_for(key: str, params: dict) -> dict:
    with FETCH_LOCK:
        status = dict(FETCHES.get(key) or {"status": "idle", "key": key})
    manifest = spherex.load_manifest(key)
    running = status.get("status") in ("running", "queued")
    if manifest and not running:
        status["status"] = "ready"
        status["key"] = key
        status["manifest"] = manifest
        return status
    return status


def _run_fetch(key: str, params: dict, status: dict) -> None:
    def progress(stage, done, total, message):
        if status.get("cancel"):
            raise RuntimeError("Fetch cancelled")
        status.update(
            {
                "status": "running",
                "stage": stage,
                "progress": int(done),
                "total": int(total),
                "message": message,
                "updated": time.time(),
            }
        )

    try:
        logger.info("Starting fetch %s params=%s", key, params)
        manifest = spherex.build_target(params, progress_cb=progress)
        status.update(
            {
                "status": "ready",
                "stage": "done",
                "progress": len(manifest.get("frames", [])),
                "total": len(manifest.get("frames", [])),
                "message": f"Ready: {len(manifest.get('frames', []))} frames.",
                "updated": time.time(),
            }
        )
    except Exception as exc:
        if status.get("cancel"):
            status.update(
                {
                    "status": "cancelled",
                    "stage": "cancelled",
                    "message": "Cancelled",
                    "updated": time.time(),
                }
            )
            return
        logger.error("Fetch %s failed: %s\n%s", key, exc, traceback.format_exc())
        status.update(
            {
                "status": "error",
                "stage": "error",
                "message": str(exc),
                "error": str(exc),
                "updated": time.time(),
            }
        )


def _start_fetch(params: dict) -> dict:
    key = spherex.cache_key(params)
    with FETCH_LOCK:
        current = FETCHES.get(key)
        if current and current.get("status") == "running":
            return current
        status = {
            "status": "running",
            "key": key,
            "stage": "queued",
            "progress": 0,
            "total": 1,
            "message": "Starting fetch from IRSA ...",
            "started": time.time(),
            "updated": time.time(),
        }
        FETCHES[key] = status
    thread = threading.Thread(target=_run_fetch, args=(key, params, status), daemon=True, name=f"wats-fetch-{key}")
    thread.start()
    return status


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.after_request
def _cors(resp):
    resp.headers.setdefault("Access-Control-Allow-Origin", "*")
    resp.headers.setdefault("Access-Control-Allow-Methods", "GET, OPTIONS")
    resp.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")
    # API payloads (presets, manifests, spectra) must never be cached by the
    # browser: a stale presets response can hide newly added targets.
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    payload = spherex.health()
    payload["fetches"] = len(FETCHES)
    return jsonify(payload)


@app.get("/api/presets")
def presets():
    return jsonify({"status": "ready", "presets": PRESETS})


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
        return jsonify({"status": "fetching", "key": key, "stage": status.get("stage"), "progress": status.get("progress", 0), "total": status.get("total", 1), "message": status.get("message", "Fetching from IRSA ...")}), 202
    if manifest and status.get("status") == "error":
        # A previous error is not fatal if we already have frames on disk.
        return jsonify({"status": "ready", "key": key, **manifest})
    status = _start_fetch(params)
    return jsonify({"status": "fetching", "key": key, "stage": status.get("stage", "queued"), "progress": 0, "total": 1, "message": status.get("message", "Starting fetch ...")}), 202


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
    with FETCH_LOCK:
        status = FETCHES.get(key)
        if status:
            status["cancel"] = True
            status["status"] = "cancelled"
            status["stage"] = "cancelled"
            status["message"] = "Cancelled"
            status["updated"] = time.time()
    return jsonify({"status": "cancelled", "key": key, "message": "Fetch cancelled."})


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
        return jsonify(spherex.extract_spectrum(params))
    except Exception as exc:
        logger.error("Spectrum failed: %s", exc)
        return _json_error(f"Spectrum could not be extracted: {exc}", 500)


@app.get("/api/overlays")
def overlays():
    try:
        ra = spherex.parse_ra(request.args.get("ra"))
        dec = spherex.parse_dec(request.args.get("dec"))
        fov = float(request.args.get("fov", spherex.DEFAULT_FOV_DEG))
        naxis = int(request.args.get("naxis", spherex.DEFAULT_NAXIS))
    except (TypeError, ValueError) as exc:
        return _json_error(str(exc), 400)
    if not math.isfinite(fov):
        return _json_error("fov must be a finite number", 400)
    fov = min(max(fov, 0.02), 2.0)
    naxis = min(max(naxis, 128), 512)
    try:
        return jsonify(spherex.fetch_overlays(ra, dec, fov, naxis))
    except Exception as exc:
        logger.error("Overlays failed: %s", exc)
        return _json_error(f"Overlay catalogues are unavailable: {exc}", 502)


@app.get("/frames/<key>/<path:filename>")
def serve_frame(key, filename):
    # Keys are 16-char sha1 prefixes.  Reject anything else before touching
    # the filesystem so a crafted key (e.g. "..") cannot escape TARGET_DIR.
    if not FRAME_KEY_RE.fullmatch(key):
        abort(404)
    base = (spherex.TARGET_DIR / key).resolve()
    full = (base / filename).resolve()
    if base not in full.parents and full != base:
        abort(404)
    if not full.is_file():
        abort(404)
    resp = send_from_directory(base, filename)
    # Frames for a key can be overwritten by a refresh; revalidate instead of
    # letting the browser serve month-old PNGs from its HTTP cache.
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@app.errorhandler(404)
def not_found(exc):
    # Do not answer unknown paths with index.html and HTTP 200: missing assets
    # and missing frame PNGs must be real 404s.
    return _json_error("Not found", 404)


@app.errorhandler(500)
def server_error(exc):
    logger.error("Unhandled error: %s", exc)
    if request.path.startswith("/api/"):
        return _json_error("Internal server error", 500)
    return "Internal server error", 500


if __name__ == "__main__":
    try:
        port = int(os.environ.get("PORT", "5000"))
    except ValueError:
        port = 5000
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)