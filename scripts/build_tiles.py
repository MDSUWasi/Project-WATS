#!/usr/bin/env python3
"""Project WATS — build whole-sky / region tile pyramid.

Builds coverage tiles (observation count per HEALPix cell) as PNG images under
``data/tiles/{nside}/{healpix}.png`` plus an ``index.json`` manifest.  These
tiles are served by the ``/tiles/<z>/<x>/<y>`` route when present; otherwise the
app falls back to on-demand cutouts.  Tile generation is batch/self-hosted only
and is NOT enabled on the free host.

Examples
--------
    python3 scripts/build_tiles.py --nside 64 --release dr1
    python3 scripts/build_tiles.py --nside 64 --healpix 298374
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.config import DB_PATH, TILES_DIR  # noqa: E402
from backend.repo import _connect, get_observation_count, get_observations_by_healpix  # noqa: E402

import numpy as np
from PIL import Image, ImageDraw


def _nside_valid(nside: int) -> bool:
    return nside > 0 and (nside & (nside - 1)) == 0


def _tile_path(tiles_dir: Path, nside: int, healpix: int) -> Path:
    return tiles_dir / str(nside) / f"{healpix}.png"


def _render_coverage_tile(counts: dict[int, int], nside: int, size: int = 256) -> Image.Image:
    """Render a single tile showing observation counts for healpix cells that
    overlap the tile's sky region.  Here each tile corresponds to one healpix
    cell (level = nside), drawn as a color-coded count."""
    img = Image.new("RGB", (size, size), (8, 13, 19))
    draw = ImageDraw.Draw(img)
    total = sum(counts.values())
    if total == 0:
        return img
    maxc = max(counts.values()) or 1
    # Draw concentric rings whose radius/color encode the count.
    cx, cy = size // 2, size // 2
    for i, (hp, c) in enumerate(sorted(counts.items(), key=lambda kv: -kv[1])):
        frac = c / maxc
        r = int(6 + 90 * frac)
        # color from blue (low) to red (high)
        col = (int(255 * frac), int(120 * (1 - frac)), 180)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=3)
        if i >= 6:
            break
    draw.text((8, 8), f"n={total} obs", fill=(220, 238, 255))
    return img


def build_tiles(nside: int, release: str | None, single_healpix: int | None):
    if not _nside_valid(nside):
        raise ValueError(f"nside must be a power of two, got {nside}")
    npix = 12 * nside * nside
    cells = [single_healpix] if single_healpix is not None else list(range(npix))

    TILES_DIR.mkdir(parents=True, exist_ok=True)
    (TILES_DIR / str(nside)).mkdir(parents=True, exist_ok=True)

    conn = _connect(DB_PATH)
    total_tiles = 0
    try:
        for cell in cells:
            rows = get_observations_by_healpix(conn, cell)
            counts = {cell: len(rows)}
            if not rows:
                # still write an empty tile so the pyramid is complete
                img = Image.new("RGB", (256, 256), (8, 13, 19))
            else:
                img = _render_coverage_tile(counts, nside)
            out = _tile_path(TILES_DIR, nside, cell)
            img.save(out)
            total_tiles += 1
            if total_tiles % 50 == 0:
                print(f"[tiles] wrote {total_tiles} tiles…", flush=True)
            time.sleep(0.01)
    finally:
        conn.close()

    # Write index.json manifest.
    manifest = {
        "nside": nside,
        "npix": npix,
        "tile_size": 256,
        "format": "png",
        "scheme": "healpix_nested",
        "release": release,
        "built": datetime.now(timezone.utc).isoformat(),
        "tiles_written": total_tiles,
        "url_pattern": "/tiles/{nside}/{healpix}.png",
    }
    manifest_path = TILES_DIR / str(nside) / "index.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"[tiles] done. wrote {total_tiles} tiles to {TILES_DIR / str(nside)}")
    print(f"[tiles] manifest: {manifest_path}")
    return total_tiles


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build whole-sky coverage tiles")
    parser.add_argument("--nside", type=int, default=64, help="HEALPix nside (power of two; default 64)")
    parser.add_argument("--release", default=None, help="data release filter (not yet applied to tiles)")
    parser.add_argument("--healpix", type=int, default=None, help="build a single healpix cell tile")
    args = parser.parse_args(argv)
    build_tiles(args.nside, args.release, args.healpix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
