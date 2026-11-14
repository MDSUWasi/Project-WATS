# Project WATS — SPHEREx Research Analysis Workbench

Project WATS turns SPHEREx spectral images of a sky patch into a blink
comparator and a small research workbench: spectra, light curves, source
extraction, annotations, collections, activity, moving-object candidates, and
export. It queries NASA/IPAC IRSA for public SPHEREx Level 2 observations,
downloads IBE cutouts, reprojects them onto a common north-up grid, and renders
gray/color frames in the browser.

## Features

- IRSA TAP (ADQL) query for SPHEREx `plane`/`artifact` rows and IBE cutout URLs
- **Data-release-aware caching**: choose SPHEREx DR1 / QR3 / QR2 from the query
  form or `?release=dr1` URL parameter. The cache key includes the release, so a
  new release never reuses a stale manifest, and the `GET /api/releases`
  endpoint advertises the known releases so the UI stays in sync without a
  code change.
- IBE cutout download with retries, deadlines, and a disk cache
- Astropy FITS reading, WCS handling, and `scipy.ndimage` reprojection
- Gray and color renders, background-subtracted frames, blink/subtract/sum, and
  stretch controls
- **Per-detector band images** (the "102-band" view): each of the six SPHEREx
  detectors is rendered as its own greyscale image (raw and background
  subtracted). In the Sky view, pick any subset of detectors and an average /
  sum / max merge to improve signal-to-noise or isolate a wavelength regime.
- **Spectral explorer**: hover any pixel on the sky canvas to extract its
  per-pixel WCS-WAVE spectrum on the fly, colored by detector, with interactive
  wavelength bins (`GET /api/analysis/pixel-spectrum`).
- **Ghost-trail difference view**: a toggle that accumulates frame-to-frame
  differences so a moving object leaves a visible trail across visits.
- **AI-assisted moving-object detection & trajectory prediction**: the
  `POST /api/analysis/moving-objects` endpoint detects candidates across
  consecutive frames, fits a linear trajectory per cluster, predicts future
  positions, and auto-classifies each candidate (asteroid / brown dwarf /
  variable star / artifact) from motion, flux trend, and catalog match.
- **Community annotation layer**: shift-drag on the sky canvas to draw a box
  around a suspect; annotations are stored per target and shared via the
  public annotations API (citizen-science layer).
- **Shareable discovery cards**: one click generates a tweet-ready PNG card
  with the current frame, coordinates, detector list, release, and visit time.
- **Dark mode & accessibility**: a display-mode toggle cycles Default / IR
  grayscale / High contrast; the grayscale mode is pure black-and-white for
  infrared work. Includes `prefers-reduced-motion` support and alt-text on
  canvases.
- **Watchlist & alerts**: save coordinates (`POST /api/public/watchlist`) and
  poll (`GET /api/public/watchlist/<id>/check`) for new SPHEREx visits since the
  last check; the watchlist view surfaces new-visit alerts.
- On-the-fly spectrum at the target position from cached cutouts
- Gaia DR3 and CatWISE2020 overlays
- SQLite-backed, resumable fetch jobs with cancel/resume and stale detection
- Target-scoped spectrum, light-curve, quality, source, completeness, difference,
  annotation, collection, activity, moving-object, and spectral-fit APIs
- Bookmarkable URLs and cached manifests for repeat loads
- Optional Vite build for a bundled frontend; the default Flask server serves
  unbundled ES modules directly

## Requirements

- Python 3.10+ with a virtual environment
- Network access to NASA/IPAC IRSA (no API key or sign-in)
- Python packages in `requirements.txt`: `flask`, `requests`, `astropy`, `numpy`,
  `scipy`, `Pillow`, `pytest`
- Node/npm only if you want to run or build the optional Vite frontend

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run the web app

```bash
. .venv/bin/activate
WATS_PROFILE=free python backend/app.py
# open http://localhost:5000
```

`WATS_PROFILE` selects the deployment profile:

- `free`: small cache, one worker, 6-frame default (default)
- `local`: larger cache, two workers, 12-frame default
- `self-host`: large cache, more workers, 96-frame default, optional tiles

Useful overrides: `WATS_CACHE_BUDGET_MB`, `WATS_MAX_WORKERS`,
`WATS_MAX_FRAMES_DEFAULT`, `WATS_REQUEST_TIMEOUT_S`, `PORT`.

Use a preset or enter RA/Dec (decimal degrees or sexagesimal) and click
**Load frames**. The first load for a new patch queries IRSA and downloads
cutouts; repeat loads use the cache. The **Refresh data** button re-queries IRSA.

URL parameters are supported for sharing:

```
http://localhost:5000/?ra=133.795096&dec=-7.245147&fov=0.15&max_frames=3&name=WISE%200855
```

Playback/stretch options can also be set in the URL (`color`, `subtract`,
`blink`, `sum`, `background`, `stretch`).

## Optional Vite frontend

The Flask server serves `frontend/src/` directly. If you want a bundled build:

