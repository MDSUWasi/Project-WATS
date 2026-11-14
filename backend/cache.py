"""Shared cutout cache with atomic writes, per-key locks, LRU eviction, pinning."""

from __future__ import annotations

import datetime
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from backend import config as _config
from backend.config import WatsConfig


class Cache:
    """Thread-safe on-disk cache for SPHEREx cutouts and derived products.

    - Atomic writes via temp file + os.replace
    - Per-target-key advisory locks (fcntl.flock) so only one fetch runs
      per key at a time
    - LRU eviction of unpinned entries when the budget is exceeded
    - Pinned entries are never evicted
    """

    def __init__(self, config: WatsConfig, cache_dir: Path | None = None):
        self.config = config
        self.dir = Path(cache_dir) if cache_dir is not None else Path(_config.CACHE_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ------------------------------------------------------------------
    # per-key locking
    # ------------------------------------------------------------------
    def _key_lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def with_key_lock(self, key: str, fn: Callable[[], object]) -> object:
        """Run ``fn`` while holding the in-process lock for ``key``."""
        with self._key_lock(key):
            return fn()

    # ------------------------------------------------------------------
    # cutout cache
    # ------------------------------------------------------------------
    def cutout_path(self, url_hash: str) -> Path:
        return self.dir / f"cutout_{url_hash}.fits"

    def store_cutout(self, path: Path, data: bytes) -> None:
        """Atomically write ``data`` to ``path``."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def record_cache_entry(
        self,
        path: str | Path,
        bytes_len: int,
        target_key: Optional[str] = None,
        pin: bool = False,
        data_release: Optional[str] = None,
    ) -> None:
        """Touch / insert a cache_entries row so LRU knows what to evict.

        ``last_used`` is refreshed on every call, which is what makes the LRU
        ordering meaningful.  ``bytes_len`` is re-stated from disk when the
        file exists so the recorded size cannot drift from reality.
        """
        from backend.repo import _connect, upsert_cache_entry

        p = Path(path)
        try:
            size = int(p.stat().st_size) if p.exists() else int(bytes_len)
        except OSError:
            size = int(bytes_len)
        conn = _connect()
        try:
            upsert_cache_entry(
                conn,
                {
                    "path": str(path),
                    "bytes": size,
                    "last_used": time.time(),
                    "pin": 1 if pin else 0,
                    "target_key": target_key,
                    "data_release": data_release,
                    "created": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                },
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # LRU eviction
    # ------------------------------------------------------------------
    def evict_to_budget(self, budget_mb: Optional[int] = None) -> int:
        """Evict least-recently-used unpinned cutouts until under budget.

        Returns the number of entries evicted.
        """
        budget_mb = budget_mb or self.config.cache_budget_mb
        budget_bytes = budget_mb * 1024 * 1024

        from backend.repo import _connect, list_cache_entries

        conn = _connect()
        try:
            entries = list_cache_entries(conn, unpinned_only=True)
            total = sum(int(e["bytes"]) for e in entries)
            evicted = 0
            for e in entries:
                if total <= budget_bytes:
                    break
                p = Path(e["path"])
                if p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        pass
                try:
                    conn.execute("DELETE FROM cache_entries WHERE path = ?", (e["path"],))
                    total -= int(e["bytes"])
                    evicted += 1
                except sqlite3.Error:
                    pass
            conn.commit()
            return evicted
        finally:
            conn.close()
