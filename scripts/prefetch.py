#!/usr/bin/env python3
"""Project WATS prefetch tool.

Pre-fetches the five preset targets completely so they are served from cache
on the free tier.  Also provides cache stats and eviction.

Examples
--------
    python3 scripts/prefetch.py --stats
    python3 scripts/prefetch.py --evict
    python3 scripts/prefetch.py --all --max-frames 12
    python3 scripts/prefetch.py --preset WISE0855 --max-frames 6
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend import spherex  # noqa: E402
from backend import config as _config  # noqa: E402
from backend.cache import Cache  # noqa: E402
from backend.config import WatsConfig  # noqa: E402
from backend.repo import _connect, upsert_target, get_target  # noqa: E402


def _progress(stage, done, total, message):
    print(f"[{stage:>6}] {done:>3}/{total:<3}  {message}", flush=True)


def prefetch_preset(preset, max_frames, force=False):
    key = spherex.cache_key({
        "ra": preset["ra"], "dec": preset["dec"], "fov": preset["fov"],
        "max_frames": max_frames, "naxis": 320, "detector": None, "name": preset["name"],
    })
    manifest = spherex.load_manifest(key)
    if manifest and not force:
        print(f"  {preset['name']}: already cached (key={key})")
        return key, manifest
    print(f"  {preset['name']}: fetching {max_frames} frames ...")
    manifest = spherex.build_target(
        {"ra": preset["ra"], "dec": preset["dec"], "fov": preset["fov"],
         "max_frames": max_frames, "naxis": 320, "detector": None,
         "name": preset["name"], "refresh": force},
        progress_cb=_progress,
        force=force,
    )
    # Copy rendered frames + manifest to the committed preset directory so
    # they survive deploys on hosts with ephemeral storage.
    import shutil
    preset_dir = Path(_config.PRESET_DIR) / key
    runtime_dir = Path(_config.TARGET_DIR) / key
    preset_dir.mkdir(parents=True, exist_ok=True)
    if runtime_dir.is_dir():
        for item in runtime_dir.iterdir():
            dst = preset_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
    # Record the target in the DB.
    conn = _connect()
    try:
        upsert_target(conn, key, {
            "key": key, "name": preset["name"], "ra": preset["ra"], "dec": preset["dec"],
            "fov": preset["fov"], "naxis": 320, "detector_filter": "all",
            "max_frames": max_frames, "data_release": manifest.get("data_release", ""),
            "created": manifest.get("created", ""), "updated": manifest.get("updated", ""),
            "completeness_json": None,
        })
        conn.commit()
    finally:
        conn.close()
    print(f"  {preset['name']}: done ({len(manifest.get('frames', []))} frames)")
    return key, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pre-fetch SPHEREx presets for Project WATS")
    parser.add_argument("--stats", action="store_true", help="show cache statistics and exit")
    parser.add_argument("--evict", action="store_true", help="evict unpinned cutouts to budget and exit")
    parser.add_argument("--all", action="store_true", help="prefetch all five presets")
    parser.add_argument("--preset", default=None, help="preset key to prefetch (wise0855, barnard, epsindib, rwaur, rdra)")
    parser.add_argument("--max-frames", type=int, default=6, help="max frames per preset (default 6)")
    parser.add_argument("--force", action="store_true", help="re-fetch even if cached")
    args = parser.parse_args(argv)

    config = WatsConfig.from_env()
    cache = Cache(config)

    if args.stats:
        conn = _connect()
        try:
            from backend.repo import cache_stats
            stats = cache_stats(conn)
        finally:
            conn.close()
        print(f"Profile: {config.profile}")
        print(f"Cache budget: {config.cache_budget_mb} MB")
        print(f"Cache entries: {stats['entries']}")
        print(f"Cache size: {stats['total_mb']} MB ({stats['total_bytes']} bytes)")
        print(f"Pinned entries: {stats['pinned']}")
        return 0

    if args.evict:
        evicted = cache.evict_to_budget(config.cache_budget_mb)
        print(f"Evicted {evicted} unpinned cache entries.")
        return 0

    presets = {p["key"]: p for p in spherex.PRESETS}
    if args.preset:
        p = presets.get(args.preset.lower())
        if not p:
            print(f"Unknown preset: {args.preset}. Available: {', '.join(presets)}", file=sys.stderr)
            return 1
        prefetch_preset(p, args.max_frames, force=args.force)
    elif args.all:
        print(f"Prefetching {len(presets)} presets (max_frames={args.max_frames}) ...")
        for p in spherex.PRESETS:
            prefetch_preset(p, args.max_frames, force=args.force)
    else:
        parser.print_help()
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
