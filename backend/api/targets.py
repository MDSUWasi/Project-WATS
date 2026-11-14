"""Target API blueprint.

Endpoints:
  GET    /api/coverage?ra&dec&fov          coverage/completeness estimates
  POST   /api/targets                      create/load a target; returns key + coverage
  GET    /api/targets/<key>                manifest, coverage, completeness, data-release
  GET    /api/targets/<key>/frames         frame list
  GET    /api/targets/<key>/spectrum       spectrum at target position
  GET    /api/targets/<key>/lightcurve     light curve (flux vs MJD)
  GET    /api/targets/<key>/difference     difference image info
  GET    /api/targets/<key>/sources        extracted sources
  GET    /api/targets/<key>/overlays       Gaia/CatWISE sources
  GET    /api/targets/<key>/quality        quality flags / completeness
  GET    /api/targets/<key>/completeness   completeness breakdown
  GET    /api/targets/<key>/export?format= export products
"""

from __future__ import annotations

import json
import math
import time
from flask import Blueprint, jsonify, request

from backend import spherex
from backend.config import DEFAULT_RELEASE, SPHEREx_RELEASES

bp = Blueprint("targets", __name__, url_prefix="/api")

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _parse_params(args) -> dict:
    ra = spherex.parse_ra(args.get("ra"))
    dec = spherex.parse_dec(args.get("dec"))
    try:
        fov = float(args.get("fov", spherex.DEFAULT_FOV_DEG))
    except (TypeError, ValueError):
        raise ValueError("fov must be a number")
    if not math.isfinite(fov):
        raise ValueError("fov must be a finite number")
    fov = min(max(fov, 0.02), 2.0)
    try:
        max_frames = int(args.get("max_frames", spherex.DEFAULT_MAX_FRAMES))
    except (TypeError, ValueError):
        raise ValueError("max_frames must be an integer")
    max_frames = min(max(max_frames, 1), 200)
    try:
        naxis = int(args.get("naxis", spherex.DEFAULT_NAXIS))
    except (TypeError, ValueError):
        raise ValueError("naxis must be an integer")
    naxis = min(max(naxis, 128), 512)
    detector = (args.get("detector") or "").strip() or None
    if detector and detector.lower() not in ("all", "any", ""):
        det = detector.upper()
        if not det.startswith("D"):
            det = "D" + det
        if det not in {f"D{i}" for i in range(1, 7)}:
            raise ValueError("detector must be D1..D6 or 'all'")
        detector = det
    else:
        detector = None
    name = (args.get("name") or "custom").strip()[:80] or "custom"
    refresh = str(args.get("refresh", "")).lower() in ("1", "true", "yes", "on")
    release = (args.get("release") or DEFAULT_RELEASE).strip().lower()
    if release and release not in {r["key"] for r in SPHEREx_RELEASES}:
        release = DEFAULT_RELEASE
    return {
        "ra": ra,
        "dec": dec,
        "fov": fov,
        "max_frames": max_frames,
        "naxis": naxis,
        "detector": detector,
        "name": name,
        "refresh": refresh,
        "release": release,
    }


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------


@bp.get("/health")
def health():
    payload = spherex.health()
    return jsonify(payload)


@bp.get("/releases")
def releases():
    """List the known SPHEREx data releases the workbench can query.

    New releases are appended to ``backend.config.SPHEREx_RELEASES`` as they
    are published by IRSA; this endpoint lets the UI stay in sync without a
    code change.
    """
    from backend.config import SPHEREx_RELEASES, DEFAULT_RELEASE

    return jsonify({
        "status": "ready",
        "default": DEFAULT_RELEASE,
        "releases": [
            {"key": r["key"], "name": r["name"], "uri_pattern": r["uri_pattern"],
             "description": r.get("description", "")}
            for r in SPHEREx_RELEASES
        ],
    })


@bp.get("/presets")
def presets():
    return jsonify({"status": "ready", "presets": spherex.PRESETS})


