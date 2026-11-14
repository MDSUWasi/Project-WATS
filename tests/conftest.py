"""Pytest fixtures for Project WATS.

Provides offline fixtures (fake cutouts, manifests) so tests run without
network access to IRSA.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from backend.config import CACHE_DIR, TARGET_DIR
from backend.repo import init_db

FIXTURE_DIR = Path(__file__).parent / "fixtures"
TARGET_RA = 133.795096
TARGET_DEC = -7.245147


@pytest.fixture(scope="session", autouse=True)
def init_test_db():
    """Initialize the default test database once per session."""
    init_db()


@pytest.fixture
def clean_data(tmp_path, monkeypatch):
    """Point DATA_DIR/CACHE_DIR/TARGET_DIR/DB_PATH at a temp dir."""
    data_dir = tmp_path / "data"
    cache_dir = data_dir / "cache"
    target_dir = data_dir / "targets"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    import backend.config as cfg

    monkeypatch.setattr(cfg, "DATA_DIR", data_dir)
    monkeypatch.setattr(cfg, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(cfg, "TARGET_DIR", target_dir)
    monkeypatch.setattr(cfg, "DB_PATH", data_dir / "wats.db")
    init_db(data_dir / "wats.db")

    yield data_dir

    shutil.rmtree(data_dir, ignore_errors=True)


def _make_fits_cutout(tmp_path, detector=1, mjd=59000.0, naxis=128):
    """Create a minimal valid SPHEREx MEF cutout on disk."""
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [TARGET_RA, TARGET_DEC]
    wcs.wcs.crpix = [(naxis + 1.0) / 2.0, (naxis + 1.0) / 2.0]
    scale = 0.00125
    wcs.wcs.cd = np.array([[-scale, 0.0], [0.0, scale]], dtype=float)
    wcs.wcs.cunit = ["deg", "deg"]
    wcs.wcs.set()

    data = np.full((naxis, naxis), 0.1, dtype=np.float64)
    cy, cx = naxis // 2, naxis // 2
    data[cy - 2:cy + 3, cx - 2:cx + 3] = 2.0
    image_hdu = fits.ImageHDU(data, name="IMAGE", header=wcs.to_header())
    image_hdu.header["DETECTOR"] = detector
    image_hdu.header["MJD-OBS"] = mjd
    image_hdu.header["OBSID"] = f"obs{detector}_{int(mjd)}"
    hdu_flags = fits.ImageHDU(np.zeros((naxis, naxis), dtype=np.int64), name="FLAGS")
    hdu_var = fits.ImageHDU(np.full((naxis, naxis), 0.01, dtype=np.float64), name="VARIANCE")
    hdu_zodi = fits.ImageHDU(np.full((naxis, naxis), 0.05, dtype=np.float64), name="ZODI")
    hdul = fits.HDUList([
        fits.PrimaryHDU(),
        image_hdu,
        hdu_flags,
        hdu_var,
        hdu_zodi,
    ])
    path = tmp_path / f"cutout_D{detector}_{int(mjd)}.fits"
    hdul.writeto(path, overwrite=True)
    return path


@pytest.fixture
def fake_cutout(clean_data, tmp_path, monkeypatch):
    """Create a fake cached cutout and patch cutout fetching everywhere."""
    path = _make_fits_cutout(tmp_path)

    import backend.spherex.ibe as ibe_mod
    import backend.spherex.pipeline as pipemod
    import backend.spherex.spectrum as spectmod

    def fake_fetch(uri, ra, dec, size_deg, retries=4):
        return path

    monkeypatch.setattr(ibe_mod, "fetch_cutout", fake_fetch)
    monkeypatch.setattr(pipemod, "_fetch_cutout", fake_fetch)
    monkeypatch.setattr(spectmod, "_fetch_cutout", fake_fetch)
    return path


@pytest.fixture
def fake_tap_rows(monkeypatch):
    """Monkeypatch query_spherex to return fake rows without network access."""

    def fake_query(ra, dec, max_rows=5000, refresh=False, min_mjd=None, max_mjd=None, release=None):
        rows = []
        for i in range(3):
            mjd = 59000.0 + i
            rows.append({
                "uri": f"/spherex/dr1/level2_obs{i}_1D{i+1}_spx_v1-20240101.fits",
                "url": f"https://irsa.ipac.caltech.edu/spherex/dr1/level2_obs{i}_1D{i+1}_spx_v1-20240101.fits",
                "mjd": mjd,
                "time_bounds_lower": mjd,
                "time_bounds_upper": mjd + 1,
                "time": "2024-01-01T00:00:00Z",
                "detector": i + 1,
                "detector_name": f"D{i+1}",
                "bandpass": "",
                "wavelength_min_um": 1.0,
                "wavelength_max_um": 2.0,
                "exptime": 100.0,
                "obs_id": f"obs{i}",
                "obs_base": f"obs{i}",
                "dither": 1,
                "release": "dr1",
                "version": "v1-20240101",
            })
        return rows

    import backend.spherex as spherex_mod
    import backend.spherex.pipeline as pipemod
    monkeypatch.setattr(pipemod, "query_spherex", fake_query)
    monkeypatch.setattr(spherex_mod, "query_spherex", fake_query)
    return fake_query


@pytest.fixture
def app(clean_data):
    """Create the Flask app for testing against the isolated database."""
    from backend.app import create_app
    from backend.config import WatsConfig
    config = WatsConfig.free()
    config.debug = True
    return create_app(config)


@pytest.fixture
def client(app):
    """Flask test client."""
    return app.test_client()