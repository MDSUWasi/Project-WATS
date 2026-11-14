"""HEALPix/COG tile builder (stub; self-hosted/batch only)."""

from __future__ import annotations

from pathlib import Path

from backend.config import TILES_DIR


def build_tiles() -> dict:
    """Placeholder.  Real tile generation lives in scripts/build_tiles.py."""
    TILES_DIR.mkdir(parents=True, exist_ok=True)
    return {"status": "not_implemented", "tiles_dir": str(TILES_DIR)}