@bp.get("/coverage")
def coverage():
    try:
        params = _parse_params(request.args)
    except ValueError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    rows = spherex.query_spherex(params["ra"], params["dec"])
    available_visits = len({r["obs_id"] for r in rows})
    available_detectors = sorted({r["detector"] for r in rows})
    bands = {spherex.DETECTOR_NAMES.get(r["detector"], f"D{r['detector']}") for r in rows}

    # Per-visit breakdown: list each visit with its detectors and MJD range.
    visits: dict[str, dict] = {}
    for r in rows:
        vid = r["obs_id"] or r["obs_base"] or r["uri"]
        v = visits.setdefault(vid, {
            "obs_id": vid, "obs_base": r.get("obs_base", ""),
            "mjd": r["mjd"], "detectors": [], "release": r.get("release", ""),
            "version": r.get("version", ""),
        })
        det = r["detector"]
        if det not in v["detectors"]:
            v["detectors"].append(det)
    for v in visits.values():
        v["detectors"].sort()
        v["n_detectors"] = len(v["detectors"])

    # Band coverage across SPHEREx's 102 bands (using detector bandpasses as proxy).
    band_ranges = []
    for det in available_detectors:
        lo, hi = spherex.DETECTOR_BANDS.get(det, (0.0, 0.0))
        band_ranges.append({"detector": spherex.DETECTOR_NAMES.get(det, f"D{det}"),
                            "wavelength_min_um": lo, "wavelength_max_um": hi})

    return jsonify({
        "status": "ready",
        "ra": params["ra"],
        "dec": params["dec"],
        "fov": params["fov"],
        "available_visits": available_visits,
        "available_detectors": len(available_detectors),
        "detector_names": [spherex.DETECTOR_NAMES.get(d, f"D{d}") for d in available_detectors],
        "bands_covered": len(bands),
        "bands": sorted(bands),
        "band_ranges": band_ranges,
        "query_rows": len(rows),
        "visits": list(visits.values()),
        "components": {
            "visits": {"available": available_visits, "fetched": 0, "score": 0.0},
            "detectors": {"available": len(available_detectors), "fetched": 0, "score": 0.0},
            "bands": {"available": 102, "covered": len(bands), "score": round(len(bands) / 102.0, 4)},
            "quality": {"score": 0.0},
            "catalog": {"score": 0.0},
        },
        "message": f"{available_visits} visits, {len(available_detectors)} detectors covering the field.",
    })


@bp.post("/targets")
def create_target():
    try:
        params = _parse_params(request.args)
    except ValueError as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    key = spherex.cache_key(params)
    manifest = spherex.load_manifest(key)
    coverage = None
    if manifest:
        coverage = {
            "available_visits": manifest.get("visits", 0),
            "available_detectors": len(manifest.get("detectors", [])),
            "bands_covered": len(manifest.get("detectors", [])),
            "frames_ready": len(manifest.get("frames", [])),
        }
    return jsonify({
        "status": "ready",
        "key": key,
        "coverage": coverage,
        "manifest": manifest,
    })


@bp.get("/targets/<key>")
def get_target(key):
    manifest = spherex.load_manifest(key)
    if not manifest:
        return jsonify({"status": "error", "error": "target not found"}), 404
    return jsonify({"status": "ready", "key": key, **manifest})


@bp.get("/targets/<key>/frames")
def target_frames(key):
    manifest = spherex.load_manifest(key)
    if not manifest:
        return jsonify({"status": "error", "error": "target not found"}), 404
    frames = manifest.get("frames", [])
    return jsonify({"status": "ready", "key": key, "frames": frames, "n_frames": len(frames)})


@bp.get("/targets/<key>/spectrum")
def target_spectrum(key):
    manifest = spherex.load_manifest(key)
    if not manifest:
        return jsonify({"status": "pending", "key": key, "message": "Frames are not ready yet."}), 202
    params = {
        "ra": manifest["ra"],
        "dec": manifest["dec"],
        "fov": manifest.get("fov_deg", spherex.DEFAULT_FOV_DEG),
        "max_frames": manifest.get("max_frames", spherex.DEFAULT_MAX_FRAMES),
        "naxis": manifest.get("naxis", spherex.DEFAULT_NAXIS),
        "detector": manifest.get("detector_filter"),
        "name": manifest.get("name", "custom"),
        "key": key,
    }
    try:
        return jsonify(spherex.extract_spectrum(params))
    except Exception as exc:
        return jsonify({"status": "error", "error": f"Spectrum could not be extracted: {exc}"}), 500


@bp.get("/targets/<key>/lightcurve")
def target_lightcurve(key):
    manifest = spherex.load_manifest(key)
    if not manifest:
        return jsonify({"status": "error", "error": "target not found"}), 404
    points = []
    for frame in manifest.get("frames", []):
        points.append({
            "frame": frame.get("index"),
            "mjd": frame.get("mjd"),
            "time": frame.get("time"),
            "background_mjy_sr": frame.get("background_mjy_sr"),
            "noise_mjy_sr": frame.get("noise_mjy_sr"),
            "peak_mjy_sr": frame.get("peak_mjy_sr"),
            "detectors": frame.get("detectors"),
            "wavelength_um": frame.get("wavelength_um"),
        })
    return jsonify({"status": "ready", "key": key, "points": points, "n_points": len(points)})


