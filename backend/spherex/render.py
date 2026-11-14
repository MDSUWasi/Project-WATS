"""Gray/color PNG rendering and display-scale computation."""

from __future__ import annotations

import math
import warnings
from typing import Iterable

import numpy as np
from PIL import Image
from scipy.ndimage import median_filter

from backend.config import CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED


def _subtract_background(img: np.ndarray, size: int = 15) -> np.ndarray:
    """Flatten large-scale detector/zodiacal structure for display."""
    if img is None:
        return img
    finite = np.isfinite(img)
    if not np.any(finite):
        return img
    fill = float(np.nanmedian(img[finite]))
    filled = np.where(finite, img, fill)
    size = max(5, int(size) | 1)
    bg = median_filter(filled, size=size, mode="nearest")
    return img - bg


def compute_scale(images: Iterable[np.ndarray], sample: int = 60000) -> dict:
    """Robust global display scale from a set of images."""
    vals = []
    for img in images:
        if img is None:
            continue
        arr = np.asarray(img, dtype=float).ravel()
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            continue
        if arr.size > sample:
            idx = np.linspace(0, arr.size - 1, sample).astype(int)
            arr = arr[idx]
        vals.append(arr)
    if not vals:
        return {"bg": 0.0, "mad": 1.0, "vmin": -1.0, "vmax": 1.0}
    allv = np.concatenate(vals)
    bg = float(np.median(allv))
    mad = float(np.median(np.abs(allv - bg)) * 1.4826)
    if not math.isfinite(mad) or mad <= 0:
        mad = float(np.std(allv)) or 1.0
    hi = float(np.nanpercentile(allv, 99.0))
    lo = float(np.nanpercentile(allv, 1.0))
    if not math.isfinite(hi) or hi <= bg:
        hi = float(np.max(allv))
    if hi <= bg:
        hi = bg + 5.0 * mad
    vmin = min(lo, bg - 3.0 * mad)
    return {"bg": bg, "mad": mad, "vmin": vmin, "vmax": hi}


def _norm_channel(img: np.ndarray, scale: dict, gamma: float = 1.0) -> np.ndarray:
    bg = float(scale.get("bg", 0.0))
    vmax = float(scale.get("vmax", 1.0))
    if not math.isfinite(vmax) or vmax <= bg:
        vmax = bg + 1.0
    v = (np.asarray(img, dtype=float) - bg) / (vmax - bg)
    v = np.clip(v, 0.0, 1.0)
    if gamma and gamma != 1.0:
        v = np.power(v, 1.0 / float(gamma))
    return v


def render_gray(img: np.ndarray, scale: dict, gamma: float = 2.0) -> Image.Image:
    v = _norm_channel(img, scale, gamma)
    v = np.nan_to_num(v, nan=0.0, posinf=1.0, neginf=0.0)
    return Image.fromarray((np.clip(v, 0, 1) * 255.0 + 0.5).astype(np.uint8), mode="L")


def render_color(channels: dict, scales: dict, gamma: float = 2.0) -> Image.Image:
    """channels: {0: blue_image, 1: green_image, 2: red_image}."""
    out = np.zeros((320, 320, 3), dtype=float)
    for ch in (CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED):
        img = channels.get(ch)
        if img is not None:
            out = np.zeros((img.shape[0], img.shape[1], 3), dtype=float)
            break
    for ch in (CHANNEL_BLUE, CHANNEL_GREEN, CHANNEL_RED):
        img = channels.get(ch)
        if img is None:
            continue
        scale = scales.get(str(ch)) or scales.get(ch) or {"bg": 0.0, "vmax": 1.0}
        v = _norm_channel(img, scale, gamma)
        out[..., ch] = np.nan_to_num(v, nan=0.0)
    rgb = np.clip(out, 0, 1)
    return Image.fromarray((rgb * 255.0 + 0.5).astype(np.uint8), mode="RGB")
