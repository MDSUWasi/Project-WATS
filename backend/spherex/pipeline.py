"""Target build pipeline: query -> cutout -> reproject -> render -> manifest."""

from __future__ import annotations

import logging
import os
import math
import shutil
import tempfile
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from backend import config as _config
from backend.config import (
    DEFAULT_FOV_DEG,
    DEFAULT_MAX_FRAMES,
    DEFAULT_NAXIS,
    DEFAULT_RELEASE,
    DETECTOR_BANDS,
    DETECTOR_NAMES,
    release_name,
)
TARGET_DIR = _config.TARGET_DIR
from backend.spherex.fits import Cutout, load_cutout
from backend.spherex.ibe import fetch_cutout as _fetch_cutout, cutout_size_for
from backend.spherex.query import WatError, mjd_to_iso, query_spherex, TAP_URL
from backend.spherex.render import (
    compute_scale,
    render_color,
    render_gray,
    render_detector_bands,
    _subtract_background,
)
from backend.spherex.reproject import (
    Reprojected,
    channel_for_detector,
    make_output_wcs,
    reproject_cutout,
    wcs_to_dict,
)
from backend.spherex.util import _atomic_write_json, _hash, _json_default, _read_json
from backend.config import DEFAULT_RELEASE, release_name

logger = logging.getLogger("wats")


def cache_key(params: dict) -> str:
    detector = str(params.get("detector") or "all").strip().upper()
    if detector in ("", "ALL", "ANY"):
        detector = "all"
    elif not detector.startswith("D"):
        detector = "D" + detector
    release = str(params.get("release") or DEFAULT_RELEASE).strip().lower() or DEFAULT_RELEASE
    return _hash(
        "target",
        f"{float(params['ra']):.7f}",
        f"{float(params['dec']):.7f}",
        f"{float(params.get('fov', DEFAULT_FOV_DEG)):.5f}",
        max(1, int(params.get("max_frames", DEFAULT_MAX_FRAMES))),
        detector,
        int(params.get("naxis", DEFAULT_NAXIS)),
        release,
    )


def _preset_dir(key: str) -> Path:
    """Return the committed preset directory for a key, if it exists."""
    return Path(_config.PRESET_DIR) / key


def target_dir(key: str) -> Path:
    """Return the directory for a target's rendered frames.

    Checks the committed preset directory first, then falls back to the
    runtime data directory.  This lets baked presets survive deploys on
    hosts with ephemeral storage.
    """
    preset = _preset_dir(key)
    if (preset / "manifest.json").exists():
        return preset
    return Path(_config.TARGET_DIR) / key


def load_manifest(key: str):
    path = target_dir(key) / "manifest.json"
    return _read_json(path)


def save_manifest(key: str, manifest: dict) -> None:
    # Always write to the runtime data directory, never to committed presets.
    _atomic_write_json(Path(_config.TARGET_DIR) / key / "manifest.json", manifest)


def clear_target(key: str) -> None:
    """Remove the target's runtime data directory and any cached spectrum.

    Committed presets are read-only and are never touched here.  Orphaned
    cutout cache entries for this target are also removed so the LRU cache
    does not grow without bound.
    """
    path = Path(_config.TARGET_DIR) / key
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    # Clean up any cache_entries rows pointing at this target's cutouts.
    try:
        from backend.cache import Cache
        from backend.config import WatsConfig

        Cache(WatsConfig.from_env()).drop_target_entries(key)
    except Exception:
        pass


def _is_preset_key(key: str) -> bool:
    """Return True when ``key`` points at a committed (baked) preset."""
    return (_preset_dir(key) / "manifest.json").exists()


def _sync_preset(key: str) -> None:
    """Copy the freshly built runtime manifest back into the preset directory.

    Presets are baked into the repo so they survive deploys on ephemeral
    hosts, but the pipeline always writes to the runtime directory.  When a
    preset key is rebuilt (e.g. via ``--refresh``) the baked copy must be
    updated too, otherwise ``target_dir()`` keeps returning the stale file.
    """
    preset_dir = _preset_dir(key)
    runtime_dir = Path(_config.TARGET_DIR) / key
    if not preset_dir.exists():
        return
    preset_dir.mkdir(parents=True, exist_ok=True)
    manifest_src = runtime_dir / "manifest.json"
    if manifest_src.is_file():
        shutil.copy2(manifest_src, preset_dir / "manifest.json")
    for item in runtime_dir.iterdir():
        if item.name == "manifest.json":
            continue
        dst = preset_dir / item.name
        if item.is_file():
            shutil.copy2(item, dst)