@bp.get("/targets/<key>/overlays")
def target_overlays(key):
    manifest = spherex.load_manifest(key)
    if not manifest:
        return jsonify({"status": "error", "error": "target not found"}), 404
    try:
        ra = float(request.args.get("ra", manifest["ra"]))
        dec = float(request.args.get("dec", manifest["dec"]))
        fov = float(request.args.get("fov", manifest.get("fov_deg", spherex.DEFAULT_FOV_DEG)))
        naxis = int(request.args.get("naxis", manifest.get("naxis", spherex.DEFAULT_NAXIS)))
    except (TypeError, ValueError) as exc:
        return jsonify({"status": "error", "error": str(exc)}), 400
    try:
        return jsonify(spherex.fetch_overlays(ra, dec, fov, naxis))
    except Exception as exc:
        return jsonify({"status": "error", "error": f"Overlays unavailable: {exc}"}), 502


@bp.get("/targets/<key>/quality")
def target_quality(key):
    manifest = spherex.load_manifest(key)
    if not manifest:
        return jsonify({"status": "error", "error": "target not found"}), 404
    frames = manifest.get("frames", [])
    total_bad = sum(int(f.get("flags_bad", 0)) for f in frames)
    total_flags = sum(int(f.get("flags_total", 0)) for f in frames)
    return jsonify({
        "status": "ready",
        "key": key,
        "n_frames": len(frames),
        "flags_bad_total": total_bad,
        "flags_total": total_flags,
        "flags_bad_fraction": (total_bad / total_flags) if total_flags else 0.0,
        "frames": [
            {
                "index": f.get("index"),
                "mjd": f.get("mjd"),
                "flags_bad_fraction": f.get("flags_bad_fraction"),
                "n_detectors": f.get("n_detectors"),
                "n_channels": f.get("n_channels"),
            }
            for f in frames
        ],
    })


@bp.get("/targets/<key>/completeness")
def target_completeness(key):
    manifest = spherex.load_manifest(key)
    if not manifest:
        return jsonify({"status": "error", "error": "target not found"}), 404
    frames = manifest.get("frames", [])
    detectors = manifest.get("detectors", [])
    visits = len(frames)
    max_frames = manifest.get("max_frames", 6)

    # Band coverage: distinct wavelengths across frames (out of 102 SPHEREx bands).
    wls = set()
    for f in frames:
        wls_by_det = f.get("wavelengths_um") or {}
        for v in wls_by_det.values():
            if isinstance(v, (int, float)) and v > 0:
                wls.add(round(float(v), 3))
    band_coverage = len(wls) / 102.0 if wls else 0.0

    # Visit coverage = fetched / available (use max_frames as available proxy).
    visits_score = min(1.0, visits / max(1, max_frames))
    # Detector coverage = fetched detectors / 6.
    det_score = min(1.0, len(detectors) / 6.0)
    # Pixel quality = fraction of unflagged pixels across frames.
    total_bad = sum(int(f.get("flags_bad", 0)) for f in frames)
    total_flags = sum(int(f.get("flags_total", 0)) for f in frames)
    pixel_quality = 1.0 - ((total_bad / total_flags) if total_flags else 0.0)
    # Catalog match = placeholder until cross-match is implemented (Phase 1 task 14).
    catalog_match = 0.0

    components = {
        "visits": round(visits_score, 3),
        "detectors": round(det_score, 3),
        "bands": round(band_coverage, 3),
        "pixel_quality": round(pixel_quality, 3),
        "catalog": round(catalog_match, 3),
    }
    # Weights per plan: visits 0.40, detectors 0.20, bands 0.20, pixel quality 0.10, catalog 0.10.
    score = round(
        0.40 * visits_score
        + 0.20 * det_score
        + 0.20 * band_coverage
        + 0.10 * pixel_quality
        + 0.10 * catalog_match,
        3,
    )
    return jsonify({
        "status": "ready",
        "key": key,
        "completeness_score": score,
        "components": components,
        "weights": {"visits": 0.40, "detectors": 0.20, "bands": 0.20,
                    "pixel_quality": 0.10, "catalog": 0.10},
        "available_visits": manifest.get("visits", 0),
        "fetched_visits": visits,
        "available_detectors": 6,
        "fetched_detectors": len(detectors),
        "bands_covered": len(wls),
        "flags_bad_fraction": (total_bad / total_flags) if total_flags else 0.0,
        "message": "Completeness breakdown (visits/detectors/bands/pixel quality/catalog match).",
    })


