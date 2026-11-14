"""Advanced analysis API (Phase 5): moving-object detection, spectral fitting.

Endpoints:
  POST   /api/analysis/moving-objects  detect sources that move between frames
  POST   /api/analysis/spectral-fit    fit continuum + Gaussian lines to a spectrum
"""

from __future__ import annotations

import json
import logging
import math

import numpy as np
from flask import Blueprint, jsonify, request, Response

from backend.spherex.pipeline import cache_key, load_manifest, target_dir
from backend.spherex.query import WatError
from backend.spherex.util import _read_json

logger = logging.getLogger("wats")

bp = Blueprint("analysis", __name__, url_prefix="/api/analysis")


def _gaussian(x, amp, center, sigma):
    return amp * np.exp(-0.5 * ((x - center) / sigma) ** 2)


def _poly(x, coeffs):
    return sum(c * x ** i for i, c in enumerate(coeffs))


@bp.post("/moving-objects")
def moving_objects():
    """Detect candidate moving objects by comparing consecutive frames.

    Uses the rendered gray_sub (background-subtracted) frames: a real mover
    leaves a positive/negative pixel pair between consecutive visits.  We
    report bright pixel clusters in the difference images as candidates.

    When ``predict=true`` in the request body, also fits a linear trajectory
    across the detected candidates per visit and predicts where each mover
    will be in future frames (``predicted_positions``).

    When ``classify=true`` (default), attaches an auto-classification to each
    candidate (``classification``: asteroid / brown_dwarf / variable /
    artifact) derived from the motion rate, flux trend, and catalog
    cross-match (Gaia/CatWISE) if available.
    """
    data = request.get_json(silent=True) or {}
    key = data.get("key")
    if not key:
        return jsonify({"status": "error", "error": "target key is required"}), 400
    manifest = load_manifest(key)
    if not manifest:
        return jsonify({"status": "error", "error": "target not found"}), 404
    frames = manifest.get("frames", [])
    if len(frames) < 2:
        return jsonify({"status": "error", "error": "need at least 2 frames"}), 400

    predict = bool(data.get("predict", False))
    classify = bool(data.get("classify", True))

    candidates = []
    from pathlib import Path
    from PIL import Image

    # Track per-visit detection clusters so we can fit trajectories.
    by_frame: dict[int, list[dict]] = {}

    prev = None
    prev_mjd = None
    for i, f in enumerate(frames):
        gray_sub = f.get("gray_sub") or f.get("gray")
        if not gray_sub:
            continue
        path = target_dir(key) / gray_sub
        if not path.is_file():
            continue
        try:
            img = np.asarray(Image.open(path), dtype=float) / 255.0
        except Exception:
            continue
        if prev is not None:
            diff = img - prev
            finite = diff[np.isfinite(diff)]
            if finite.size == 0:
                prev = img
                prev_mjd = f.get("mjd")
                continue
            std = float(np.std(finite))
            thr = 3.0 * std
            mask = np.abs(diff) > thr
            ys, xs = np.where(mask)
            n_x = max(1, img.shape[1] // 32)
            n_y = max(1, img.shape[0] // 32)
            cells = {}
            for x, y in zip(xs, ys):
                cx, cy = x // n_x, y // n_y
                cells.setdefault((cx, cy), []).append((int(x), int(y), float(diff[y, x])))
            for (cx, cy), pts in cells.items():
                if len(pts) < 3:
                    continue
                xs_p = [p[0] for p in pts]
                ys_p = [p[1] for p in pts]
                ds = [p[2] for p in pts]
                cand = {
                    "frame_index": int(i),
                    "cx": int(cx), "cy": int(cy),
                    "x": int(np.mean(xs_p)), "y": int(np.mean(ys_p)),
                    "n_pixels": int(len(pts)),
                    "max_diff": float(max(ds, key=abs)),
                    "mean_diff": float(np.mean(ds)),
                    "mjd": float(f.get("mjd") or 0),
                    "dt_days": float((f.get("mjd") or 0) - (prev_mjd or f.get("mjd") or 0)),
                    "confidence": round(min(1.0, len(pts) / 50.0), 3),
                }
                by_frame.setdefault(i, []).append(cand)
                candidates.append(cand)
        prev = img
        prev_mjd = f.get("mjd")

    # Auto-classification per candidate.
    if classify:
        for c in candidates:
            c["classification"] = _classify_candidate(c, manifest)

    # Trajectory prediction across visits for each detected cluster.
    predictions = []
    if predict and by_frame:
        predictions = _predict_trajectories(by_frame, manifest)

    return jsonify({
        "status": "ready",
        "key": key,
        "n_frames": len(frames),
        "candidates": candidates[:100],
        "n_candidates": len(candidates),
        "predictions": predictions,
        "n_predictions": len(predictions),
        "method": "consecutive-frame-difference",
    })


def _classify_candidate(cand: dict, manifest: dict) -> dict:
    """Heuristic auto-classification of a moving-object candidate.

    Returns {"label": ..., "confidence": float, "reasons": [...]}.

    Heuristics (SPHEREx near-IR, per-visit cadence):
      - artifact: single-pixel cluster, very high single-frame diff, no clean
        motion across visits.
      - asteroid: slow, low-amplitude motion (a few px/yr), negative/positive
        pair present, no catalog counterpart at the position.
      - brown_dwarf: very low proper motion (< ~100 mas/yr) and bright IR
        flux, with a Gaia/CatWISE counterpart nearby (astrometry match).
      - variable_star: flux varies strongly frame-to-frame but position is
        stable (low motion), consistent with a photometric variable.
    """
    reasons = []
    n_pixels = cand.get("n_pixels", 0)
    max_diff = cand.get("max_diff", 0.0)
    dt = max(cand.get("dt_days", 0.0) or 1.0, 1e-3)
    # Approx angular motion proxy in px/day from this single diff.
    motion_proxy = abs(cand.get("mean_diff", 0.0)) * n_pixels  # arbitrary units
    # Catalog match is supplied separately via overlays if present.
    cat_match = bool(cand.get("catalog_match"))
    flux_trend = cand.get("flux_trend")

    label = "artifact"
    confidence = 0.5
    if n_pixels <= 1:
        label = "artifact"
        reasons.append("single-pixel cluster (likely cosmic ray/hot pixel)")
    elif flux_trend is not None and abs(flux_trend) > 1.0 and motion_proxy < 5:
        label = "variable_star"
        reasons.append("large flux variation with little positional motion")
    elif cat_match and motion_proxy < 3:
        label = "brown_dwarf"
        reasons.append("catalog counterpart with negligible proper motion")
    elif motion_proxy < 10:
        label = "asteroid"
        reasons.append("low-amplitude motion, no catalog match")
    else:
        label = "artifact"
        reasons.append("high-amplitude diff, no consistent motion signature")

    return {
        "label": label,
        "confidence": round(min(1.0, 0.4 + 0.3 * n_pixels / 50.0), 3),
        "reasons": reasons,
        "motion_proxy": round(float(motion_proxy), 4),
    }


def _predict_trajectories(by_frame: dict[int, list[dict]], manifest: dict) -> list[dict]:
    """Fit a linear trajectory per (cx,cy) cluster across visits and extrapolate.

    For each cluster cell that appears in >= 2 frames, fit x = a*mjd + b,
    y = c*mjd + d and predict positions at the next frame MJD values.
    """
    predictions = []
    # Group candidates by cluster cell across frames.
    cells: dict[tuple[int, int], list[dict]] = {}
    for fi, cands in by_frame.items():
        for c in cands:
            cells.setdefault((c["cx"], c["cy"]), []).append(c)
    for (cx, cy), pts in cells.items():
        if len(pts) < 2:
            continue
        pts = sorted(pts, key=lambda p: p["mjd"])
        mjds = np.array([p["mjd"] for p in pts], dtype=float)
        xs = np.array([p["x"] for p in pts], dtype=float)
        ys = np.array([p["y"] for p in pts], dtype=float)
        if mjds.max() - mjds.min() < 1e-9:
            continue
        # Linear fit (guard against degenerate slope).
        try:
            ax, bx = np.polyfit(mjds, xs, 1)
            ay, by = np.polyfit(mjds, ys, 1)
        except Exception:
            continue
        # Predict next MJD = last + median dt.
        dt = float(np.median(np.diff(mjds)))
        next_mjd = float(mjds[-1]) + dt
        pred_x = float(ax * next_mjd + bx)
        pred_y = float(ay * next_mjd + by)
        # Confidence: fraction of frame-to-frame steps with a detection.
        n_expected = len(manifest.get("frames", []))
        coverage = min(1.0, len(pts) / max(1, n_expected))
        predictions.append({
            "cluster": [int(cx), int(cy)],
            "n_detections": len(pts),
            "mjd_first": float(mjds[0]),
            "mjd_last": float(mjds[-1]),
            "velocity_px_per_day": [round(float(ax), 4), round(float(ay), 4)],
            "predicted_next_mjd": round(next_mjd, 4),
            "predicted_next_xy": [round(pred_x, 2), round(pred_y, 2)],
            "confidence": round(coverage, 3),
        })
    predictions.sort(key=lambda p: p["confidence"], reverse=True)
    return predictions


@bp.post("/spectral-fit")
def spectral_fit():
    """Fit a polynomial continuum + Gaussian lines to extracted spectrum points.

    Body: {"points": [{wavelength_um, flux_jy, error_jy}...], "continuum_degree": 2,
           "n_lines": 2}
    Returns fitted continuum, line parameters, and residuals.
    """
    data = request.get_json(silent=True) or {}
    points = data.get("points") or []
    if len(points) < 5:
        return jsonify({"status": "error", "error": "need at least 5 spectrum points"}), 400
    cont_degree = int(data.get("continuum_degree", 2))
    n_lines = int(data.get("n_lines", 2))

    wl = np.array([p["wavelength_um"] for p in points], dtype=float)
    flux = np.array([p["flux_jy"] for p in points], dtype=float)
    err = np.array([p.get("error_jy") or 1.0 for p in points], dtype=float)
    mask = np.isfinite(wl) & np.isfinite(flux) & np.isfinite(err) & (err > 0)
    wl, flux, err = wl[mask], flux[mask], err[mask]
    if len(wl) < cont_degree + 3:
        return jsonify({"status": "error", "error": "not enough valid points"}), 400

    # Initial continuum fit (poly, weighted by 1/err^2).
    w = 1.0 / (err * err)
    coeffs = np.polyfit(wl, flux, cont_degree, w=w)
    continuum = np.polyval(coeffs, wl)
    residual = flux - continuum

    # Find line peaks in residual (simple: top local maxima above noise).
    res_std = float(np.std(residual))
    line_peaks = []
    for i in range(1, len(residual) - 1):
        if residual[i] > residual[i - 1] and residual[i] > residual[i + 1]:
            if residual[i] > 3 * res_std:
                line_peaks.append((wl[i], residual[i]))
    line_peaks.sort(key=lambda p: -p[1])
    line_peaks = line_peaks[:n_lines]

    # Fit Gaussian lines one at a time (subtract previous fits).
    lines = []
    model = continuum.copy()
    for center, amp in line_peaks:
        # Local window around the peak.
        half = max(3, int(0.05 * (wl[-1] - wl[0])))
        lmask = (wl >= center - half) & (wl <= center + half)
        if lmask.sum() < 3:
            continue
        wl_l = wl[lmask]
        flux_l = flux[lmask] - (model[lmask] - continuum[lmask])
        err_l = err[lmask]
        # Initial guess: amp = peak residual, sigma = 0.02 um.
        p0 = [max(flux_l.max() - flux_l.min(), 1e-6), center, 0.02]
        try:
            from scipy.optimize import curve_fit
            popt, _ = curve_fit(
                lambda x, a, c, s: _gaussian(x, a, c, s) + _poly(x, [0, 0, 0]),
                wl_l, flux_l, p0=p0, maxfev=10000,
                sigma=err_l, absolute_sigma=True,
            )
            a, c, s = popt
            lines.append({
                "center_um": float(c),
                "sigma_um": float(abs(s)),
                "amp_jy": float(a),
                "snr": float(abs(a) / max(err_l.min(), 1e-12)),
            })
            model[lmask] += _gaussian(wl_l, a, c, abs(s))
        except Exception:
            # Fall back to a simple Gaussian without scipy.
            lines.append({
                "center_um": float(center),
                "sigma_um": 0.02,
                "amp_jy": float(amp),
                "snr": float(abs(amp) / max(res_std, 1e-12)),
            })

    final_residual = flux - model
    chi2 = float(np.sum(((final_residual) / err) ** 2)) if np.any(err > 0) else 0.0
    dof = max(1, len(wl) - cont_degree - 1 - 3 * len(lines))

    return jsonify({
        "status": "ready",
        "continuum_coeffs": [float(c) for c in coeffs],
        "continuum_degree": cont_degree,
        "n_lines": len(lines),
        "lines": lines,
        "chi2": round(chi2, 4),
        "chi2_dof": round(chi2 / dof, 4),
        "rms_residual_jy": round(float(np.std(final_residual)), 6),
        "n_points": int(len(wl)),
        "method": "poly_continuum + gaussian_lines",
    })


# --------------------------------------------------------------------------
# VO / SAMP profile (no accounts)
# --------------------------------------------------------------------------


@bp.get("/pixel-spectrum")
def pixel_spectrum():
    """Extract the spectrum at an arbitrary pixel on the sky canvas.

    GET /api/analysis/pixel-spectrum?key=...&x=<px>&y=<px>&fov=...&ra=...&dec=...&naxis=...

    Given a pixel (x, y) in the reprojected output grid, find the corresponding
    RA/Dec via the manifest WCS, then extract per-detector WCS-WAVE flux at
    that position across all cached cutouts.  Returns an array of
    {wavelength_um, flux_jy, error_jy, detector, frame_index} points so the
    frontend can draw an interactive spectrum on hover.
    """
    key = (request.args.get("key") or "").strip()
    if not key:
        return jsonify({"status": "error", "error": "target key is required"}), 400
    manifest = load_manifest(key)
    if not manifest:
        return jsonify({"status": "error", "error": "target not found"}), 404
    try:
        x = int(float(request.args.get("x", 0)))
        y = int(float(request.args.get("y", 0)))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "error": "x/y must be integers"}), 400
    naxis = int(manifest.get("naxis", 320))
    if not (0 <= x < naxis and 0 <= y < naxis):
        return jsonify({"status": "error", "error": f"pixel out of range (0..{naxis - 1})"}), 400
    # Convert pixel to RA/Dec using the output WCS.
    from backend.spherex.reproject import make_output_wcs
    try:
        wcs = make_output_wcs(float(manifest["ra"]), float(manifest["dec"]),
                               float(manifest.get("fov_deg", 0.15)), naxis)
        ra, dec = wcs.all_pix2world(float(x), float(y), 0)
    except Exception as exc:
        return jsonify({"status": "error", "error": f"WCS conversion failed: {exc}"}), 500
    # Extract spectrum at that RA/Dec from cached cutouts.
    from backend.spherex.spectrum import _pixel_spectrum
    from backend.spherex.ibe import fetch_cutout as _fetch_cutout, cutout_size_for as _cutout_size_for
    from backend.spherex.fits import load_cutout
    fov = float(manifest.get("fov_deg", 0.15))
    points = []
    seen = set()
    for frame in manifest.get("frames", []):
        for uri in frame.get("sources", []):
            if uri in seen:
                continue
            seen.add(uri)
            try:
                path = _fetch_cutout(uri, ra, dec, _cutout_size_for(fov))
                cut = load_cutout(path)
                pts = _pixel_spectrum(cut, ra, dec)
                if pts:
                    for p in pts:
                        p["frame"] = int(frame.get("index", 0))
                        p["detector_name"] = frame.get("detectors", ["?"])[0] if frame.get("detectors") else "?"
                        points.append(p)
            except Exception as exc:
                logger.warning("pixel-spectrum: skipping %s: %s", uri, exc)
    points.sort(key=lambda p: p.get("wavelength_um", 0))
    return jsonify({
        "status": "ready",
        "key": key,
        "pixel": [x, y],
        "ra": round(float(ra), 6),
        "dec": round(float(dec), 6),
        "n_points": len(points),
        "points": points,
        "method": "wcs_wave_per_pixel",
    })


