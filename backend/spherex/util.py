"""Shared low-level utilities used across the spherex package."""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, obj: Any) -> None:
    _atomic_write_bytes(path, json.dumps(obj, indent=2, default=_json_default).encode("utf-8"))


def _json_default(obj: Any) -> Any:
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    return str(obj)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _hash(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _as_float_array(value) -> np.ndarray:
    if isinstance(value, __import__("astropy").units.Quantity):
        return np.asarray(value.value, dtype=float)
    return np.asarray(value, dtype=float)


def _to_um(value) -> np.ndarray:
    """Convert an astropy spectral quantity/array to microns."""
    u = __import__("astropy").units
    if isinstance(value, u.Quantity):
        try:
            return np.asarray(value.to(u.um).value, dtype=float)
        except Exception:
            pass
    if hasattr(value, "to"):
        try:
            return np.asarray(value.to(u.um).value, dtype=float)
        except Exception:
            pass
    return np.asarray(value, dtype=float)


def _f(row: dict, key: str, default=None):
    try:
        value = row.get(key)
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default