@bp.get("/targets/<key>/difference")
def target_difference(key):
    manifest = spherex.load_manifest(key)
    if not manifest:
        return jsonify({"status": "error", "error": "target not found"}), 404
    frames = manifest.get("frames", [])
    if len(frames) < 2:
        return jsonify({"status": "error", "error": "need at least 2 frames for difference"}), 400
    pairs = []
    for i in range(1, len(frames)):
        pairs.append({
            "frame_a": frames[i - 1].get("index"),
            "frame_b": frames[i].get("index"),
            "mjd_a": frames[i - 1].get("mjd"),
            "mjd_b": frames[i].get("mjd"),
            "time_a": frames[i - 1].get("time"),
            "time_b": frames[i].get("time"),
            "gray_diff_url": f"/frames/{key}/{frames[i].get('index', i):03d}_gray_sub.png",
            "color_diff_url": f"/frames/{key}/{frames[i].get('index', i):03d}_color_sub.png",
        })
    return jsonify({"status": "ready", "key": key, "pairs": pairs, "n_pairs": len(pairs)})


@bp.get("/targets/<key>/sources")
def target_sources(key):
    manifest = spherex.load_manifest(key)
    if not manifest:
        return jsonify({"status": "error", "error": "target not found"}), 404
    # Simple source extraction: threshold-based detection on each frame's gray image.
    import numpy as np
    from PIL import Image

    sources = []
    for f in manifest.get("frames", []):
        gray_path = spherex.target_dir(key) / f.get("gray", "")
        if not gray_path.is_file():
            continue
        try:
            img = np.asarray(Image.open(gray_path), dtype=float) / 255.0
            # Threshold at 3 sigma above median.
            finite = img[np.isfinite(img)]
            if finite.size == 0:
                continue
            med = float(np.median(finite))
            std = float(np.std(finite))
            thr = med + 3.0 * std
            mask = img > thr
            # Find connected components (simple: just report bright pixels above threshold).
            ys, xs = np.where(mask)
            for x, y in zip(xs[::max(1, len(xs) // 50)], ys[::max(1, len(ys) // 50)]):
                sources.append({
                    "frame_index": f.get("index"),
                    "mjd": f.get("mjd"),
                    "x": int(x),
                    "y": int(y),
                    "flux": float(img[y, x]),
                    "ra": None,
                    "dec": None,
                    "match_gaia": None,
                    "match_catwise": None,
                })
        except Exception:
            continue
    sources.sort(key=lambda s: s.get("flux", 0), reverse=True)
    return jsonify({"status": "ready", "key": key, "sources": sources[:100], "n_sources": len(sources)})


@bp.get("/targets/<key>/export")
def target_export(key):
    manifest = spherex.load_manifest(key)
    if not manifest:
        return jsonify({"status": "error", "error": "target not found"}), 404
    fmt = (request.args.get("format") or "json").lower()
    if fmt == "json":
        return jsonify({"status": "ready", "key": key, "format": "json", "data": manifest})
    if fmt == "csv":
        what = request.args.get("what", "frames")
        if what == "frames":
            import csv, io
            frames = manifest.get("frames", [])
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=[
                "index", "mjd", "time", "obs_id", "obs_base", "detectors", "n_detectors",
                "n_channels", "wavelength_um", "exptime", "background_mjy_sr",
                "noise_mjy_sr", "peak_mjy_sr", "flags_bad_fraction", "release", "version",
            ])
            writer.writeheader()
            for f in frames:
                writer.writerow({k: f.get(k) for k in writer.fieldnames})
            return buf.getvalue(), 200, {"Content-Type": "text/csv",
                                         "Content-Disposition": f'attachment; filename="{key}_frames.csv"'}
        if what == "lightcurve":
            import csv, io
            frames = manifest.get("frames", [])
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=["index", "mjd", "time",
                                                     "background_mjy_sr", "noise_mjy_sr", "peak_mjy_sr",
                                                     "wavelength_um", "detectors"])
            writer.writeheader()
            for f in frames:
                writer.writerow({
                    "index": f.get("index"), "mjd": f.get("mjd"), "time": f.get("time"),
                    "background_mjy_sr": f.get("background_mjy_sr"), "noise_mjy_sr": f.get("noise_mjy_sr"),
                    "peak_mjy_sr": f.get("peak_mjy_sr"), "wavelength_um": f.get("wavelength_um"),
                    "detectors": ",".join(f.get("detectors", [])),
                })
            return buf.getvalue(), 200, {"Content-Type": "text/csv",
                                         "Content-Disposition": f'attachment; filename="{key}_lightcurve.csv"'}
        return jsonify({"status": "error", "error": f"CSV export for '{what}' not yet supported"}), 400
    return jsonify({"status": "error", "error": f"export format '{fmt}' not yet supported"}), 400
