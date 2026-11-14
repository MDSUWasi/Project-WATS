# Project WATS — SPHEREx Blink Comparator

A small web tool that turns SPHEREx spectral images of a sky patch into a
time-lapse / blink comparator, so moving and variable objects stand out.

Given an RA/Dec (or one of the preset targets) the backend queries NASA/IPAC
IRSA for SPHEREx Level 2 observations, downloads small IBE cutouts, reprojects
them onto a common north-up tangent-plane grid, and renders gray/color frames.
The frontend adds blink, subtract, sum, a stretch control, an on-the-fly
spectrum at the target position, and Gaia DR3 / CatWISE2020 overlays.

## Features

- IRSA TAP (ADQL) query for SPHEREx `plane`/`artifact` rows and IBE cutout URLs
- IBE cutout download with retries, a per-cutout deadline, and a disk cache
- Astropy FITS reading, WCS handling, and `scipy.ndimage` reprojection onto a
  common TAN grid (north up, east left)
- Gray (all available detectors) and color (D1/D2 blue, D3 green, D4–D6 red)
  renders, plus background-subtracted versions
- Blink A/B, subtract (this frame minus the previous frame), sum-all, and a
  gamma/stretch slider
- On-the-fly spectrum at the target position from the cached cutouts
- Gaia DR3 and CatWISE2020 overlays with a source table
- Preset targets: WISE 0855, Barnard's Star, Epsilon Indi B, RW Aurigae, R Draconis
- URL parameters for bookmarkable views; cached manifests make repeat loads fast
- Flask API for scripts and embedding; CLI fetch script for pre-filling caches

## Requirements

- Python 3.10+ with a virtual environment
- Network access to NASA/IPAC IRSA (no API key or sign-in)
- Packages in `requirements.txt`: `flask`, `requests`, `astropy`, `numpy`,
  `scipy`, `Pillow`

## Setup

```bash
cd project-wats
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run the web app

```bash
. .venv/bin/activate
python backend/app.py
# open http://localhost:5000
```

Use a preset button, or enter RA/Dec (decimal degrees or sexagesimal) and click
**Load frames**. The first load for a new patch queries IRSA and downloads
cutouts; this can take a few minutes. The default is 6 frames, which is enough
for a blink; 3 frames loads faster. If IRSA is busy, press **Cancel** and try
again with fewer frames. Repeat loads use the cache.

The **Refresh data** button re-queries IRSA and can fill in detectors that were
skipped during a busy period.

URL parameters are supported for sharing:

```
http://localhost:5000/?ra=133.795096&dec=-7.245147&fov=0.15&max_frames=3&name=WISE%200855
```

Playback/stretch options can also be set in the URL (`color`, `subtract`,
`blink`, `sum`, `background`, `stretch`).

## CLI fetch

Pre-fill a target without the browser:

```bash
. .venv/bin/activate
python scripts/fetch_frames.py --ra 133.795096 --dec -7.245147 --fov 0.15 --max-frames 3
python scripts/fetch_frames.py --preset WISE0855 --max-frames 3
python scripts/fetch_frames.py --list-presets
python scripts/fetch_frames.py --ra 133.795096 --dec -7.245147 --json
```

Manifests and rendered frames are written under `data/targets/<key>/`; raw
cutouts are cached under `data/cache/`. Both are ignored by git.

## API

- `GET /api/health` — cache/target status and active fetch count
- `GET /api/presets` — preset target list
- `GET /api/frames?ra=<deg>&dec=<deg>&fov=<deg>&max_frames=<n>&name=<label>` —
  returns a cached manifest, or starts a fetch and returns `202` with a `key`
- `GET /api/status?key=<key>` — fetch progress and final manifest
- `GET /api/spectrum?ra=<deg>&dec=<deg>&fov=<deg>&max_frames=<n>&name=<label>` —
  spectrum at the target position (returns `202` until frames are ready)
- `GET /api/overlays?ra=<deg>&dec=<deg>&fov=<deg>&naxis=<n>` — Gaia/CatWISE sources
- `GET /frames/<key>/<filename>` — rendered PNGs and `manifest.json`

## Layout

- `backend/spherex.py` — TAP/IBE fetching, reprojection, rendering, spectrum,
  overlays, manifest helpers
- `backend/app.py` — Flask app, fetch registry, API routes
- `frontend/` — static HTML/CSS/JS (vanilla, no build step)
- `scripts/fetch_frames.py` — CLI wrapper
- `app.py` — thin launcher (`python app.py`)

## Notes

- SPHEREx Level 2 cutouts are multi-extension MEF files: `IMAGE`, `FLAGS`,
  `VARIANCE`, `ZODI`, `EPSF`, and `WCS-WAVE` per-detector wavelength tables.
- IRSA's IBE cutout service can be slow or return HTTP 503 under load. The
  fetcher retries, enforces a per-cutout deadline, and skips individual
  detectors that cannot be downloaded; the rest of the visit is still rendered.
  A frame with fewer detectors is marked accordingly in the metadata.
- The spectrum is extracted from the cached cutouts at the target position; it
  is not a full spectral cube. Each point is one detector's effective
  wavelength/bandpass for that visit.
- Cached cutouts, manifests, and rendered frames are ignored by `.gitignore`.

## Data credits

- SPHEREx image data via [NASA/IPAC Infrared Science Archive (IRSA)](https://irsa.ipac.caltech.edu/)
- Ancillary catalogues: [Gaia DR3](https://www.cosmos.esa.int/web/gaia/dr3) and
  [CatWISE2020](https://irsa.ipac.caltech.edu/Missions/wise.html)

Please credit NASA/IPAC IRSA and the SPHEREx mission/data products per IRSA
guidance when using or presenting results.
