"""Quality flags, completeness scoring, and service health."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.config import TAP_URL


def health() -> dict:
    return {
        "status": "ok",
        "service": "Project WATS",
        "version": "1.0",
        "data": "SPHEREx DR1 / QR3 via NASA/IPAC IRSA",
        "tap": TAP_URL,
        "time": datetime.now(timezone.utc).isoformat(),
    }