```bash
cd frontend
npm install
npm run build
```

The build writes `frontend/dist/`; Flask serves that directory automatically
when it exists.

## CLI tools

Pre-fill a target without the browser:

```bash
. .venv/bin/activate
python scripts/fetch_frames.py --ra 133.795096 --dec -7.245147 --fov 0.15 --max-frames 3
python scripts/fetch_frames.py --preset WISE0855 --max-frames 3
python scripts/fetch_frames.py --list-presets
python scripts/fetch_frames.py --ra 133.795096 --dec -7.245147 --json
```

Other maintenance scripts:

- `scripts/ingest_region.py` — ingest an existing region/cache into the DB
- `scripts/prefetch.py` — prefetch preset or supplied targets
- `scripts/build_index.py` — build the sky-tile index
- `scripts/build_tiles.py` — build slippy/HEALPix tiles

Manifests and rendered frames are written under `data/targets/<key>/`; raw
cutouts are cached under `data/cache/`; job/target metadata is in
`data/wats.db`.

## API

Core fetch/status routes:

- `GET /api/health` — service/cache status
- `GET /api/presets` — preset target list
- `GET /api/frames?ra=<deg>&dec=<deg>&fov=<deg>&max_frames=<n>&name=<label>` —
  cached manifest, or starts a fetch and returns `202` with a `key`
- `GET /api/status?key=<key>` — fetch progress and final manifest
- `GET /api/cancel?key=<key>` — request cancellation for a running fetch
- `GET /api/spectrum?ra=<deg>&dec=<deg>&fov=<deg>&max_frames=<n>&name=<label>` —
  spectrum at the target position (`202` until frames are ready)
- `GET /api/overlays?ra=<deg>&dec=<deg>&fov=<deg>&naxis=<n>` — Gaia/CatWISE sources
- `GET /frames/<key>/<filename>` — rendered PNGs and `manifest.json`

Target, job, and analysis routes:

- `GET /api/coverage?ra=&dec=&fov=` — coverage/completeness estimates
- `POST /api/targets` — create/load a target
- `GET /api/targets/<key>` — manifest, coverage, completeness, release metadata
- `GET /api/targets/<key>/frames|spectrum|lightcurve|overlays|quality|completeness|difference|sources|export`
- `POST /api/targets/jobs` and `POST /api/targets/<key>/jobs` — start a job
- `GET /api/jobs`, `GET /api/jobs/<id>`, `POST /api/jobs/<id>/cancel`,
  `POST /api/jobs/<id>/resume`
- `GET/POST /api/public/targets/<key>/annotations`
- `GET/POST /api/public/collections`
- `POST /api/analysis/moving-objects`, `POST /api/analysis/spectral-fit`
- `GET /api/admin/cache`, `GET /api/admin/prefetch`, `GET /api/admin/index`
- `GET /api/tiles/...`

## Layout

- `backend/app.py` — Flask app factory, frontend serving, legacy fetch routes
- `backend/api/` — target, job, overlay, tile, admin, public, and analysis blueprints
- `backend/spherex/` — query, IBE download, FITS, reprojection, rendering,
  photometry, spectrum, quality, tiles, and pipeline helpers
- `backend/jobs.py` — resumable SQLite-backed job runner
- `backend/repo.py` — SQLite schema/migrations and repositories
- `backend/config.py` — profiles and paths
- `frontend/src/` — canonical HTML/CSS/ES-module frontend
- `frontend/dist/` — optional Vite build output
- `scripts/` — CLI fetch, ingest, prefetch, and tile/index tools
- `tests/` — offline pytest suite

## Tests

```bash
. .venv/bin/activate
python -m pytest -q
```

The suite uses temporary databases/caches and fake IRSA/cutout responses, so it
does not require network access.

## Notes

- SPHEREx Level 2 cutouts are multi-extension MEF files: `IMAGE`, `FLAGS`,
  `VARIANCE`, `ZODI`, `EPSF`, and `WCS-WAVE` per-detector wavelength tables.
- IRSA's IBE cutout service can be slow or return HTTP 503 under load. The
  fetcher retries, enforces a per-cutout deadline, and skips individual
  detectors that cannot be downloaded; the rest of the visit is still rendered.
- The spectrum is extracted from cached cutouts at the target position; it is
  not a full spectral cube. Each point is one detector's effective
  wavelength/bandpass for that visit.
- Cached cutouts, manifests, frames, tiles, and the SQLite DB are ignored by git.

## Data credits

- SPHEREx image data via [NASA/IPAC Infrared Science Archive (IRSA)](https://irsa.ipac.caltech.edu/)
- Ancillary catalogues: [Gaia DR3](https://www.cosmos.esa.int/web/gaia/dr3) and
  [CatWISE2020](https://irsa.ipac.caltech.edu/Missions/wise.html)

Please credit NASA/IPAC IRSA and the SPHEREx mission/data products per IRSA
guidance when using or presenting results.