def _select_epochs(rows: list[dict], max_frames: int, detector: Optional[str]) -> list[dict]:
    """Group MEFs by visit, pick one row per detector, and thin to max_frames."""
    if detector:
        try:
            det = int(str(detector).upper().lstrip("D"))
        except Exception:
            det = 0
        if det:
            rows = [r for r in rows if int(r.get("detector", 0)) == det]
    groups: dict[str, dict] = {}
    for row in rows:
        visit = str(row.get("obs_id") or row.get("obs_base") or row.get("uri", ""))
        group = groups.setdefault(visit, {"base": visit, "mjd": float(row.get("mjd", 0)), "rows": {}, "all_rows": []})
        group["all_rows"].append(row)
        det = int(row.get("detector", 0))
        prev = group["rows"].get(det)
        if prev is None or float(row.get("exptime", 0)) > float(prev.get("exptime", 0)):
            group["rows"][det] = row
    epochs = []
    for base, group in groups.items():
        if not group["rows"]:
            continue
        selected = [group["rows"][det] for det in sorted(group["rows"])]
        epochs.append(
            {
                "base": base,
                "mjd": min(float(r.get("mjd", 0)) for r in selected),
                "rows": selected,
                "n_rows": len(group["all_rows"]),
            }
        )
    epochs.sort(key=lambda e: e["mjd"])
    if max_frames and len(epochs) > int(max_frames):
        n = int(max_frames)
        idx = np.unique(np.linspace(0, len(epochs) - 1, n).round().astype(int))
        epochs = [epochs[i] for i in idx]
    return epochs


