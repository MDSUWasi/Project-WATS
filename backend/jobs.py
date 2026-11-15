"""Resumable job runner with cancel flags, stale detection, and pipeline hooks.

Job states: queued -> running -> done/error/cancelled/stale.
A job can be cancelled by setting cancel=1; the pipeline checks the flag
between cutouts and in progress callbacks.  If the worker dies (process
restart), a running job is marked stale; a new request resumes it from
cached cutouts.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from backend import config as _config
from backend.repo import _connect, create_job, get_job, list_jobs, update_job, upsert_target

STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"
STATE_CANCELLED = "cancelled"
STATE_STALE = "stale"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_params(value) -> dict | None:
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


class JobRunner:
    """In-process job registry plus SQLite persistence.

    The registry mirrors the DB for fast polling; the DB is the source of
    truth across worker restarts.  Each runner owns its registry so two
    application instances with different databases cannot observe each
    other's in-memory jobs.
    """

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path is not None else Path(_config.DB_PATH)
        self._jobs: dict[int, dict] = {}
        self._guard = threading.Lock()
        self._daemon_threads: dict[int, threading.Thread] = {}
        # On startup, mark any jobs left in queued/running state from a
        # previous process as stale or cancelled so they don't hang forever.
        self._reap_orphaned_on_startup()

    def _reap_orphaned_on_startup(self) -> None:
        """Mark queued/running jobs from a previous process as cancelled.

        A job that is still ``queued`` after a restart has no worker to
        pick it up, and a ``running`` job has no live thread.  Both must be
        resolved so the activity feed and job list don't show phantom work.
        """
        conn = _connect(self.db_path)
        try:
            try:
                rows = conn.execute(
                    "SELECT id, status FROM jobs WHERE status IN ('queued', 'running')"
                ).fetchall()
            except Exception:
                # Tables not yet created (fresh DB, migrations not run).
                # Nothing to reap — init_db() will create them later.
                return
            now = now_iso()
            for row in rows:
                conn.execute(
                    "UPDATE jobs SET status='cancelled', stage='cancelled', "
                    "message='Cancelled (orphaned on restart)', cancel=1, updated=? "
                    "WHERE id=?",
                    (now, int(row["id"])),
                )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # creation / lookup
    # ------------------------------------------------------------------
    def create(
        self,
        job_type: str,
        params: dict | None = None,
        target_key: str | None = None,
    ) -> int:
        conn = _connect(self.db_path)
        try:
            if target_key and params:
                now = now_iso()
                upsert_target(conn, target_key, {
                    "key": target_key,
                    "name": str(params.get("name") or "custom")[:80] or "custom",
                    "ra": float(params["ra"]),
                    "dec": float(params["dec"]),
                    "fov": float(params.get("fov", 0.15)),
                    "naxis": int(params.get("naxis", 320)),
                    "detector_filter": str(params.get("detector") or "all"),
                    "max_frames": int(params.get("max_frames", 6)),
                    "created": now,
                    "updated": now,
                })
            job_id = create_job(conn, target_key, job_type, params)
            conn.commit()
        finally:
            conn.close()
        with self._guard:
            self._jobs[job_id] = {
                "id": job_id,
                "target_key": target_key,
                "type": job_type,
                "status": STATE_QUEUED,
                "stage": "",
                "progress": 0,
                "total": 0,
                "message": "",
                "cancel": False,
                "params": params,
                "error": None,
                "created": now_iso(),
                "updated": now_iso(),
            }
        return job_id

    @staticmethod
    def _from_row(row) -> dict:
        return {
            "id": row["id"],
            "target_key": row["target_key"],
            "type": row["type"],
            "status": row["status"],
            "stage": row["stage"],
            "progress": row["progress"],
            "total": row["total"],
            "message": row["message"],
            "cancel": bool(row["cancel"]),
            "params": _decode_params(row["params_json"]),
            "error": row["error"],
            "created": row["created"],
            "updated": row["updated"],
        }

    def get(self, job_id: int) -> dict | None:
        with self._guard:
            job = self._jobs.get(job_id)
        if job is not None:
            return dict(job)
        conn = _connect(self.db_path)
        try:
            row = get_job(conn, job_id)
        finally:
            conn.close()
        return self._from_row(row) if row else None

    def list(self, target_key: str | None = None) -> list[dict]:
        with self._guard:
            registry = [dict(j) for j in self._jobs.values()]
        conn = _connect(self.db_path)
        try:
            db_jobs = [self._from_row(row) for row in list_jobs(conn, target_key)]
        finally:
            conn.close()
        by_id = {j["id"]: j for j in registry}
        out = []
        for job in db_jobs:
            out.append(dict(by_id.get(job["id"], job)))
        out.sort(key=lambda j: j.get("created", ""), reverse=True)
        return out

    def find_active(self, target_key: str | None = None, job_type: str | None = None) -> dict | None:
        """Return the most-recently-created job for ``target_key`` that is still
        queued or running, optionally filtered by ``job_type``.

        Used by the API to deduplicate identical fetch requests so the UI
        cannot queue many duplicate jobs by clicking "Load" repeatedly.
        """
        with self._guard:
            candidates = [dict(j) for j in self._jobs.values() if j["status"] in (STATE_QUEUED, STATE_RUNNING)]
        if target_key:
            candidates = [j for j in candidates if j.get("target_key") == target_key]
        if job_type:
            candidates = [j for j in candidates if j.get("type") == job_type]
        if not candidates:
            return None
        candidates.sort(key=lambda j: j.get("created", ""), reverse=True)
        return candidates[0]

    # ------------------------------------------------------------------
    # state transitions
    # ------------------------------------------------------------------
    def _set(
        self,
        job_id: int,
        status: Optional[str] = None,
        stage: Optional[str] = None,
        progress: Optional[int] = None,
        total: Optional[int] = None,
        message: Optional[str] = None,
        cancel: Optional[bool] = None,
        error: Optional[str] = None,
        clear_error: bool = False,
    ) -> None:
        conn = _connect(self.db_path)
        try:
            update_job(
                conn,
                job_id,
                status=status,
                stage=stage,
                progress=progress,
                total=total,
                message=message,
                cancel=1 if cancel is True else (0 if cancel is False else None),
                error=error,
                clear_error=clear_error,
            )
            conn.commit()
        finally:
            conn.close()
        with self._guard:
            job = self._jobs.get(job_id)
            if job is not None:
                if status is not None:
                    job["status"] = status
                if stage is not None:
                    job["stage"] = stage
                if progress is not None:
                    job["progress"] = progress
                if total is not None:
                    job["total"] = total
                if message is not None:
                    job["message"] = message
                if cancel is not None:
                    job["cancel"] = cancel
                if clear_error:
                    job["error"] = None
                elif error is not None:
                    job["error"] = error
                job["updated"] = now_iso()

    def mark_running(self, job_id: int) -> None:
        self._set(job_id, status=STATE_RUNNING, stage="running", message="Job started")

    def mark_done(self, job_id: int, progress: int, total: int, message: str = "") -> None:
        self._set(
            job_id,
            status=STATE_DONE,
            stage="done",
            progress=progress,
            total=total,
            message=message,
            clear_error=True,
        )

    def mark_error(self, job_id: int, message: str, error: str) -> None:
        self._set(job_id, status=STATE_ERROR, stage="error", message=message, error=error)

    def mark_cancelled(self, job_id: int) -> None:
        self._set(
            job_id,
            status=STATE_CANCELLED,
            stage="cancelled",
            message="Cancelled",
            clear_error=True,
        )

    def mark_stale(self, job_id: int) -> None:
        self._set(
            job_id,
            status=STATE_STALE,
            stage="stale",
            message="Worker recycled; job is stale and can be resumed",
        )

    def cancel(self, job_id: int) -> bool:
        """Request cancellation.  Returns True if a job existed to cancel."""
        job = self.get(job_id)
        if job is None:
            return False
        if job["status"] in (STATE_DONE, STATE_ERROR, STATE_CANCELLED):
            return False
        self._set(job_id, cancel=True, status=STATE_CANCELLED, stage="cancelled", message="Cancelled")
        return True

    def is_cancelled(self, job_id: int) -> bool:
        job = self.get(job_id)
        return bool(job and job["cancel"])

    # ------------------------------------------------------------------
    # stale detection
    # ------------------------------------------------------------------
    def mark_stale_jobs(self, older_than_s: float = 3600) -> list[int]:
        """Mark running jobs that haven't been updated in a while as stale."""
        cutoff = time.time() - older_than_s
        stale_ids: list[int] = []
        conn = _connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id, updated FROM jobs WHERE status = 'running'"
            ).fetchall()
            for row in rows:
                try:
                    updated = datetime.fromisoformat(row["updated"]).timestamp()
                except Exception:
                    updated = 0
                if updated < cutoff:
                    self.mark_stale(int(row["id"]))
                    stale_ids.append(int(row["id"]))
        finally:
            conn.close()
        return stale_ids

    # ------------------------------------------------------------------
    # run a pipeline as a resumable job
    # ------------------------------------------------------------------
    def run(
        self,
        job_id: int,
        pipeline: Callable[[Callable[[str, int, int, str], None]], object],
        *,
        total_hint: int = 1,
    ) -> None:
        """Execute ``pipeline(progress_cb)`` as a resumable job.

        The callback is ``(stage, done, total, message)``.  Raising from the
        callback cancels the pipeline between stages.  If the pipeline returns
        a manifest-like dict, its frame count becomes the final progress.
        """
        self.mark_running(job_id)

        def progress(stage: str, done: int, total: int, message: str) -> None:
            if self.is_cancelled(job_id):
                raise RuntimeError("Fetch cancelled")
            self._set(
                job_id,
                stage=stage,
                progress=done,
                total=total,
                message=message,
            )

        try:
            result = pipeline(progress)
            if self.is_cancelled(job_id):
                self.mark_cancelled(job_id)
            else:
                total = total_hint
                if isinstance(result, dict) and result.get("frames") is not None:
                    total = max(1, len(result.get("frames") or []))
                self.mark_done(job_id, total, total, "Job complete")
        except RuntimeError as exc:
            if "cancelled" in str(exc).lower() or self.is_cancelled(job_id):
                self.mark_cancelled(job_id)
            else:
                self.mark_error(job_id, str(exc), str(exc))
        except Exception as exc:  # noqa: BLE001
            self.mark_error(job_id, str(exc), repr(exc))

    def run_threaded(
        self,
        job_id: int,
        pipeline: Callable[[Callable[[str, int, int, str], None]], object],
        *,
        total_hint: int = 1,
        daemon: bool = True,
    ) -> threading.Thread:
        """Run the pipeline in a background thread and return it."""
        thread = threading.Thread(
            target=self._run_and_cleanup,
            args=(job_id, pipeline),
            kwargs={"total_hint": total_hint},
            daemon=daemon,
            name=f"wats-job-{job_id}",
        )
        thread.start()
        self._daemon_threads[job_id] = thread
        return thread

    def _run_and_cleanup(self, job_id, pipeline, total_hint=1):
        """Run the pipeline then release the thread reference so the
        ``_daemon_threads`` dict does not grow without bound."""
        try:
            self.run(job_id, pipeline, total_hint=total_hint)
        finally:
            with self._guard:
                self._daemon_threads.pop(job_id, None)


def run_build_job(runner: JobRunner, job_id: int, params: dict) -> None:
    """Run the target-build pipeline as a resumable job."""
    from backend import spherex

    def pipeline(progress):
        return spherex.build_target(params, progress_cb=progress)

    runner.run(job_id, pipeline, total_hint=max(1, int(params.get("max_frames", 1))))