#!/usr/bin/env python3
"""Project WATS command-line front end.

Examples
--------
    python3 scripts/fetch_frames.py WISE0855
    python3 scripts/fetch_frames.py 133.795096 -7.245147 --fov 0.15 --max-frames 12
    python3 scripts/fetch_frames.py --ra 08h55m10.831s --dec -7d14m42.53s --name "WISE 0855"
    python3 scripts/fetch_frames.py --list-presets

The script writes rendered PNG frames and manifest.json under
``data/targets/<key>/``.  It is the same code path used by the web API.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Allow running directly from the repository root without installation.
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from backend import spherex  # noqa: E402

PRESETS = {
    "wise0855": {"name": "WISE 0855", "ra": spherex.parse_ra("08h55m10.831s"), "dec": spherex.parse_dec("-7d14m42.53s"), "fov": 0.15},
    "barnard": {"name": "Barnard's Star", "ra": 269.4521, "dec": 4.6933, "fov": 0.15},
    "epsindib": {"name": "Epsilon Indi B", "ra": 330.59025, "dec": -56.7833, "fov": 0.15},
    "rwaur": {"name": "RW Aurigae", "ra": 76.95625, "dec": 30.6747, "fov": 0.20},
    "rdra": {"name": "R Draconis", "ra": 269.4321, "dec": 57.4, "fov": 0.20},
    "custom": {"name": "custom", "ra": None, "dec": None, "fov": 0.15},
}


def _progress(stage, done, total, message):
    if total and total > 0:
        pct = 100.0 * done / total
        print(f"[{stage:>6}] {done:>3}/{total:<3} {pct:5.1f}%  {message}", flush=True)
    else:
        print(f"[{stage:>6}] {message}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch SPHEREx frames for Project WATS")
    parser.add_argument("target", nargs="?", default=None, help="preset name, or RA (decimal/sexagesimal) when --dec is omitted")
    parser.add_argument("dec_pos", nargs="?", default=None, help="Dec (decimal/sexagesimal)")
    parser.add_argument("--ra", default=None, help="Right Ascension (degrees or sexagesimal hours)")
    parser.add_argument("--dec", default=None, help="Declination (degrees or sexagesimal)")
    parser.add_argument("--name", default=None, help="display name")
    parser.add_argument("--fov", type=float, default=None, help="field of view in degrees (default 0.15)")
    parser.add_argument("--max-frames", type=int, default=None, help="maximum number of visits to render")
    parser.add_argument("--detector", default=None, help="restrict to one detector (D1..D6) or all")
    parser.add_argument("--naxis", type=int, default=spherex.DEFAULT_NAXIS, help="output image size in pixels")
    parser.add_argument("--refresh", action="store_true", help="re-query IRSA TAP even if a cached manifest exists")
    parser.add_argument("--list-presets", action="store_true", help="list preset targets and exit")
    parser.add_argument("--json", action="store_true", help="print the manifest JSON only")
    args = parser.parse_args(argv)

    if args.list_presets:
        for key, p in PRESETS.items():
            print(f"{key:10s} {p['name']:18s} RA={p['ra']} Dec={p['dec']} FOV={p['fov']}")
        return 0

    preset = PRESETS.get(str(args.target or "").lower()) if args.target else None
    if preset and preset.get("ra") is not None:
        ra = preset["ra"]
        dec = preset["dec"]
        name = args.name or preset["name"]
        fov = args.fov if args.fov is not None else preset["fov"]
    else:
        ra_text = args.ra or args.target
        dec_text = args.dec if args.dec is not None else args.dec_pos
        if ra_text is None or dec_text is None:
            parser.error("provide a preset name, or RA and Dec (positional or --ra/--dec)")
        try:
            ra = spherex.parse_ra(ra_text)
            dec = spherex.parse_dec(dec_text)
        except ValueError as exc:
            parser.error(str(exc))
        name = args.name or "custom"
        fov = args.fov if args.fov is not None else spherex.DEFAULT_FOV_DEG

    params = {
        "ra": ra,
        "dec": dec,
        "fov": fov,
        "max_frames": args.max_frames if args.max_frames is not None else spherex.DEFAULT_MAX_FRAMES,
        "naxis": args.naxis,
        "detector": args.detector,
        "name": name,
        "refresh": bool(args.refresh),
    }

    if not args.json:
        print(f"Project WATS fetch: {name}")
        print(f"  RA={ra:.6f}  Dec={dec:.6f}  FOV={fov:.3f} deg  max_frames={params['max_frames']}  detector={params['detector'] or 'all'}")
        print(f"  TAP: {spherex.TAP_URL}")

    t0 = time.time()
    try:
        manifest = spherex.build_target(params, progress_cb=_progress, force=args.refresh)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(manifest, indent=2))
        return 0

    print(f"\nDone in {time.time() - t0:.1f}s")
    print(f"  key:       {manifest['key']}")
    print(f"  frames:    {len(manifest['frames'])}")
    print(f"  visits:    {manifest.get('visits')}")
    print(f"  detectors: {', '.join(manifest.get('detectors', []))}")
    print(f"  time span: {manifest.get('time_range')}")
    print(f"  directory: {spherex.target_dir(manifest['key'])}")
    print("\nOpen in the web app:")
    print(f"  http://localhost:5000/?ra={ra:.6f}&dec={dec:.6f}&fov={fov}&name={name.replace(' ', '%20')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())