#!/usr/bin/env python3
"""Project WATS — build the global SPHEREx observation index.

Pages the IRSA TAP `spherex.plane`/`artifact` table into the local
``observations`` table, keyed by HEALPix cell so tile builders and coverage
queries can find all MEFs covering a region without a per-target TAP round trip.

Examples
--------
    python3 scripts/build_index.py --nside 64 --release dr1
    python3 scripts/build_index.py --nside 128 --max-rows 200000
    python3 scripts/build_index.py --healpix 298374  # single cell
"""

from __future__ import annotations

import argparse
import math
import re
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

# HEALPix NESTED ordering: nside must be a power of two.
# Implemented without healpy so the free host needs no extra pip install.


def _nside_valid(nside: int) -> bool:
    return nside > 0 and (nside & (nside - 1)) == 0


def _ang2pix_nested(nside: int, ra_deg: float, dec_deg: float) -> int:
    """HEALPix NESTED pixel index for (ra, dec) in degrees.

    Pure-Python implementation (no healpy).  Returns the NESTED index.
    """
    if not _nside_valid(nside):
        raise ValueError(f"nside must be a power of two, got {nside}")
    theta = math.radians(90.0 - dec_deg)  # colatitude
    phi = math.radians(ra_deg % 360.0)
    z = math.cos(theta)
    # Handle poles.
    if z > 1.0:
        z = 1.0
    if z < -1.0:
        z = -1.0
    # Equal-area projection onto the face of the enclosing cube (HEALPix scheme).
    nside2 = nside * nside
    npix = 12 * nside2
    # Determine face and position on face (HEALPix NESTED).
    # Use the standard HEALPix ang2pix logic for NESTED ordering.
    # Simplify: compute via the z/phi face logic.
    tt = theta
    pp = phi
    if pp < 0:
        pp += 2 * math.pi
    # cos(theta) in [-1,1]
    c1 = math.cos(tt)
    # Find which of the 4 faces around equator / polar caps.
    # This is the standard HEALPix vec2pix nested.
    # x = sin(theta)*cos(phi), y = sin(theta)*sin(phi), z = cos(theta)
    st = math.sin(tt)
    x = st * math.cos(pp)
    y = st * math.sin(pp)
    zz = c1
    # Face determination (Kogut et al. / HEALPix):
    jp = 0
    jm = 0
    if zz >= 0:
        if abs(x) >= abs(y):
            jp = 0 if x >= 0 else 2  # face 0 or 2
        else:
            jp = 1 if y >= 0 else 3  # face 1 or 3
        # position on face
        u = x / zz if zz != 0 else 0.0
        v = y / zz if zz != 0 else 0.0
        if jp == 0:
            uu = u
            vv = v
        elif jp == 1:
            uu = v
            vv = -u
        elif jp == 2:
            uu = -u
            vv = -v
        else:
            uu = -v
            vv = u
    else:
        # south polar cap: faces 4 (x>=y?) ...
        if abs(x) >= abs(y):
            jp = 4 if x >= 0 else 6
        else:
            jp = 5 if y >= 0 else 7
        u = x / -zz if zz != 0 else 0.0
        v = y / -zz if zz != 0 else 0.0
        if jp == 4:
            uu = u
            vv = v
        elif jp == 5:
            uu = v
            vv = -u
        elif jp == 6:
            uu = -u
            vv = -v
        else:
            uu = -v
            vv = u
    # Nested index: each face has nside^2 pixels in Z-order (NESTED).
    # Map (uu, vv) in [-1,1] to [0, nside) via the HEALPix nested scheme.
    # Use the standard nested pixel computation:
    # ij = floor((1 - uu)/2 * nside), etc., then interleave bits.
    # Simpler: compute the pixel via the standard HEALPix formula for NESTED.
    # We use the well-known nested encoding: bit-interleave the row/col.
    n = nside
    # Convert uu,vv in [-1,1] to integer row/col in [0, nside)
    uu = max(-1.0, min(1.0, uu))
    vv = max(-1.0, min(1.0, vv))
    col = int((uu + 1.0) / 2.0 * n)
    row = int((1.0 - vv) / 2.0 * n)  # y down
    col = max(0, min(n - 1, col))
    row = max(0, min(n - 1, row))
    # NESTED: interleave bits of row and col, then add face offset.
    def interleave(v: int) -> int:
        r = 0
        shift = 0
        while v:
            r |= (v & 1) << shift
            v >>= 1
            shift += 2
        return r
    pix_in_face = interleave(col) | (interleave(row) << 1)
    return jp * nside2 + pix_in_face


