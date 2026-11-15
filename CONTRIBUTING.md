# Contributing to Project WATS

<b><i>This is a project created by `Team WATS` for `SPACE APP CHALLENGE 2026`. So, anyone who 
wants to contribute on ``Project WATS``, Please consider it after 01-February 2026.</i></b> 

Thank you for considering a contribution. This guide is for anyone who
wants to work with the source code, whether fixing a bug, adding a
feature, or improving documentation.

---

## ◆ Project Layout

```
backend/app.py       Flask app factory, frontend serving, legacy fetch routes
backend/api/         Target, job, overlay, tile, admin, public, and analysis blueprints
backend/spherex/     Query, IBE download, FITS, reprojection, rendering, photometry,
                     spectrum, quality, tiles, and pipeline helpers
backend/jobs.py      Resumable SQLite-backed job runner
backend/repo.py      SQLite schema/migrations and repositories
backend/config.py    Profiles and paths
frontend/src/        Canonical HTML/CSS/ES-module frontend
frontend/dist/       Optional Vite build output (never hand-edit)
scripts/             CLI fetch, ingest, prefetch, and tile/index tools
tests/               Offline pytest suite
CREDITS.md           Data sources, external services, and license attribution
THANK_YOU.md         Acknowledgements
```

---

## ◆ Development Environment

Follow the standard setup from the README, then activate the virtualenv
for all development work:

```bash
. .venv/bin/activate
```

For frontend work on the bundled build:

```bash
cd frontend
npm install
npm run build
```

`frontend/src/` is the canonical source of truth. `frontend/dist/` is
build output only — do not commit changes made directly there.

---

## ◆ Running the Tests

The test suite runs fully offline:

```bash
. .venv/bin/activate
python -m pytest -q
```

It uses temporary databases and caches, plus fake IRSA/cutout responses,
so no network access is required. Please ensure the suite passes before
submitting a change, and add tests for any new behavior:

- Backend logic → add tests under `tests/`
- API endpoints → test the blueprint handlers with the fake IRSA fixtures
- Frontend changes → verify manually in the browser against the
  unbundled server

---

## ◆ Data Releases and Caching

Caching is data-release-aware. Key rules for contributors:

- The cache key **must** include the release identifier, so a new release
  never reuses a stale manifest.
- Known releases are advertised through `GET /api/releases`; update that
  list (not hardcoded UI) when a new release is added.
- Release choices come from the query form or the `?release=` URL
  parameter.

---

## ◆ Working with IRSA / IBE

- The IBE cutout service can be slow or return HTTP 503 under load. Any
  fetching code must follow the existing pattern: retries, a per-cutout
  deadline, and skipping individual detectors that fail while still
  rendering the rest of the visit.
- SPHEREx Level 2 cutouts are multi-extension MEF files with `IMAGE`,
  `FLAGS`, `VARIANCE`, `ZODI`, `EPSF`, and `WCS-WAVE` extensions. Handle
  all of these when touching FITS/WCS code.

---

## ◆ Data Storage Rules

- Manifests and rendered frames: `data/targets/<key>/`
- Raw cutouts: `data/cache/`
- Job/target metadata: `data/wats.db`
- Cached cutouts, manifests, frames, tiles, and the SQLite DB are ignored
  by git — never commit them.

---

## ◆ Submission Guidelines

1. Fork/branch and make focused changes.
2. Add or update tests for new behavior.
3. Run `python -m pytest -q` and ensure it passes.
4. Keep the public API surface consistent with the README (update the
   README if you add or change routes).
5. Update `CREDITS.md` if you introduce a new data source or external
   dependency.

---

## ◆ Licensing and Attribution

Project WATS uses only public data and open-source software. Any new
dependency must carry an open-source license compatible with the project,
and any new data source must be documented in `CREDITS.md` with provider,
links, data format, fetch method, and usage. Acknowledgements belong in
`THANK_YOU.md`.