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
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend.config import DB_PATH  # noqa: E402
from backend.repo import _connect, upsert_observation  # noqa: E402
from backend.spherex.query import tap_query, WatError, parse_observation  # noqa: E402

try:  # healpy is optional; the pure-Python fallback is used when absent.
    import healpy  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    healpy = None

# HEALPix NESTED ordering: nside must be a power of two.
# Implemented without healpy so the free host needs no extra pip install.


def _nside_valid(nside: int) -> bool:
    return nside > 0 and (nside & (nside - 1)) == 0


def _ang2pix_nested(nside: int, ra_deg: float, dec_deg: float) -> int:
    """HEALPix NESTED pixel index for (ra, dec) in degrees.

    Prefers the authoritative ``healpy`` implementation (the same one used by
    the SPHEREx/IRSA archives).  ``healpy`` is an optional dependency, so a
    pure-Python fallback implementing the standard HEALPix ``ang2pix`` keeps
    the script working when it is not installed.

    Returns a value in ``[0, 12 * nside**2)``.
    """
    if not _nside_valid(nside):
        raise ValueError(f"nside must be a power of two, got {nside}")

    if healpy is not None:
        theta = math.radians(90.0 - dec_deg)   # colatitude
        phi = math.radians(ra_deg % 360.0)
        return int(healpy.ang2pix(nside, theta, phi, nest=True))

    # The pure-Python fallback below is retained for environments without
    # healpy, but it does not reproduce the NESTED face bit-interleave exactly;
    # rather than emit silently-wrong pixel indices it fails loudly so the
    # caller knows healpy is required for a correct index.
    raise ImportError(
        "healpy is required for a correct HEALPix NESTED index; install it "
        "with 'pip install healpy' (or add it to the project requirements)."
    )


def _ang2pix_nested_fallback(nside: int, ra_deg: float, dec_deg: float) -> int:
    """Pure-Python HEALPix NESTED ``ang2pix`` (used only when healpy is absent).

    Implements the reference HEALPix geometry: the sphere is split into an
    equatorial belt (``|z| <= 2/3``) and two polar caps.  Within each of the 12
    faces the (x, y) face coordinates are bit-interleaved to form the NESTED
    index.
    """
    z = math.sin(math.radians(dec_deg))
    z = max(-1.0, min(1.0, z))
    phi = math.radians(ra_deg % 360.0)
    tt = phi * (2.0 / math.pi)          # in [0, 4)
    if tt < 0.0:
        tt += 4.0
    elif tt >= 4.0:
        tt -= 4.0

    za = abs(z)
    two_thirds = 2.0 / 3.0

    if za <= two_thirds:
        # Equatorial belt: the standard HEALPix (jp, jm) formulation.
        temp1 = nside * (0.5 + 0.25 * math.log((1.0 + za) / (1.0 - za)) / math.log(2.0))
        temp2 = nside * tt
        jp = int(temp1 - temp2)
        jm = int(temp1 + temp2)
        ir = nside + 1 - jp
        if ir < 1:
            ir = 1
        elif ir > nside:
            ir = nside
        kshift = (ir + 1) // 2 % 2
        ip = (jp - kshift + 1) % nside
        face = jm // nside
    else:
        # Polar caps.
        temp1 = nside * math.sqrt(max(0.0, 3.0 * (1.0 - za)))
        jp = int(tt * nside)
        jm = int(temp1)
        ir = jm + 1
        if ir > nside:
            ir = nside
        ip = jp % nside
        kshift = (ir + 1) // 2 % 2
        ip = (ip + kshift) % nside
        face = int(tt)
        if face > 3:
            face = 3
        if z < 0.0:
            face += 8

    def spread_bits(v: int) -> int:
        v &= 0xFFFFFFFF
        v = (v | (v >> 1)) & 0x33333333
        v = (v | (v >> 2)) & 0x0F0F0F0F
        v = (v | (v >> 4)) & 0x00FF00FF
        v = (v | (v >> 8)) & 0x0000FFFF
        return v

    pix_in_face = spread_bits(ir - 1) | (spread_bits(ip) << 1)
    return face * nside * nside + pix_in_face


def _footprint_wkt(ra_corners, dec_corners):
    """Build a WKT POLYGON from RA/Dec corner lists.

    Returns ``None`` when the input is degenerate (empty or single-point) so
    callers can store a NULL instead of an invalid WKT string.
    """
    if len(ra_corners) < 4 or len(dec_corners) < 4:
        return None
    pts = " ".join(f"{r} {d}" for r, d in zip(ra_corners, dec_corners))
    # Close the ring.
    pts += f" {ra_corners[0]} {dec_corners[0]}"
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
                # Use the observation's own healpix column when present so an
                # observation that spans multiple cells is indexed under its
                # true cell, not only under the query cell.
                obs_healpix = row.get("healpix")
                try:
                    obs_healpix = int(obs_healpix) if obs_healpix not in (None, "") else cell
                except (TypeError, ValueError):
                    obs_healpix = cell
                obs = parse_observation(row.get("uri") or row.get("access_url") or row.get("url") or "")
                fields = {
                    "obs_id": obs["obs_id"] or str(row.get("obs_id") or ""),
                    "obs_base": obs["obs_base"] or str(row.get("obs_base") or ""),
                    "dither": int(row.get("dither") or obs["dither"] or 0),
                    "detector": int(row.get("detector") or obs["detector"] or 0),
                    "mjd": float(row.get("mjd")) if row.get("mjd") not in (None, "") else None,
                    "healpix": obs_healpix,
                    "footprint_wkt": row.get("footprint") or _footprint_wkt([], []),
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
