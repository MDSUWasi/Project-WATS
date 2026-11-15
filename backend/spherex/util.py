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


def _network_timeout(default: int = 30) -> int:
    """Cap outbound HTTP timeouts so requests fail fast on restricted hosts.

    PythonAnywhere free-tier and other sandboxed environments often block
    outbound internet; a 300s urllib timeout per attempt causes the UI to hang
    indefinitely.  Respect WATS_REQUEST_TIMEOUT_S and WATS_NETWORK_TIMEOUT_S,
    and never exceed the caller's default by more than necessary.
    """
    import os

    for name in ("WATS_NETWORK_TIMEOUT_S", "WATS_REQUEST_TIMEOUT_S"):
        try:
            val = int(os.environ.get(name, ""))
            if val > 0:
                return min(default, max(5, val))
        except (TypeError, ValueError):
            pass
    return default


def _irsa_session():
    """Return a session-like object that connects to IRSA.

    By default uses a direct connection with an empty ProxyHandler, which
    bypasses host-level proxy interception.  When WATS_TAP_RELAY is set
    (e.g. on PythonAnywhere), all requests route through that CORS proxy
    so IRSA becomes reachable even when the host blocks direct access.
    """
    import os
    import socket
    import urllib.parse
    import urllib.request

    relay = os.environ.get("WATS_TAP_RELAY", "").strip()

    class _IrsaResponse:
        def __init__(self, response):
            self._response = response
            self.status_code = response.status
            self._content = None
            self.url = response.url

        @property
        def text(self):
            return self._response.read().decode("utf-8", errors="replace")

        @property
        def content(self):
            if self._content is None:
                self._content = self._response.read()
            return self._content

    class _Session:
        def _build(self, url, data=None):
            if relay:
                target = urllib.parse.quote(url, safe="")
                url = f"{relay.rstrip('/')}?url={target}"
            req = urllib.request.Request(url, data=data)
            req.add_header("User-Agent", "Project-WATS/1.0 (public SPHEREx blink comparator)")
            return req

        def _open(self, req, timeout):
            cap = _network_timeout(_timeout_to_int(timeout))
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            # Set a global default socket timeout as a safety net for
            # environments where urllib's timeout argument is not respected.
            old = socket.getdefaulttimeout()
            try:
                socket.setdefaulttimeout(cap)
                return _IrsaResponse(opener.open(req, timeout=cap))
            finally:
                socket.setdefaulttimeout(old)

        def post(self, url, data=None, timeout=60, **_kwargs):
            if isinstance(data, dict):
                data = urllib.parse.urlencode(data).encode("utf-8")
            req = self._build(url, data=data)
            return self._open(req, timeout)

        def get(self, url, timeout=60, **_kwargs):
            req = self._build(url)
            return self._open(req, timeout)

    return _Session()


def _timeout_to_int(timeout):
    """urllib wants a single int, but the callers pass requests-style tuples."""
    if isinstance(timeout, (tuple, list)):
        try:
            return int(max(float(t) for t in timeout if t is not None))
        except Exception:
            return 30
    try:
        return int(timeout)
    except Exception:
        return 30
