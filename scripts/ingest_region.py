#!/usr/bin/env python3
"""Project WATS — targeted bulk ingest for a region.

Cone-searches IRSA TAP for all SPHEREx MEFs covering a circular region and
ingests their metadata into the local ``observations`` index (and optionally
warms the on-disk cutout cache).  Intended for selected regions, not the whole
survey (TB-scale).  Run as a batch job, not on the free host.

Examples
--------
    python3 scripts/ingest_region.py --ra 133.795096 --dec -7.245147 --radius 1.0
    python3 scripts/ingest_region.py --ra 08h55m10s --dec -7d14m42s --radius 0.5 --release dr1
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.config import DB_PATH  # noqa: E402
from backend.repo import _connect, upsert_observation  # noqa: E402
from backend.spherex.query import tap_query, WatError, parse_observation  # noqa: E402


def _cone_query(ra: float, dec: float, radius_deg: float, release: str | None) -> str:
    where = f"CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.6f})) = 1"
    if release:
        where += f" AND release = '{release}'"
    return f"SELECT * FROM spherex.plane WHERE {where} ORDER BY obs_id, detector"


def ingest_region(ra: float, dec: float, radius_deg: float, release: str | None, warm_cache: bool):
    query = _cone_query(ra, dec, radius_deg, release)
    print(f"[ingest] cone ra={ra:.4f} dec={dec:.4f} radius={radius_deg:.2f}° release={release or 'any'}")
    try:
        rows = tap_query(query)
    except WatError as exc:
        print(f"[ingest] TAP error: {exc}", flush=True)
        return 0
    print(f"[ingest] {len(rows)} MEFs found")
    conn = _connect(DB_PATH)
    inserted = 0
    try:
        for i, row in enumerate(rows):
            obs = parse_observation(row.get("uri") or row.get("access_url") or row.get("url") or "")
            fields = {
                "obs_id": obs["obs_id"] or str(row.get("obs_id") or ""),
                "obs_base": obs["obs_base"] or str(row.get("obs_base") or ""),
                "dither": int(row.get("dither") or obs["dither"] or 0),
                "detector": int(row.get("detector") or obs["detector"] or 0),
                "mjd": float(row.get("mjd")) if row.get("mjd") not in (None, "") else None,
                "healpix": int(row.get("healpix")) if row.get("healpix") not in (None, "") else 0,
                "footprint_wkt": row.get("footprint") or "",
                "exptime": float(row.get("exptime")) if row.get("exptime") not in (None, "") else 0.0,
                "quality": row.get("quality") or "",
                "uri": row.get("uri") or row.get("access_url") or row.get("url") or "",
                "release": row.get("release") or obs["release"] or "",
                "version": row.get("version") or obs["version"] or "",
            }
            if not fields["obs_id"]:
                continue
            try:
                upsert_observation(conn, fields)
                inserted += 1
            except Exception as exc:
                print(f"[ingest] upsert failed {fields['obs_id']}: {exc}", flush=True)
            if (i + 1) % 50 == 0:
                conn.commit()
                print(f"[ingest] ingested {i + 1}/{len(rows)}…", flush=True)
                time.sleep(0.1)
        conn.commit()
    finally:
        conn.close()
    print(f"[ingest] done. inserted/updated {inserted} observations.")
    return inserted


def main(argv=None):
    parser = argparse.ArgumentParser(description="Targeted bulk ingest of SPHEREx observations for a region")
    parser.add_argument("--ra", required=True, help="RA (decimal degrees or 08h55m10s)")
    parser.add_argument("--dec", required=True, help="Dec (decimal degrees or -7d14m42s)")
    parser.add_argument("--radius", type=float, default=1.0, help="search radius in degrees (default 1.0)")
    parser.add_argument("--release", default=None, help="data release filter (dr1/qr3/...)")
    parser.add_argument("--warm-cache", action="store_true", help="also warm the cutout cache (slow, not recommended)")
    args = parser.parse_args(argv)

    from backend.spherex.query import parse_ra, parse_dec
    ra = parse_ra(args.ra)
    dec = parse_dec(args.dec)
    ingest_region(ra, dec, args.radius, args.release, args.warm_cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
