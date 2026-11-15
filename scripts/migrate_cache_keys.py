"""Migrate stale manifest cache keys to the current cache_key() scheme.

The release-aware cache key was added in commit b0510fe.  Manifests built
before that change were written under keys that omit the release term, so the
frontend can no longer find them and the pipeline re-queries IRSA instead of
reusing cached frames.

This script rewrites each manifest's ``key`` field and moves the directory to
the location the current code expects.  It is idempotent and safe to run while
the server is stopped.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.config import TARGET_DIR
from backend.spherex.pipeline import cache_key, load_manifest, save_manifest


def migrate_one(old_dir: Path) -> bool:
    manifest_path = old_dir / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(manifest, dict):
        return False

    old_key = manifest.get("key") or old_dir.name
    if old_key != old_dir.name:
        # Already migrated / moved previously.
        return False

    # Reconstruct the params the manifest was built from.
    params = {
        "ra": float(manifest["ra"]),
        "dec": float(manifest["dec"]),
        "fov": float(manifest.get("fov_deg", 0.15)),
        "max_frames": int(manifest.get("max_frames", 6)),
        "detector": manifest.get("detector_filter") or None,
        "release": manifest.get("data_release_key") or "",
        "naxis": int(manifest.get("naxis", 320)),
        "name": manifest.get("name") or "custom",
    }
    new_key = cache_key(params)
    if new_key == old_key:
        return False

    new_dir = TARGET_DIR / new_key
    if new_dir.exists():
        # A newer manifest already exists at the target key; drop the stale one.
        shutil.rmtree(old_dir, ignore_errors=True)
        return True

    # Rewrite the manifest key and every per-frame *_url field so they point
    # at the new directory, then move the directory.
    manifest["key"] = new_key
    for frame in manifest.get("frames") or []:
        for field in ("gray_url", "color_url", "gray_sub_url", "color_sub_url",
                      "band_urls", "band_sub_urls"):
            value = frame.get(field)
            if isinstance(value, str) and value.startswith("/frames/"):
                frame[field] = f"/frames/{new_key}/{value.rsplit('/', 1)[-1]}"
            elif isinstance(value, dict):
                frame[field] = {
                    d: (f"/frames/{new_key}/{u.rsplit('/', 1)[-1]}" if isinstance(u, str) and u.startswith("/frames/") else u)
                    for d, u in value.items()
                }
    save_manifest(new_key, manifest)
    new_dir.mkdir(parents=True, exist_ok=True)
    for entry in old_dir.iterdir():
        if entry.name == "manifest.json":
            continue
        target = new_dir / entry.name
        if not target.exists():
            shutil.move(str(entry), str(target))
    shutil.rmtree(old_dir, ignore_errors=True)
    print(f"migrated {old_key} -> {new_key}  ({manifest.get('name')})")
    return True


def main() -> int:
    if not TARGET_DIR.is_dir():
        print(f"target dir missing: {TARGET_DIR}")
        return 1
    migrated = 0
    for entry in sorted(TARGET_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if migrate_one(entry):
            migrated += 1
    print(f"migrated {migrated} stale manifest(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())