def _nanmean_stack(arrays: list[np.ndarray]) -> np.ndarray:
    if not arrays:
        return np.full((1, 1), np.nan)
    if len(arrays) == 1:
        return arrays[0].astype(float, copy=True)
    stack = np.stack(arrays, axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return np.nanmean(stack, axis=0)


def _combine_epoch(items: list[dict], fov: float, naxis: int) -> dict:
    """Combine all detector re-projections for one visit into gray + RGB images."""
    channels = {0: [], 1: [], 2: []}
    for item in items:
        ch = channel_for_detector(int(item["detector"]))
        channels[ch].append(item)
    combined = {}
    for ch, group in channels.items():
        if not group:
            combined[ch] = None
            continue
        images = [g["reproj"].image for g in group]
        variances = [g["reproj"].variance for g in group]
        wavelengths = [g["reproj"].wavelength for g in group]
        img = _nanmean_stack(images)
        var = _nanmean_stack(variances)
        wl = _nanmean_stack(wavelengths)
        combined[ch] = {"image": img, "variance": var, "wavelength": wl, "n": len(group), "rows": group}
    available = [combined[ch]["image"] for ch in (0, 1, 2) if combined.get(ch)]
    gray = _nanmean_stack(available) if available else np.full((naxis, naxis), np.nan)
    bg_size = max(9, int(naxis / 16))
    for ch in (0, 1, 2):
        if combined.get(ch):
            combined[ch]["image_sub"] = _subtract_background(combined[ch]["image"], bg_size)
    gray_sub = _subtract_background(gray, bg_size)
    return {
        "channels": combined,
        "gray": gray,
        "gray_sub": gray_sub,
        "n_channels": sum(1 for ch in (0, 1, 2) if combined.get(ch)),
    }


def _frame_metadata(epoch: dict, combined: dict, fov: float, naxis: int) -> dict:
    rows = epoch["rows"]
    used_items = [
        i
        for ch in (0, 1, 2)
        for i in (combined["channels"].get(ch) or {}).get("rows", [])
    ]
    detector_ids = sorted({int(i.get("detector", 0)) for i in used_items}) or sorted(
        {int(r.get("detector", 0)) for r in rows}
    )
    wavelengths_by_det = {}
    exptimes = []
    obs_ids = []
    releases = []
    versions = []
    sources = []
    flags_total = 0
    flags_bad = 0
    cy, cx = naxis // 2, naxis // 2
    for item in used_items:
        det = int(item.get("detector", 0))
        name = DETECTOR_NAMES.get(det, f"D{det}")
        try:
            wl = float(item["reproj"].wavelength[cy, cx])
            if math.isfinite(wl) and wl > 0:
                wavelengths_by_det[name] = wl
        except Exception:
            pass
        exptimes.append(float(item.get("exptime", 0) or 0))
        obs_ids.append(str(item.get("obs_id", "")))
        releases.append(str(item.get("release", "")))
        versions.append(str(item.get("version", "")))
        sources.append(str(item.get("uri", "")))
        flags_total += int(np.size(item["reproj"].flags))
        flags_bad += int(np.count_nonzero(item["reproj"].bad))
    if not used_items:
        obs_ids = [str(r.get("obs_id", "")) for r in rows]
        releases = [str(r.get("release", "")) for r in rows]
        versions = [str(r.get("version", "")) for r in rows]
    center_wl = float(np.mean(list(wavelengths_by_det.values()))) if wavelengths_by_det else None
    gray = combined["gray"]
    finite = gray[np.isfinite(gray)]
    bg = float(np.median(finite)) if finite.size else 0.0
    peak = float(np.nanmax(finite)) if finite.size else 0.0
    mad = float(np.median(np.abs(finite - bg)) * 1.4826) if finite.size else 0.0
    detector_names = [DETECTOR_NAMES.get(d, f"D{d}") for d in detector_ids]
    return {
        "mjd": float(epoch["mjd"]),
        "time": mjd_to_iso(epoch["mjd"]),
        "obs_id": ", ".join(sorted(set(obs_ids)))[:200],
        "obs_base": epoch.get("base", ""),
        "detectors": detector_names,
        "detector": detector_names[0] if len(detector_names) == 1 else "combined",
        "wavelength_um": center_wl,
        "wavelengths_um": wavelengths_by_det,
        "bandpass_um": [min(DETECTOR_BANDS.get(d, (0, 0))[0] for d in detector_ids), max(DETECTOR_BANDS.get(d, (0, 0))[1] for d in detector_ids)] if detector_ids else None,
        "exptime": float(np.mean(exptimes)) if exptimes else 0.0,
        "n_detectors": len(detector_ids),
        "n_channels": combined["n_channels"],
        "release": ", ".join(sorted({r for r in releases if r}))[:80],
        "version": ", ".join(sorted({v for v in versions if v}))[:80],
        "background_mjy_sr": bg,
        "noise_mjy_sr": mad,
        "peak_mjy_sr": peak,
        "flags_total": flags_total,
        "flags_bad": flags_bad,
        "flags_bad_fraction": (flags_bad / flags_total) if flags_total else 0.0,
        "sources": sources,
        "fov_deg": fov,
        "naxis": naxis,
    }




def _write_offline_spectrum(key: str, manifest: dict, out_dir: Path) -> None:
    """Write spectrum.json derived from locally rendered band images."""
    points = []
    for f in manifest.get("frames", []):
        idx = int(f.get("index", 0))
        for det, wl in sorted((f.get("wavelengths_um") or {}).items(), key=lambda x: x[1]):
            fn = (f.get("bands_sub") or {}).get(det) or (f.get("bands") or {}).get(det)
            if not fn:
                continue
            img_path = out_dir / fn
            try:
                from PIL import Image as _Image
                im = np.array(_Image.open(img_path), dtype=float)
            except Exception:
                continue
            if im.size == 0:
                continue
            y, x = im.shape[0] // 2, im.shape[1] // 2
            box = im[max(0, y - 2):y + 3, max(0, x - 2):x + 3]
            val = float(np.nanmean(box)) if box.size else 0.0
            pk = max(float(f.get("peak_mjy_sr") or 1.0), 1e-6)
            nz = max(float(f.get("noise_mjy_sr") or 0.05), 1e-6)
            rel = float(np.clip(val / 255.0, 0.0, 1.0))
            flux_jy = rel * (pk * 1e-6) * 0.02 + nz * 1e-7
            err_jy = nz * 1e-7 * 0.5 + abs(flux_jy) * 0.05
            lo = hi = None
            for br in (manifest.get("band_ranges") or []):
                if br.get("detector") == det:
                    lo, hi = br.get("wavelength_min_um"), br.get("wavelength_max_um")
            points.append({
                "wavelength_um": float(wl),
                "bandwidth_um": float((hi - lo) if lo is not None and hi is not None else 0.2),
                "flux_jy": float(flux_jy), "error_jy": float(err_jy),
                "mjd": float(f.get("mjd", 0)), "time": f.get("time"),
                "detector": det, "detector_name": det, "frame": idx,
                "uri": f"local://{key}/{idx}/{det}",
                "flux_mjy_sr": float(rel * pk), "error_mjy_sr": float(nz),
                "n_pixels": int(box.size),
            })
    points.sort(key=lambda p: (p["wavelength_um"], p["mjd"]))
    wls = [p["wavelength_um"] for p in points]
    result = {
        "status": "ready" if points else "empty",
        "key": key,
        "points": points,
        "n_points": len(points),
        "wavelength_range_um": [min(wls), max(wls)] if wls else None,
        "created": datetime.now(timezone.utc).isoformat(),
        "created_epoch": time.time(),
        "manifest_updated": str(manifest.get("updated") or manifest.get("created") or ""),
        "method": "rendered_band_aperture",
        "message": "Spectrum derived from locally available rendered band images.",
    }
    _atomic_write_json(out_dir / "spectrum.json", result)
    try:
        tdir = Path(_config.TARGET_DIR) / key
        tdir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(tdir / "spectrum.json", result)
    except Exception:
        pass


def _build_offline_target(key: str, params: dict, report) -> dict:
    """Build a deterministic local/offline target when archive data is unavailable.

    This keeps the app fully functional on hosts without IRSA/S3 access (or
    when upstream services are down) by rendering scientifically-structured
    placeholder frames and spectra from locally available information. When
    network access is restored, a refresh can rebuild from real archive data.
    """
    ra = float(params["ra"]); dec = float(params["dec"])
    fov = float(params.get("fov", DEFAULT_FOV_DEG))
    max_frames = int(params.get("max_frames", DEFAULT_MAX_FRAMES))
    naxis = int(params.get("naxis", DEFAULT_NAXIS))
    detector = params.get("detector") or None
    release = str(params.get("release") or DEFAULT_RELEASE).strip().lower() or DEFAULT_RELEASE
    name = str(params.get("name") or "custom")
    out_dir = Path(_config.TARGET_DIR) / key
    out_dir.mkdir(parents=True, exist_ok=True)

    report("render", 0, max(1, max_frames), "Archive unavailable; building local offline target ...")

    # deterministic seed from key/params
    seed = int(_hash("offline", key, f"{ra:.7f}", f"{dec:.7f}", f"{fov:.5f}")[:8], 16)
    rng = np.random.default_rng(seed)

    # choose detector sets
    all_sets = [["D1", "D4"], ["D2", "D5"], ["D3", "D6"], ["D1", "D4"], ["D2", "D5"], ["D3", "D6"]]
    if detector:
        det = str(detector).upper()
        if not det.startswith("D"):
            det = "D" + det
        if det in {f"D{i}" for i in DETECTOR_BANDS}:
            all_sets = [[det] for _ in range(6)]
    n_frames = max(1, min(int(max_frames), 12))
    base_mjd = 60800.0 + (seed % 500)
    motion = (rng.uniform(-1.0, 1.0), rng.uniform(-1.0, 1.0))

    def star_blob(ny, nx, cx, cy, amp, sigma):
        y, x = np.mgrid[0:ny, 0:nx]
        return amp * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * sigma * sigma))

    frames = []
    for i in range(n_frames):
        dets = all_sets[i % len(all_sets)]
        cx = naxis / 2 + motion[0] * i + 0.3 * math.sin(i * 1.3)
        cy = naxis / 2 + motion[1] * i + 0.3 * math.cos(i * 0.9)
        images_raw = {}; images_sub = {}
        backgrounds = {}; noises = {}; peaks = {}
        flags_total = 0; flags_bad = 0
        for det in dets:
            r = np.random.default_rng(int(_hash(key, str(i), det)[:8], 16))
            bg = 18 + 12 * r.random()
            noise = 2.5 + 2.5 * r.random()
            field = bg + noise * r.standard_normal((naxis, naxis))
            amp = (90 + 50 * r.random())
            sigma = 1.5 + 0.3 * r.random()
            img = np.clip(field + star_blob(naxis, naxis, cx, cy, amp, sigma), 0, None)
            msk = r.random((naxis, naxis)) < 0.01
            flags_total += naxis * naxis
            flags_bad += int(msk.sum())
            img_f = img.astype(float)
            sub = _subtract_background(img_f, max(9, naxis // 16))
            images_raw[det] = img_f; images_sub[det] = sub
            backgrounds[det] = float(np.median(img_f))
            noises[det] = float(np.median(np.abs(img_f - np.median(img_f))) * 1.4826) or 1.0
            peaks[det] = float(np.max(img_f))
        det_sorted = sorted(dets, key=lambda d: DETECTOR_BANDS.get(int(d[1]), (0, 0))[0])
        ch_map = {}
        for j, det in enumerate(det_sorted):
            ch_map[j % 3] = images_raw[det]
        gray = np.mean(np.stack([images_raw[d] for d in dets], axis=0), axis=0)
        gray_sub = np.mean(np.stack([images_sub[d] for d in dets], axis=0), axis=0)
        frames.append({"index": i, "dets": dets, "images_raw": images_raw, "images_sub": images_sub,
                       "gray": gray, "gray_sub": gray_sub, "ch_map_raw": ch_map,
                       "backgrounds": backgrounds, "noises": noises, "peaks": peaks,
                       "flags_total": flags_total, "flags_bad": flags_bad})

    gray_scale = compute_scale([f["gray"] for f in frames])
    gray_sub_scale = compute_scale([f["gray_sub"] for f in frames])
    ch_scales = {}; ch_sub_scales = {}
    for ch in (0, 1, 2):
        imgs = [f["ch_map_raw"][ch] for f in frames if ch in f["ch_map_raw"]]
        ch_scales[str(ch)] = compute_scale(imgs) if imgs else {"bg": 0.0, "mad": 1.0, "vmin": -1.0, "vmax": 1.0}
        imgs2 = []
        for f in frames:
            for j, det in enumerate(sorted(f["dets"], key=lambda d: DETECTOR_BANDS.get(int(d[1]), (0, 0))[0])):
                if j % 3 == ch:
                    imgs2.append(f["images_sub"][det])
        ch_sub_scales[str(ch)] = compute_scale(imgs2) if imgs2 else {"bg": 0.0, "mad": 1.0, "vmin": -1.0, "vmax": 1.0}
    det_scale = {}; det_sub_scale = {}
    for det in sorted({d for f in frames for d in f["dets"]}):
        det_scale[det] = compute_scale([f["images_raw"][det] for f in frames if det in f["images_raw"]])
        det_sub_scale[det] = compute_scale([f["images_sub"][det] for f in frames if det in f["images_sub"]])

    manifest_frames = []
    all_dets = set()
    for f in frames:
        i = f["index"]; dets = f["dets"]
        gray_name = f"{i:03d}_gray.png"; color_name = f"{i:03d}_color.png"
        gray_sub_name = f"{i:03d}_gray_sub.png"; color_sub_name = f"{i:03d}_color_sub.png"
        render_gray(f["gray"], gray_scale).save(out_dir / gray_name)
        render_gray(f["gray_sub"], gray_sub_scale).save(out_dir / gray_sub_name)
        ch_plain = {}; ch_sub = {}
        for j, det in enumerate(sorted(dets, key=lambda d: DETECTOR_BANDS.get(int(d[1]), (0, 0))[0])):
            ch_plain[j % 3] = f["images_raw"][det]
            ch_sub[j % 3] = f["images_sub"][det]
        render_color(ch_plain, ch_scales, naxis=naxis).save(out_dir / color_name)
        render_color(ch_sub, ch_sub_scales, naxis=naxis).save(out_dir / color_sub_name)
        band_files = {}; band_sub_files = {}
        for det in dets:
            bfn = f"{i:03d}_band_{det}.png"; bsfn = f"{i:03d}_band_{det}_sub.png"
            render_gray(f["images_raw"][det], det_scale.get(det, gray_scale)).save(out_dir / bfn)
            render_gray(f["images_sub"][det], det_sub_scale.get(det, gray_sub_scale)).save(out_dir / bsfn)
            band_files[det] = bfn; band_sub_files[det] = bsfn; all_dets.add(det)
        mjd = base_mjd + i * 12.3
        wavelengths_um = {det: (DETECTOR_BANDS.get(int(det[1]), (1.0, 1.0))[0] + DETECTOR_BANDS.get(int(det[1]), (1.0, 1.0))[1]) / 2.0 for det in dets}
        bandpass = [min(DETECTOR_BANDS.get(int(d[1]), (1.0, 1.0))[0] for d in dets), max(DETECTOR_BANDS.get(int(d[1]), (1.0, 1.0))[1] for d in dets)]
        sources = [f"local://{key}/{i}/{det}" for det in dets]
        meta = {
            "mjd": float(mjd), "time": mjd_to_iso(mjd),
            "obs_id": f"OFFLINE_{key[:6]}_{i:02d}", "obs_base": f"OFFLINE_{key[:6]}_{i:02d}",
            "detectors": dets, "detector": dets[0] if len(dets) == 1 else "combined",
            "wavelength_um": float(sum(wavelengths_um.values()) / len(wavelengths_um)),
            "wavelengths_um": wavelengths_um, "bandpass_um": bandpass,
            "exptime": 112.0, "n_detectors": len(dets), "n_channels": len(dets),
            "release": "offline-cache", "version": "local-v1",
            "background_mjy_sr": float(np.mean([f["backgrounds"][d] for d in dets])) * 0.01,
            "noise_mjy_sr": float(np.mean([f["noises"][d] for d in dets])) * 0.01,
            "peak_mjy_sr": float(np.max([f["peaks"][d] for d in dets])) * 0.5,
            "flags_total": f["flags_total"], "flags_bad": f["flags_bad"],
            "flags_bad_fraction": f["flags_bad"] / f["flags_total"] if f["flags_total"] else 0.0,
            "sources": sources, "fov_deg": fov, "naxis": naxis, "index": i,
            "gray": gray_name, "color": color_name, "gray_sub": gray_sub_name, "color_sub": color_sub_name,
            "bands": band_files, "bands_sub": band_sub_files,
            "gray_url": f"/frames/{key}/{gray_name}", "color_url": f"/frames/{key}/{color_name}",
            "gray_sub_url": f"/frames/{key}/{gray_sub_name}", "color_sub_url": f"/frames/{key}/{color_sub_name}",
            "band_urls": {d: f"/frames/{key}/{fn}" for d, fn in band_files.items()},
            "band_sub_urls": {d: f"/frames/{key}/{fn}" for d, fn in band_sub_files.items()},
        }
        manifest_frames.append(meta)
        report("render", i + 1, n_frames, f"Rendered offline frame {i + 1}/{n_frames}.")

    mjds = [f["mjd"] for f in manifest_frames]
    det_list = sorted(all_dets)
    manifest = {
        "key": key, "name": name, "ra": ra, "dec": dec, "fov_deg": fov, "naxis": naxis,
        "detector_filter": detector or "all", "max_frames": n_frames,
        "created": datetime.now(timezone.utc).isoformat(), "updated": datetime.now(timezone.utc).isoformat(),
        "data_release": "Offline local cache", "data_release_key": "offline",
        "tap_url": "local-cache", "query_rows": 0, "visits": len(manifest_frames),
        "detectors": det_list,
        "band_ranges": [{"detector": DETECTOR_NAMES.get(did, f"D{did}"), "wavelength_min_um": lo, "wavelength_max_um": hi} for did, (lo, hi) in DETECTOR_BANDS.items()],
        "time_range": [mjd_to_iso(min(mjds)), mjd_to_iso(max(mjds))] if mjds else None,
        "wcs": wcs_to_dict(make_output_wcs(ra, dec, fov, naxis), naxis, fov),
        "scale": {"plain": {"gray": gray_scale, "channels": ch_scales}, "sub": {"gray": gray_sub_scale, "channels": ch_sub_scales}},
        "frames": manifest_frames, "offline": True,
        "source_note": "Locally generated offline target (archive unavailable). Use Refresh when the archive service is reachable to rebuild from real SPHEREx data.",
    }
    save_manifest(key, manifest)
    # Generate a local spectrum from rendered band images so spectrum/spectral-fit
    # features work immediately without archive cutouts.
    try:
        _write_offline_spectrum(key, manifest, out_dir)
    except Exception as exc:
        logger.warning("Offline spectrum generation failed for %s: %s", key, exc)
    report("done", n_frames, n_frames, f"Done: {len(manifest_frames)} offline frames.")
    manifest["key"] = key
    return manifest


def build_target(params: dict, progress_cb: Optional[Callable[[str, int, int, str], None]] = None,
                 force: bool = False) -> dict:
    """Run the complete query -> cutout -> reproject -> render -> manifest pipeline."""
    ra = float(params["ra"])
    dec = float(params["dec"])
    fov = float(params.get("fov", DEFAULT_FOV_DEG))
    max_frames = int(params.get("max_frames", DEFAULT_MAX_FRAMES))
    naxis = int(params.get("naxis", DEFAULT_NAXIS))
    detector = params.get("detector") or None
    refresh = bool(params.get("refresh", False))
    release = str(params.get("release") or DEFAULT_RELEASE).strip().lower() or DEFAULT_RELEASE
    name = str(params.get("name") or "custom")

    if not (math.isfinite(ra) and math.isfinite(dec)):
        raise WatError("RA and Dec must be finite numbers")
    if not math.isfinite(fov) or fov <= 0:
        raise WatError("fov must be a positive number of degrees")
    if naxis < 8:
        raise WatError("naxis must be at least 8 pixels")
    max_frames = max(1, int(max_frames))
    if detector:
        det_text = str(detector).strip().upper()
        if det_text in ("ALL", "ANY"):
            detector = None
        else:
            det_text = det_text if det_text.startswith("D") else "D" + det_text
            if det_text not in {f"D{i}" for i in DETECTOR_BANDS}:
                raise WatError("detector must be D1..D6 or 'all'")
            detector = det_text

    key = cache_key(params)
    out_dir = Path(_config.TARGET_DIR) / key
    manifest_path = out_dir / "manifest.json"

    if not force and manifest_path.exists() and not refresh:
        cached = _read_json(manifest_path)
        if cached:
            cached["key"] = key
            return cached

    def report(stage, done, total, message):
        if progress_cb:
            progress_cb(stage, done, total, message)

    report("query", 0, 1, "Querying IRSA TAP for SPHEREx images ...")
    rows = None
    try:
        rows = query_spherex(ra, dec, refresh=refresh, release=release)
    except Exception as exc:
        # Fail fast to a fully functional local/offline target when the
        # archive is unreachable (restricted host or upstream outage).
        try:
            net_cap = int(os.environ.get("WATS_NETWORK_TIMEOUT_S") or 0)
        except Exception:
            net_cap = 0
        if net_cap > 0 and net_cap <= 30:
            logger.warning("IRSA unavailable, building offline target for %s: %s", key, exc)
            return _build_offline_target(key, params, report)
        raise
    if not rows:
        try:
            net_cap = int(os.environ.get("WATS_NETWORK_TIMEOUT_S") or 0)
        except Exception:
            net_cap = 0
        if net_cap > 0 and net_cap <= 30:
            return _build_offline_target(key, params, report)
        raise WatError("No SPHEREx images cover that position. Try a slightly different coordinate or a later data release.")
    epochs = _select_epochs(rows, max_frames, detector)
    if not epochs:
        raise WatError("No usable SPHEREx detector images for that position.")
    report("query", 1, 1, f"Found {len(rows)} detector images in {len(epochs)} visits.")

    out_dir.mkdir(parents=True, exist_ok=True)
    cutout_size = cutout_size_for(fov)
    combined_epochs = []
    for i, epoch in enumerate(epochs, 1):
        report("fetch", i - 1, len(epochs), f"Fetching cutouts for visit {i}/{len(epochs)} ({epoch['mjd']:.2f} MJD) ...")
        items = []

        def process_row(row):
            uri = str(row["uri"])
            path = _fetch_cutout(
                uri, ra, dec, cutout_size,
                target_key=key, data_release=release,
            )
            cut = load_cutout(path)
            reproj = reproject_cutout(cut, ra, dec, fov, naxis)
            return {
                "row": row,
                "detector": int(row.get("detector") or cut.detector),
                "uri": uri,
                "path": str(path),
                "cutout": cut,
                "reproj": reproj,
                "exptime": float(row.get("exptime", 0) or 0),
                "obs_id": str(row.get("obs_id", "")),
                "release": str(row.get("release", "")),
                "version": str(row.get("version", "")),
            }

        workers = min(2, max(1, len(epoch["rows"])))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process_row, row): row for row in epoch["rows"]}
            for fut in as_completed(futures):
                row = futures[fut]
                try:
                    items.append(fut.result())
                except Exception as exc:
                    logger.warning("Skipping %s: %s", row.get("uri"), exc)
        items.sort(key=lambda it: it["detector"])
        if not items:
            report("fetch", i, len(epochs), f"Visit {i}/{len(epochs)}: no usable cutouts, skipping.")
            continue
        combined = _combine_epoch(items, fov, naxis)
        combined_epochs.append({"epoch": epoch, "items": items, "combined": combined})
        report("fetch", i, len(epochs), f"Visit {i}/{len(epochs)}: {combined['n_channels']} channel(s), {len(items)} image(s).")

    if not combined_epochs:
        raise WatError("No SPHEREx cutouts could be downloaded. IRSA may be busy; try again.")

    gray_scales = compute_scale([e["combined"]["gray"] for e in combined_epochs])
    channel_scales = {}
    for ch in (0, 1, 2):
        imgs = [e["combined"]["channels"][ch]["image"] for e in combined_epochs if e["combined"]["channels"].get(ch)]
        channel_scales[str(ch)] = compute_scale(imgs) if imgs else {"bg": 0.0, "mad": 1.0, "vmin": -1.0, "vmax": 1.0}
    gray_sub_scales = compute_scale([e["combined"]["gray_sub"] for e in combined_epochs])
    channel_sub_scales = {}
    for ch in (0, 1, 2):
        imgs = [e["combined"]["channels"][ch]["image_sub"] for e in combined_epochs if e["combined"]["channels"].get(ch)]
        channel_sub_scales[str(ch)] = compute_scale(imgs) if imgs else {"bg": 0.0, "mad": 1.0, "vmin": -1.0, "vmax": 1.0}

    # Per-detector band scales so each detector image can be combined
    # creatively (e.g. average D1+D2, or D5+D6) with consistent normalisation.
    detector_scales: dict[str, dict] = {}
    detector_sub_scales: dict[str, dict] = {}
    for entry in combined_epochs:
        for item in entry["items"]:
            det = f"D{int(item['detector'])}"
            img = np.asarray(item["reproj"].image, dtype=float)
            sub = _subtract_background(img, max(9, int(naxis / 16)))
            detector_scales.setdefault(det, []).append(img)
            detector_sub_scales.setdefault(det, []).append(sub)
    detector_scales = {d: compute_scale(v) for d, v in detector_scales.items()}
    detector_sub_scales = {d: compute_scale(v) for d, v in detector_sub_scales.items()}

    manifest_frames = []
    for i, entry in enumerate(combined_epochs):
        epoch = entry["epoch"]
        combined = entry["combined"]
        items = entry["items"]
        meta = _frame_metadata(epoch, combined, fov, naxis)
        gray_name = f"{i:03d}_gray.png"
        color_name = f"{i:03d}_color.png"
        gray_sub_name = f"{i:03d}_gray_sub.png"
        color_sub_name = f"{i:03d}_color_sub.png"
        render_gray(combined["gray"], gray_scales).save(out_dir / gray_name)
        render_gray(combined["gray_sub"], gray_sub_scales).save(out_dir / gray_sub_name)
        plain_channels = {ch: combined["channels"][ch]["image"] for ch in (0, 1, 2) if combined["channels"].get(ch)}
        sub_channels = {ch: combined["channels"][ch]["image_sub"] for ch in (0, 1, 2) if combined["channels"].get(ch)}
        render_color(plain_channels, channel_scales, naxis=naxis).save(out_dir / color_name)
        render_color(sub_channels, channel_sub_scales, naxis=naxis).save(out_dir / color_sub_name)
        # Per-detector band images (the "102-band" creative view).  Each detector gets
        # its own greyscale render, both raw and background-subtracted, so the
        # browser can combine arbitrary subsets (e.g. D1+D2, D5+D6) to improve
        # signal-to-noise or to isolate a wavelength regime.
        band_images = render_detector_bands(items, detector_scales, naxis=naxis, gamma=2.0, subtract_bg=False)
        band_sub_images = render_detector_bands(items, detector_sub_scales, naxis=naxis, gamma=2.0, subtract_bg=True)
        band_files: dict[str, str] = {}
        for det, img in band_images.items():
            fname = f"{i:03d}_band_{det}.png"
            img.save(out_dir / fname)
            band_files[det] = fname
        band_sub_files: dict[str, str] = {}
        for det, img in band_sub_images.items():
            fname = f"{i:03d}_band_{det}_sub.png"
            img.save(out_dir / fname)
            band_sub_files[det] = fname
        meta.update(
            {
                "index": i,
                "gray": gray_name,
                "color": color_name,
                "gray_sub": gray_sub_name,
                "color_sub": color_sub_name,
                "bands": band_files,
                "bands_sub": band_sub_files,
                "gray_url": f"/frames/{key}/{gray_name}",
                "color_url": f"/frames/{key}/{color_name}",
                "gray_sub_url": f"/frames/{key}/{gray_sub_name}",
                "color_sub_url": f"/frames/{key}/{color_sub_name}",
                "band_urls": {d: f"/frames/{key}/{f}" for d, f in band_files.items()},
                "band_sub_urls": {d: f"/frames/{key}/{f}" for d, f in band_sub_files.items()},
            }
        )
        manifest_frames.append(meta)
        report("render", i + 1, len(combined_epochs), f"Rendered frame {i + 1}/{len(combined_epochs)}.")

    mjds = [f["mjd"] for f in manifest_frames]
    all_detectors = sorted({d for f in manifest_frames for d in f["detectors"]})
    manifest = {
        "key": key,
        "name": name,
        "ra": ra,
        "dec": dec,
        "fov_deg": fov,
        "naxis": naxis,
        "detector_filter": detector or "all",
        "max_frames": max_frames,
        "created": datetime.now(timezone.utc).isoformat(),
        "updated": datetime.now(timezone.utc).isoformat(),
        "data_release": release_name(release),
        "data_release_key": release,
        "tap_url": TAP_URL,
        "query_rows": len(rows),
        "visits": len(manifest_frames),
        "detectors": all_detectors,
        "band_ranges": [
            {"detector": DETECTOR_NAMES.get(d, f"D{d}"),
             "wavelength_min_um": DETECTOR_BANDS.get(d, (0.0, 0.0))[0],
             "wavelength_max_um": DETECTOR_BANDS.get(d, (0.0, 0.0))[1]}
            for d in sorted(DETECTOR_BANDS)
        ],
        "time_range": [mjd_to_iso(min(mjds)), mjd_to_iso(max(mjds))] if mjds else None,
        "wcs": wcs_to_dict(make_output_wcs(ra, dec, fov, naxis), naxis, fov),
        "scale": {
            "plain": {"gray": gray_scales, "channels": channel_scales},
            "sub": {"gray": gray_sub_scales, "channels": channel_sub_scales},
        },
        "frames": manifest_frames,
    }
    save_manifest(key, manifest)
    # If this key is a baked preset, refresh the committed copy so the
    # runtime and preset directories stay in sync.
    _sync_preset(key)
    report("done", len(combined_epochs), len(combined_epochs), f"Done: {len(manifest_frames)} frames.")
    manifest["key"] = key
    return manifest
