"""Job API blueprint.

Endpoints:
  POST   /api/targets/<key>/jobs        start a quick/complete/prefetch fetch job
  POST   /api/targets/jobs              compatibility alias for new targets
  GET    /api/jobs/<id>                 job status/progress/stage/cancel
  POST   /api/jobs/<id>/cancel          cancel a running job
  POST   /api/jobs/<id>/resume          resume a stale job from cache
  GET    /api/jobs                      list jobs (optionally filtered by target_key)
"""

from __future__ import annotations

import math

from flask import Blueprint, current_app, jsonify, request

from backend import spherex
from backend.config import DEFAULT_FOV_DEG, DEFAULT_MAX_FRAMES, DEFAULT_NAXIS
from backend.jobs import JobRunner, run_build_job

bp = Blueprint("jobs", __name__, url_prefix="/api")


def init_runner() -> JobRunner:
    runner = current_app.extensions.get("wats_runner")
    if runner is None:
        runner = JobRunner()
        current_app.extensions["wats_runner"] = runner
    return runner


def _parse_job_params(data: dict) -> tuple[dict | None, str | None]:
    try:
        ra = float(data.get("ra", request.args.get("ra", "")))
        dec = float(data.get("dec", request.args.get("dec", "")))
        fov = float(data.get("fov", request.args.get("fov", DEFAULT_FOV_DEG)))
        max_frames = int(data.get("max_frames", request.args.get("max_frames", DEFAULT_MAX_FRAMES)))
        naxis = int(data.get("naxis", request.args.get("naxis", DEFAULT_NAXIS)))
    except (TypeError, ValueError):
        return None, "ra/dec/fov must be numbers and max_frames/naxis must be integers"
    if not (math.isfinite(ra) and math.isfinite(dec)):
        return None, "ra and dec must be finite"
    if fov <= 0 or not math.isfinite(fov):
        return None, "fov must be a positive number"
    max_frames = max(1, min(int(max_frames), 200))
    naxis = max(128, min(int(naxis), 512))
    detector = (data.get("detector") or request.args.get("detector") or "").strip() or None
    if detector and detector.lower() not in ("all", "any"):
        detector = detector.upper()
        if not detector.startswith("D"):
            detector = "D" + detector
        if detector not in {f"D{i}" for i in range(1, 7)}:
            return None, "detector must be D1..D6 or 'all'"
    else:
        detector = None
    name = (data.get("name") or request.args.get("name") or "custom").strip()[:80] or "custom"
    refresh = bool(data.get("refresh", request.args.get("refresh", False)))
    if isinstance(refresh, str):
        refresh = refresh.lower() in ("1", "true", "yes", "on")
    key = spherex.cache_key({
        "ra": ra, "dec": dec, "fov": fov, "max_frames": max_frames,
        "naxis": naxis, "detector": detector,
    })
    params = {
        "ra": ra, "dec": dec, "fov": fov, "max_frames": max_frames,
        "naxis": naxis, "detector": detector, "name": name,
        "refresh": refresh, "key": key,
    }
    return params, None


@bp.post("/targets/jobs")
def start_job_alias():
    return start_job(None)


@bp.post("/targets/<key>/jobs")
def start_job(key):
    runner = init_runner()
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    job_type = data.get("type", "quick")
    if job_type not in ("quick", "complete", "prefetch"):
        return jsonify({"status": "error", "error": "type must be quick, complete, or prefetch"}), 400
    params, error = _parse_job_params(data)
    if params is None:
        return jsonify({"status": "error", "error": error}), 400
    target_key = params["key"]
    job_id = runner.create(job_type=job_type, params=params, target_key=target_key)
    runner.run_threaded(
        job_id,
        lambda progress: spherex.build_target(params, progress_cb=progress),
        total_hint=params["max_frames"],
    )
    return jsonify({"status": "accepted", "job": runner.get(job_id)}), 202


@bp.get("/jobs/<int:job_id>")
def job_status(job_id):
    job = init_runner().get(job_id)
    if job is None:
        return jsonify({"status": "error", "error": "job not found"}), 404
    return jsonify({"status": "ready", "job": job})


@bp.post("/jobs/<int:job_id>/cancel")
def job_cancel(job_id):
    runner = init_runner()
    if not runner.cancel(job_id):
        job = runner.get(job_id)
        if job is None:
            return jsonify({"status": "error", "error": "job not found"}), 404
        return jsonify({"status": "error", "error": "job cannot be cancelled"}), 400
    return jsonify({"status": "cancelled", "job": runner.get(job_id)})


@bp.post("/jobs/<int:job_id>/resume")
def job_resume(job_id):
    return _resume_job(job_id)


@bp.post("/jobs/<int:job_id>")
def job_resume_alias(job_id):
    return _resume_job(job_id)


def _resume_job(job_id):
    runner = init_runner()
    job = runner.get(job_id)
    if job is None:
        return jsonify({"status": "error", "error": "job not found"}), 404
    if job["status"] not in ("stale", "error", "cancelled"):
        return jsonify({"status": "error", "error": "job cannot be resumed"}), 400
    params = dict(job.get("params") or {})
    if not params:
        return jsonify({"status": "error", "error": "job has no resumable parameters"}), 400
    if job.get("target_key"):
        params["key"] = job["target_key"]
    params["refresh"] = False
    params["cancel"] = False
    runner._set(
        job_id,
        status="queued",
        stage="queued",
        progress=0,
        message="Resuming from cache",
        cancel=False,
        clear_error=True,
    )
    runner.run_threaded(
        job_id,
        lambda progress: spherex.build_target(params, progress_cb=progress),
        total_hint=max(1, int(params.get("max_frames", 1))),
    )
    return jsonify({"status": "accepted", "job": runner.get(job_id)}), 202


@bp.get("/jobs")
def job_list():
    runner = init_runner()
    target_key = request.args.get("target_key")
    jobs = runner.list(target_key)
    return jsonify({"status": "ready", "jobs": jobs, "n_jobs": len(jobs)})