def _footprint_wkt(ra_corners, dec_corners):
    """Build a WKT POLYGON from RA/Dec corner lists."""
    pts = " ".join(f"{r} {d}" for r, d in zip(ra_corners, dec_corners))
    return f"POLYGON(({pts}))"


def _parse_healpix_columns(row: dict, nside: int):
    """Extract ra/dec from a TAP row; try common column names."""
    ra = None
    dec = None
    for k, v in row.items():
        kl = k.lower()
        if v is None or v == "":
            continue
        if ra is None and kl in ("ra", "ra_deg", "alpha", "raj2000"):
            try:
                ra = float(v)
            except ValueError:
                pass
        if dec is None and kl in ("dec", "dec_deg", "delta", "dej2000"):
            try:
                dec = float(v)
            except ValueError:
                pass
    if ra is None:
        # Try s_region / coverage polygon centroid parse.
        sreg = row.get("s_region") or row.get("coverage") or row.get("footprint")
        if sreg:
            m = re.findall(r"[-+]?\d+(?:\.\d+)?", str(sreg))
            if len(m) >= 2:
                try:
                    ra = float(m[0])
                    dec = float(m[1])
                except ValueError:
                    pass
    return ra, dec


def build_index(nside: int, max_rows: int | None, release: str | None, single_healpix: int | None):
    """Build (or extend) the observations index."""
    if not _nside_valid(nside):
        raise ValueError(f"nside must be a power of two, got {nside}")
    npix = 12 * nside * nside
    cells = [single_healpix] if single_healpix is not None else list(range(npix))

    conn = _connect(DB_PATH)
    inserted = 0
    try:
        for cell in cells:
            # Query TAP for this HEALPix cell.  spherex.plane has healpix col; use it.
            where = f"healpix = {cell}"
            if release:
                where += f" AND release = '{release}'"
            query = (
                f"SELECT * FROM spherex.plane WHERE {where} "
                f"ORDER BY obs_id, detector"
            )
            if max_rows:
                query += f" LIMIT {max_rows}"
            try:
                rows = tap_query(query)
            except WatError as exc:
                print(f"[index] healpix={cell}: TAP error: {exc}", flush=True)
                continue
            for row in rows:
                ra, dec = _parse_healpix_columns(row, nside)
                obs = parse_observation(row.get("uri") or row.get("access_url") or row.get("url") or "")
                fields = {
                    "obs_id": obs["obs_id"] or str(row.get("obs_id") or ""),
                    "obs_base": obs["obs_base"] or str(row.get("obs_base") or ""),
                    "dither": int(row.get("dither") or obs["dither"] or 0),
                    "detector": int(row.get("detector") or obs["detector"] or 0),
                    "mjd": float(row.get("mjd")) if row.get("mjd") not in (None, "") else None,
                    "healpix": cell,
                    "footprint_wkt": _footprint_wkt([], []) if ra is None else row.get("footprint") or _footprint_wkt([ra], [dec]),
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
                    print(f"[index] upsert failed for {fields['obs_id']}: {exc}", flush=True)
            conn.commit()
            print(f"[index] healpix={cell}: {len(rows)} rows (total {inserted})", flush=True)
            # Be polite to IRSA between cells.
            time.sleep(0.2)
    finally:
        conn.close()
    print(f"[index] done. inserted/updated {inserted} observations across {len(cells)} healpix cell(s).")
    return inserted


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the global SPHEREx observation index")
    parser.add_argument("--nside", type=int, default=64, help="HEALPix nside (power of two; default 64)")
    parser.add_argument("--max-rows", type=int, default=None, help="max rows per healpix cell (debug)")
    parser.add_argument("--release", default=None, help="data release filter (dr1/qr3/...)")
    parser.add_argument("--healpix", type=int, default=None, help="build a single healpix cell instead of all")
    args = parser.parse_args(argv)
    build_index(args.nside, args.max_rows, args.release, args.healpix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
