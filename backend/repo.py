"""SQLite repositories + migrations for the WATS data model.

All repository methods are synchronous and use a single connection per
call (SQLite in WAL mode handles concurrent readers fine).  The database
lives at ``data/wats.db`` by default.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend import config as _config
from backend.config import DB_PATH, DATA_DIR
from backend.migrations import MIGRATIONS


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(db_path: Path | None = None) -> Path:
    """Resolve the configured database at call time.

    ``DB_PATH`` is deliberately read when a connection is opened.  Tests and
    deployments can therefore redirect the database without reloading every
    module that imported this repository.
    """
    return Path(db_path) if db_path is not None else Path(_config.DB_PATH)


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    db_path = _db_path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Run all pending migrations and create the schema if missing."""
    db_path = _db_path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            r["version"] for r in conn.execute("SELECT version FROM schema_migrations")
        }
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, now_iso()),
            )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# targets
# --------------------------------------------------------------------------


def _q(name: str) -> str:
    return f'"{name}"'


def upsert_target(conn: sqlite3.Connection, key: str, fields: dict) -> None:
    cols = ", ".join(_q(c) for c in fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    values = list(fields.values())
    conn.execute(
        f"""
        INSERT INTO targets ({cols})
        VALUES ({placeholders})
        ON CONFLICT(key) DO UPDATE SET
            {", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in fields if c != "key")}
        """,
        values,
    )


def get_target(conn: sqlite3.Connection, key: str) -> dict | None:
    row = conn.execute("SELECT * FROM targets WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def list_targets(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM targets ORDER BY updated DESC").fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# visits
# --------------------------------------------------------------------------


def upsert_visit(conn: sqlite3.Connection, target_key: str, fields: dict) -> None:
    cols = ", ".join(_q(c) for c in fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    values = list(fields.values())
    conn.execute(
        f"""
        INSERT INTO visits ({cols})
        VALUES ({placeholders})
        ON CONFLICT(target_key, obs_id, detector) DO UPDATE SET
            {", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in fields if c not in ("target_key", "obs_id", "detector"))}
        """,
        values,
    )


def list_visits(conn: sqlite3.Connection, target_key: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM visits WHERE target_key = ? ORDER BY mjd", (target_key,)
    ).fetchall()
    return [dict(r) for r in rows]


def visit_count(conn: sqlite3.Connection, target_key: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM visits WHERE target_key = ?", (target_key,)
    ).fetchone()
    return int(row["n"]) if row else 0


# --------------------------------------------------------------------------
# frames
# --------------------------------------------------------------------------


def upsert_frame(conn: sqlite3.Connection, target_key: str, fields: dict) -> None:
    cols = ", ".join(_q(c) for c in fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    values = list(fields.values())
    conn.execute(
        f"""
        INSERT INTO frames ({cols})
        VALUES ({placeholders})
        ON CONFLICT(target_key, "index") DO UPDATE SET
            {", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in fields if c not in ("target_key", "index"))}
        """,
        values,
    )


def list_frames(conn: sqlite3.Connection, target_key: str) -> list[dict]:
    rows = conn.execute(
        'SELECT * FROM frames WHERE target_key = ? ORDER BY "index"', (target_key,)
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# spectra
# --------------------------------------------------------------------------


def upsert_spectrum(conn: sqlite3.Connection, target_key: str, fields: dict) -> None:
    cols = ", ".join(_q(c) for c in fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    values = list(fields.values())
    conn.execute(
        f"""
        INSERT INTO spectra ({cols})
        VALUES ({placeholders})
        ON CONFLICT(target_key, frame_index, detector) DO UPDATE SET
            {", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in fields if c not in ("target_key", "frame_index", "detector"))}
        """,
        values,
    )


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------


def upsert_source(conn: sqlite3.Connection, target_key: str, fields: dict) -> None:
    cols = ", ".join(_q(c) for c in fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    values = list(fields.values())
    conn.execute(
        f"""
        INSERT INTO sources ({cols})
        VALUES ({placeholders})
        ON CONFLICT(id) DO UPDATE SET
            {", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in fields if c != "id")}
        """,
        values,
    )


# --------------------------------------------------------------------------
# overlays
# --------------------------------------------------------------------------


def upsert_overlay(conn: sqlite3.Connection, target_key: str, fields: dict) -> None:
    cols = ", ".join(_q(c) for c in fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    values = list(fields.values())
    conn.execute(
        f"""
        INSERT INTO overlays ({cols})
        VALUES ({placeholders})
        ON CONFLICT(id) DO UPDATE SET
            {", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in fields if c != "id")}
        """,
        values,
    )


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------


def create_job(
    conn: sqlite3.Connection,
    target_key: str | None,
    job_type: str,
    params: dict | None,
) -> int:
    now = now_iso()
    cur = conn.execute(
        """
        INSERT INTO jobs (target_key, type, status, stage, progress, total,
                          message, cancel, params_json, error, created, updated)
        VALUES (?, ?, 'queued', '', 0, 0, '', 0, ?, NULL, ?, ?)
        """,
        (target_key, job_type, json.dumps(params) if params else None, now, now),
    )
    return int(cur.lastrowid)


def update_job(
    conn: sqlite3.Connection,
    job_id: int,
    status: str | None = None,
    stage: str | None = None,
    progress: int | None = None,
    total: int | None = None,
    message: str | None = None,
    cancel: int | None = None,
    error: str | None = None,
    clear_error: bool = False,
) -> None:
    sets: list[str] = []
    vals: list[Any] = []
    if status is not None:
        sets.append("status = ?")
        vals.append(status)
    if stage is not None:
        sets.append("stage = ?")
        vals.append(stage)
    if progress is not None:
        sets.append("progress = ?")
        vals.append(progress)
    if total is not None:
        sets.append("total = ?")
        vals.append(total)
    if message is not None:
        sets.append("message = ?")
        vals.append(message)
    if cancel is not None:
        sets.append("cancel = ?")
        vals.append(cancel)
    if error is not None:
        sets.append("error = ?")
        vals.append(error)
    if clear_error:
        sets.append("error = NULL")
    if not sets:
        return
    sets.append("updated = ?")
    vals.append(now_iso())
    vals.append(job_id)
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", vals)


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(conn: sqlite3.Connection, target_key: str | None = None) -> list[dict]:
    if target_key is not None:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE target_key = ? ORDER BY created DESC",
            (target_key,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created DESC").fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------


def upsert_coverage(conn: sqlite3.Connection, target_key: str, fields: dict) -> None:
    cols = ", ".join(_q(c) for c in fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    values = list(fields.values())
    conn.execute(
        f"""
        INSERT INTO coverage ({cols})
        VALUES ({placeholders})
        ON CONFLICT(target_key) DO UPDATE SET
            {", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in fields if c != "target_key")}
        """,
        values,
    )


def get_coverage(conn: sqlite3.Connection, target_key: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM coverage WHERE target_key = ?", (target_key,)
    ).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# cache_entries
# --------------------------------------------------------------------------


def upsert_cache_entry(conn: sqlite3.Connection, fields: dict) -> None:
    cols = ", ".join(_q(c) for c in fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    values = list(fields.values())
    conn.execute(
        f"""
        INSERT INTO cache_entries ({cols})
        VALUES ({placeholders})
        ON CONFLICT(path) DO UPDATE SET
            {", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in fields if c != "path")}
        """,
        values,
    )


def list_cache_entries(
    conn: sqlite3.Connection, target_key: str | None = None, unpinned_only: bool = False
) -> list[dict]:
    if target_key is not None and unpinned_only:
        rows = conn.execute(
            "SELECT * FROM cache_entries WHERE target_key = ? AND pin = 0 ORDER BY last_used ASC",
            (target_key,),
        ).fetchall()
    elif target_key is not None:
        rows = conn.execute(
            "SELECT * FROM cache_entries WHERE target_key = ? ORDER BY last_used ASC",
            (target_key,),
        ).fetchall()
    elif unpinned_only:
        rows = conn.execute(
            "SELECT * FROM cache_entries WHERE pin = 0 ORDER BY last_used ASC"
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cache_entries ORDER BY last_used ASC").fetchall()
    return [dict(r) for r in rows]


def cache_stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(bytes), 0) AS total_bytes FROM cache_entries"
    ).fetchone()
    pinned = conn.execute(
        "SELECT COUNT(*) AS n FROM cache_entries WHERE pin = 1"
    ).fetchone()
    return {
        "entries": int(row["n"]) if row else 0,
        "total_bytes": int(row["total_bytes"]) if row else 0,
        "total_mb": round((int(row["total_bytes"]) if row else 0) / (1024 * 1024), 2),
        "pinned": int(pinned["n"]) if pinned else 0,
    }


# --------------------------------------------------------------------------
# observations (global index)
# --------------------------------------------------------------------------


def upsert_observation(conn: sqlite3.Connection, fields: dict) -> None:
    cols = ", ".join(_q(c) for c in fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    values = list(fields.values())
    conn.execute(
        f"""
        INSERT INTO observations ({cols})
        VALUES ({placeholders})
        ON CONFLICT(obs_id) DO UPDATE SET
            {", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in fields if c != "obs_id")}
        """,
        values,
    )


def get_observation_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM observations").fetchone()
    return int(row["n"]) if row else 0


def get_observations_by_healpix(conn: sqlite3.Connection, healpix: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM observations WHERE healpix = ? ORDER BY obs_id, detector", (healpix,)
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# annotations (public, keyed by target_key; no accounts)
# --------------------------------------------------------------------------


def upsert_annotation(conn: sqlite3.Connection, fields: dict) -> int:
    cols = ", ".join(_q(c) for c in fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    values = list(fields.values())
    if "id" in fields:
        conn.execute(
            f"""
            INSERT INTO annotations ({cols})
            VALUES ({placeholders})
            ON CONFLICT(id) DO UPDATE SET
                {", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in fields if c != "id")}
            """,
            values,
        )
        return int(fields["id"])
    cur = conn.execute(
        f"""
        INSERT INTO annotations ({cols})
        VALUES ({placeholders})
        """,
        values,
    )
    return int(cur.lastrowid)


def list_annotations(conn: sqlite3.Connection, target_key: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM annotations WHERE target_key = ? ORDER BY created DESC",
        (target_key,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_annotation(conn: sqlite3.Connection, annotation_id: int) -> None:
    conn.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))


# --------------------------------------------------------------------------
# collections (public, shareable; no accounts)
# --------------------------------------------------------------------------


def upsert_collection(conn: sqlite3.Connection, fields: dict) -> int:
    cols = ", ".join(_q(c) for c in fields.keys())
    placeholders = ", ".join("?" for _ in fields)
    values = list(fields.values())
    if "id" in fields:
        conn.execute(
            f"""
            INSERT INTO collections ({cols})
            VALUES ({placeholders})
            ON CONFLICT(id) DO UPDATE SET
                {", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in fields if c != "id")}
            """,
            values,
        )
        return int(fields["id"])
    cur = conn.execute(
        f"""
        INSERT INTO collections ({cols})
        VALUES ({placeholders})
        """,
        values,
    )
    return int(cur.lastrowid)


def list_collections(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM collections ORDER BY updated DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_collection(conn: sqlite3.Connection, collection_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM collections WHERE id = ?", (collection_id,)
    ).fetchone()
    return dict(row) if row else None


def delete_collection(conn: sqlite3.Connection, collection_id: int) -> None:
    conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))


