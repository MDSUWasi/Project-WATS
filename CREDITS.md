# Credits & Data Sources

Project WATS is built on public astronomical data and open-source software. This
document records **every external data source and service** used, exactly where
it comes from, how it is fetched, what format it is in, and how Project WATS uses
it — plus the attribution each source requires.

Last reviewed: 2026-11-15.

---

## 1. Astronomical data

### 1.1 SPHEREx spectral images (primary data)

| Field | Value |
| --- | --- |
| **Provider** | NASA/IPAC Infrared Science Archive (IRSA), on behalf of the SPHEREx mission |
| **Mission** | SPHEREx — Spectro-Photometer for the History of the Universe, Epoch of Reionization and Ices Explorer |
| **Data level used** | Level 2 (`l2b`) spectral images (multi-extension FITS) |
| **Data releases** | DR1 (default), QR3, QR2 — see `SPHEREx_RELEASES` in `backend/config.py` |
| **Location / landing page** | <https://irsa.ipac.caltech.edu/Missions/spherex.html> |
| **Archive TAP service** | <https://irsa.ipac.caltech.edu/TAP/sync> (Synchronous TAP, ADQL) |
| **Cutout (image) service** | <https://irsa.ipac.caltech.edu/spherex/...> (IRSA IBE cutout endpoint) |
| **Format** | FITS Multi-Extension File (MEF): `IMAGE`, `FLAGS`, `VARIANCE`, `ZODI`, `EPSF`, and a per-detector `WCS-WAVE` wavelength table |
| **Detector bands** | D1 0.75–1.10 µm, D2 1.10–1.62 µm, D3 1.63–2.41 µm, D4 2.42–3.82 µm, D5 3.83–4.41 µm, D6 4.42–5.00 µm |

**Fetch method — two steps:**

1. **Metadata / cross-match (TAP + ADQL).** `backend/spherex/query.py` runs an
   ADQL query against the IRSA TAP endpoint (`POST` with
   `REQUEST=doQuery&LANG=ADQL&FORMAT=csv`) joining `spherex.artifact` with
   `spherex.plane` and filtering on `CONTAINS(POINT('ICRS', ra, dec), p.poly)`
   to find every detector MEF whose footprint contains the target. No API key or
   account is required.
   ```sql
   SELECT TOP <n> a.uri, p.time_bounds_lower, p.time_bounds_upper,
          p.energy_bandpassname, p.energy_bounds_lower,
          p.energy_bounds_upper, p.time_exposure
   FROM spherex.artifact a
   JOIN spherex.plane p ON a.planeid = p.planeid
   WHERE 1 = CONTAINS(POINT('ICRS', <ra>, <dec>), p.poly)
   ORDER BY p.time_bounds_lower
   ```
2. **Cutout download (HTTP GET).** `backend/spherex/ibe.py` builds an IBE cutout
   URL from each artifact URI — appending
   `center=<ra>,<dec>&size=<deg>&gzip=0` — and downloads the FITS with retries,
   a per-cutout deadline, and a disk cache. Downloads use a descriptive
   `User-Agent` (`Project-WATS/1.0`).

**How the data is used:** reprojected onto a common north-up grid
(`scipy.ndimage`, WCS via `astropy.wcs`) and rendered as gray/colour blink
frames, background-subtracted frames, per-detector band images, per-pixel
WCS-WAVE spectra, light curves, quality masks, and moving-object candidate
detection. See `backend/spherex/` (query, ibe, fits, reproject, render,
spectrum, pipeline).

**Attribution required:** Please credit **NASA/IPAC IRSA** and the **SPHEREx
mission** when using or presenting results, per IRSA guidance.

---

### 1.2 Gaia DR3 (astrometric overlay)

| Field | Value |
| --- | --- |
| **Provider** | ESA Gaia mission (Gaia Data Processing and Analysis Consortium), served via NASA/IPAC IRSA TAP |
| **Catalogue** | `gaia.gaia_dr3_source` (Gaia Data Release 3) |
| **Catalogue home** | <https://www.cosmos.esa.int/web/gaia/dr3> |
| **Archive (via IRSA TAP)** | <https://irsa.ipac.caltech.edu/TAP/sync> (table `gaia.gaia_dr3_source`) |
| **Format** | ADQL/TAP tabular (CSV returned), columns: `source_id, ra, dec, phot_g_mean_mag, bp_rp, pmra, pmdec, parallax` |
| **Fetch method** | TAP + ADQL cone search (`CONTAINS(POINT('ICRS',...), CIRCLE('ICRS', ra, dec, r))`), `TOP 600`, `phot_g_mean_mag < 20` |

**How the data is used:** Gaia DR3 sources are drawn as an overlay on the sky
view (Gaia toggle) to cross-check proper motions, parallaxes, and magnitudes of
candidate moving objects. Implementation: `backend/spherex/catalogs.py`
(`fetch_overlays`).

