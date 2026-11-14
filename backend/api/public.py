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
    get_collection,
    list_annotations,
    list_collections,
    list_jobs,
    upsert_annotation,
    upsert_collection,
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