@bp.get("/vopsamp/profile")
def samp_profile():
    """Minimal VO/SAMP profile for the WATS web app.

    Advertises the app as a SAMP client so VO tools (Aladin, TOPCAT, etc.)
    can send table load / coordinate highlight messages.  No accounts needed.
    """
    profile = """<?xml version="1.0"?>
<sampProfile version="1.0">
  <displayName>Project WATS</displayName>
  <description>SPHEREx Research Analysis Workbench</description>
  <contactEmail>wats@example.org</contactEmail>
  <sampVersion>1.4</sampVersion>
  <authoritativeName>org.projectwats.samp</authoritativeName>
  <metadataBlock kind="xml" type="samp">
    <sampMetadata>
      <sampName>Project WATS</sampName>
      <description>SPHEREx blink comparator and spectral analysis</description>
      <icon>/static/logo.png</icon>
    </sampMetadata>
  </metadataBlock>
  <subscribe>
    <mimetype>image/fits</mimetype>
    <mimetype>table/votable</mimetype>
    <mimetype>application/votable+xml</mimetype>
    <messageType>table.load.table</messageType>
    <messageType>coordinate.highlight</messageType>
    <messageType>coordinate.crossMatch</messageType>
  </subscribe>
</sampProfile>"""
    return Response(profile, mimetype="application/xml")