**Attribution:** This work has made use of data from the European Space Agency
(ESA) mission Gaia (<https://www.cosmos.esa.int/gaia>), processed by the Gaia
Data Processing and Analysis Consortium (DPAC). Funding for DPAC has been
provided by national institutions, in particular those participating in the Gaia
Multilateral Agreement.

---

### 1.3 CatWISE2020 (infrared proper-motion overlay)

| Field | Value |
| --- | --- |
| **Provider** | NASA/IPAC Infrared Science Archive (IRSA), from the WISE/NEOWISE mission data |
| **Catalogue** | `catwise_2020` (CatWISE2020) |
| **Catalogue home** | <https://irsa.ipac.caltech.edu/Missions/wise.html> |
| **Archive (via IRSA TAP)** | <https://irsa.ipac.caltech.edu/TAP/sync> (table `catwise_2020`) |
| **Format** | ADQL/TAP tabular (CSV returned), columns: `source_id, ra, dec, w1mpro, w2mpro, pmra, pmdec` |
| **Fetch method** | TAP + ADQL cone search (`CONTAINS(... CIRCLE ...)`), `TOP 600`, `w1mpro < 18` |

**How the data is used:** CatWISE2020 proper motions and W1/W2 magnitudes are
overlaid (CatWISE toggle) to help identify high-proper-motion infrared sources
(e.g. brown dwarfs) that may move between SPHEREx visits. Implementation:
`backend/spherex/catalogs.py` (`fetch_overlays`).

**Attribution:** Please acknowledge the CatWISE2020 catalogue (Marocco et al.
2021) and the WISE/NEOWISE mission data via NASA/IPAC IRSA.

---

### 1.4 Local/offline cached and resilient data

To keep the application functional and fast on restricted hosts and during
upstream outages (for example when IRSA TAP/cutout services or S3 are
unreachable), Project WATS also uses **locally available data**:

- **Baked preset targets** in `data/presets/` (manifests, rendered gray/color
  and per-detector band frames) for the configured presets so core sky/blink,
  light-curve, quality, completeness, difference, and source features work
  without network access.
- **Locally derived spectra** (`spectrum.json`) generated from rendered band
  images for cached targets, enabling spectrum and spectral-fit views offline.
- **Cached query/overlay artifacts** in `data/cache/` and `data/targets/` when
  available; stale cached results may be served when live fetches fail.
- **Deterministic offline/local target generation** (`backend/spherex/pipeline.py`)
  as a fallback when archive services are unavailable, producing structured
  local frames, manifests, and spectra so the UI remains fully operational.
  These offline targets are clearly labeled in metadata and should be refreshed
  from real SPHEREx archive data when IRSA is reachable.

These fallbacks do **not** replace or relicense upstream data; when network
access is available, WATS queries and renders data from the authoritative
sources listed above.

---

## 2. External software & services

All third-party packages are used under their own open-source licenses. Project
WATS itself imposes no additional restrictions. Listed per the license each
project publishes.

### 2.1 Python packages (`requirements.txt`)

| Package | License | Used for |
| --- | --- | --- |
| **Flask** | BSD-3-Clause | HTTP API server and static frontend serving (`backend/app.py`, `backend/api/`) |
| **requests** | Apache-2.0 | IRSA TAP and IBE cutout HTTP requests (`backend/spherex/query.py`, `ibe.py`) |
| **astropy** | BSD-3-Clause | FITS I/O, WCS handling, coordinate transforms (`backend/spherex/fits.py`, time/WCS utilities) |
| **numpy** | BSD-3-Clause | Array math, reprojection, photometry, rendering (`backend/spherex/`) |
| **scipy** | BSD-3-Clause | `scipy.ndimage` reprojection/resampling and filtering (`backend/spherex/reproject.py`, `render.py`) |
| **Pillow** | MIT-CMU (HPND) | PNG frame/card rendering (`backend/spherex/render.py`) |
| **pytest** | MIT | Test suite (`tests/`) |
| **healpy** *(optional)* | GPL-2.0-only | HEALPix pixel indexing in `scripts/build_index.py`; optional — falls back to a pure-Python implementation when not installed |

### 2.2 Frontend libraries (`frontend/package.json`)

These are declared for the optional Vite build. The default Flask server serves
unbundled ES modules directly, and the shipped views use **native Canvas 2D**
(JavaScript) — no external runtime charting library is required.

| Package | License | Intended use |
| --- | --- | --- |
| **Vite** | MIT | Build tool / dev server for the optional bundled frontend |
| **uPlot** | MIT | Fast canvas time-series plotting (declared for future use; charts currently render on native canvas) |
| **three.js** | MIT | WebGL 3D rendering (declared for future use) |
| **deck.gl** | MIT | GPU whole-sky tile / scatter layer in the "Whole-sky" view (`sky_deckgl.js`; optional, loaded only if installed) |
| **OpenSeadragon** | BSD-3-Clause | Deep-zoom tile pyramid in the "Whole-sky" view (`sky_deckgl.js`; optional, loaded only if installed) |

### 2.3 Standard / platform software (not bundled, not distributed by this project)

- **Python** — PSF license (via python.org)
- **SQLite** — public domain
- **Node.js / npm** — MIT (only needed to build the optional Vite frontend)
- **astropy ecosystem, NumPy/SciPy wheels** — as per their own licenses above

---

## 3. Fonts & assets

Project WATS uses **only system font stacks** (`ui-sans-serif`, `system-ui`,
`ui-monospace`); no web fonts are downloaded or bundled, so there is no font
license to attribute. All icons are inline SVG authored for this project and are
not third-party.

---

## 4. How to cite Project WATS itself

If you use Project WATS or its outputs in a publication or presentation, please
cite the data providers listed in Section 1 (IRSA/SPHEREx, Gaia, CatWISE2020)
and, where appropriate, this software repository:
<https://github.com/MDSUWasi/Project-WATS>.
