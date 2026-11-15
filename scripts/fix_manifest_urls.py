"""Rewrite per-frame *_url fields that still reference the pre-migration key.

``scripts/migrate_cache_keys.py`` moves the target directory and rewrites the
manifest's top-level ``key`` field, but the per-frame ``gray_url`` /
``color_url`` / ``band_urls`` strings were left pointing at the old key.  The
browser therefore requests images from a directory that no longer exists.

This script scans every manifest under ``data/targets`` and rewrites any
``*_url`` value whose key prefix does not match the manifest's own key.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.config import TARGET_DIR
from backend.spherex.pipeline import save_manifest

URL_FIELDS = ("gray_url", "color_url", "gray_sub_url", "color_sub_url")


def _rewrite(url: str, key: str) -> str:
    """Normalise a frame URL to ``/frames/<key>/<basename>``.

    Manifests built before the release-aware cache key stored URLs of the form
    ``/frames/<oldkey>/<file>``.  When the cache key changes the directory is
    renamed, so every per-frame URL must be rewritten to point at the new key.

    Earlier repair attempts could leave a doubled prefix
    (``/frames/<newkey>/<oldkey>/<file>``); this normaliser collapses any number
    of ``/frames/<segment>`` prefixes down to exactly ``/frames/<key>/<file>``,
    where ``<file>`` is the last path segment (e.g. ``000_gray.png``).
    """
    if not isinstance(url, str) or not url.startswith("/frames/"):
        return url
    # Drop everything before the final filename component.
    stripped = url.rstrip("/")
    filename = stripped.rsplit("/", 1)[-1]
    if not filename:
        return url
    return f"/frames/{key}/{filename}"


def fix_manifest(path: Path) -> bool:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(manifest, dict):
        return False
    key = manifest.get("key")
    if not key:
        return False

    changed = False
    for frame in manifest.get("frames") or []:
        for field in URL_FIELDS:
            new_url = _rewrite(frame.get(field), key)
            if new_url != frame.get(field):
                frame[field] = new_url
                changed = True
        for field in ("band_urls", "band_sub_urls"):
            value = frame.get(field)
            if isinstance(value, dict):
                for det, url in list(value.items()):
                    new_url = _rewrite(url, key)
                    if new_url != url:
                        value[det] = new_url
                        changed = True
    if changed:
        save_manifest(key, manifest)
        print(f"rewrote urls in {key} ({manifest.get('name')})")
    return changed


def main() -> int:
    fixed = 0
    for entry in sorted(TARGET_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if fix_manifest(entry / "manifest.json"):
            fixed += 1
    print(f"fixed {fixed} manifest(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())