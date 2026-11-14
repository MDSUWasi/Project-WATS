"""Public API blueprint (no accounts required).

Endpoints:
  GET    /api/public/activity          recent job history (activity feed)
  GET    /api/public/targets/<key>/annotations  annotations for a target
  POST   /api/public/targets/<key>/annotations  add an annotation
  DELETE /api/public/annotations/<id>           delete an annotation
  GET    /api/public/collections       list saved collections
  POST   /api/public/collections       create a collection
  GET    /api/public/collections/<id>  get a collection
  DELETE /api/public/collections/<id>  delete a collection
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from backend.repo import (
    _connect,
    delete_annotation,
    delete_collection,
    delete_watchlist,
    get_collection,
    get_watchlist,
    list_annotations,
    list_collections,
    list_jobs,
    list_watchlist,
    upsert_annotation,
    upsert_collection,
    upsert_watchlist,
    update_watchlist_check,
)

bp = Blueprint("public", __name__, url_prefix="/api/public")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# activity feed (public job history)
# --------------------------------------------------------------------------


@bp.get("/activity")
def activity():
    limit = min(int(request.args.get("limit", "50")), 200)
    conn = _connect()
    try:
        rows = list_jobs(conn)[:limit]
    finally:
        conn.close()
    return jsonify({"status": "ready", "jobs": rows, "n": len(rows)})


# --------------------------------------------------------------------------
# annotations
# --------------------------------------------------------------------------


@bp.get("/targets/<key>/annotations")
def get_annotations(key):
    conn = _connect()
    try:
        rows = list_annotations(conn, key)
    finally:
        conn.close()
    return jsonify({"status": "ready", "key": key, "annotations": rows, "n": len(rows)})


@bp.post("/targets/<key>/annotations")
def add_annotation(key):
    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"status": "error", "error": "body is required"}), 400
    author = (data.get("author") or "anonymous").strip()[:64]
    now = _now()
    conn = _connect()
    try:
        aid = upsert_annotation(conn, {
            "target_key": key,
            "author": author,
            "body": body,
            "created": now,
            "updated": now,
        })
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ready", "id": aid, "key": key}), 201


@bp.delete("/annotations/<int:annotation_id>")
def delete_annotation_endpoint(annotation_id):
    conn = _connect()
    try:
        delete_annotation(conn, annotation_id)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ready", "deleted": annotation_id})


# --------------------------------------------------------------------------
# collections
# --------------------------------------------------------------------------


@bp.get("/collections")
def list_collections_endpoint():
    conn = _connect()
    try:
        rows = list_collections(conn)
    finally:
        conn.close()
    # Parse target_keys JSON for the client.
    for r in rows:
        try:
            r["target_keys"] = json.loads(r["target_keys"] or "[]")
        except (ValueError, TypeError):
            r["target_keys"] = []
    return jsonify({"status": "ready", "collections": rows, "n": len(rows)})


@bp.post("/collections")
def create_collection():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "Untitled collection").strip()[:128]
    target_keys = data.get("target_keys") or []
    if not isinstance(target_keys, list):
        return jsonify({"status": "error", "error": "target_keys must be a list"}), 400
    now = _now()
    conn = _connect()
    try:
        cid = upsert_collection(conn, {
            "name": name,
            "target_keys": json.dumps(target_keys),
            "created": now,
            "updated": now,
        })
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ready", "id": cid, "name": name, "target_keys": target_keys}), 201


@bp.get("/collections/<int:collection_id>")
def get_collection_endpoint(collection_id):
    conn = _connect()
    try:
        row = get_collection(conn, collection_id)
    finally:
        conn.close()
    if not row:
        return jsonify({"status": "error", "error": "collection not found"}), 404
    try:
        row["target_keys"] = json.loads(row["target_keys"] or "[]")
    except (ValueError, TypeError):
        row["target_keys"] = []
    return jsonify({"status": "ready", "collection": row})


@bp.delete("/collections/<int:collection_id>")
def delete_collection_endpoint(collection_id):
    conn = _connect()
    try:
        delete_collection(conn, collection_id)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ready", "deleted": collection_id})


# --------------------------------------------------------------------------
# watchlist (saved coordinates; alert when new SPHEREx data appears)
# --------------------------------------------------------------------------


@bp.get("/watchlist")
def list_watchlist_endpoint():
    conn = _connect()
    try:
        rows = list_watchlist(conn)
    finally:
        conn.close()
    return jsonify({"status": "ready", "watchlist": rows, "n": len(rows)})


@bp.post("/watchlist")
def add_watchlist():
    data = request.get_json(silent=True) or {}
    try:
        ra = float(data.get("ra"))
        dec = float(data.get("dec"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "error": "ra/dec must be numbers"}), 400
    fov = float(data.get("fov", 0.15))
    name = (data.get("name") or "custom").strip()[:80]
    release = (data.get("release") or "dr1").strip().lower()
    max_frames = int(data.get("max_frames", 6))
    now = _now()
    conn = _connect()
    try:
        wid = upsert_watchlist(conn, {
            "ra": ra, "dec": dec, "fov": fov, "name": name, "release": release,
            "max_frames": max_frames, "created": now, "updated": now,
        })
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ready", "id": wid}), 201


@bp.get("/watchlist/<int:watch_id>/check")
def check_watchlist(watch_id):
    """Check a saved coordinate for new SPHEREx visits since last check.

    Returns the number of new visits found (0 if none).  This is the alert
    poll endpoint; the frontend can poll it periodically.
    """
    conn = _connect()
    try:
        row = get_watchlist(conn, watch_id)
    finally:
        conn.close()
    if not row:
        return jsonify({"status": "error", "error": "watchlist entry not found"}), 404
    # Query IRSA for the latest visits covering this coordinate.
    try:
        from backend import spherex
        from backend.config import CACHE_DIR
        # Use a fresh (non-cached) query to detect new data.
        rows = spherex.query_spherex(
            float(row["ra"]), float(row["dec"]),
            max_rows=5000, refresh=True, release=row.get("release"),
        )
        visits = {}
        for r in rows:
            vid = r.get("obs_id") or r.get("obs_base") or r.get("uri")
            visits[vid] = r
        max_mjd = max((r.get("mjd", 0) for r in rows), default=None)
        last_visit = row.get("last_visit_mjd")
        new_visits = 0
        if last_visit is not None:
            new_visits = sum(1 for r in rows if (r.get("mjd") or 0) > last_visit)
        else:
            new_visits = len(visits)
        conn2 = _connect()
        try:
            update_watchlist_check(conn2, watch_id, _now(), max_mjd)
            conn2.commit()
        finally:
            conn2.close()
        return jsonify({
            "status": "ready",
            "watch_id": watch_id,
            "ra": row["ra"], "dec": row["dec"],
            "new_visits": new_visits,
            "total_visits": len(visits),
            "max_mjd": max_mjd,
            "alert": new_visits > 0,
        })
    except Exception as exc:
        return jsonify({"status": "error", "error": f"Check failed: {exc}"}), 500


@bp.delete("/watchlist/<int:watch_id>")
def delete_watchlist_endpoint(watch_id):
    conn = _connect()
    try:
        delete_watchlist(conn, watch_id)
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ready", "deleted": watch_id})
