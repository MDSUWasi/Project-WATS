"""Advanced analysis API (Phase 5): moving-object detection, spectral fitting.

Endpoints:
  POST   /api/analysis/moving-objects  detect sources that move between frames
  POST   /api/analysis/spectral-fit    fit continuum + Gaussian lines to a spectrum
"""

from __future__ import annotations

import json
import math

import numpy as np
from flask import Blueprint, jsonify, request, Response

from backend.spherex.pipeline import cache_key, load_manifest, target_dir
from backend.spherex.query import WatError
from backend.spherex.util import _read_json

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

    candidates = []
    from pathlib import Path
    from PIL import Image

    prev = None
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
            # Threshold at 3 sigma above local noise.
            finite = diff[np.isfinite(diff)]
            if finite.size == 0:
                prev = img
                continue
            std = float(np.std(finite))
            thr = 3.0 * std
            mask = np.abs(diff) > thr
            ys, xs = np.where(mask)
            # Cluster into candidates (simple grid binning).
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
                candidates.append({
                    "frame_index": int(i),
                    "cx": int(cx), "cy": int(cy),
                    "x": int(np.mean(xs_p)), "y": int(np.mean(ys_p)),
                    "n_pixels": int(len(pts)),
                    "max_diff": float(max(ds, key=abs)),
                    "mean_diff": float(np.mean(ds)),
                    "confidence": round(min(1.0, len(pts) / 50.0), 3),
                })
        prev = img

    candidates.sort(key=lambda c: c["max_diff"], reverse=True)
    return jsonify({
        "status": "ready",
        "key": key,
        "n_frames": len(frames),
        "candidates": candidates[:100],
        "n_candidates": len(candidates),
        "method": "consecutive-frame-difference",
    })


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
