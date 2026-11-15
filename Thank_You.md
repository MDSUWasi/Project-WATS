# Thank You

Project WATS is made possible by public astronomical data, open infrastructure,
and open-source software. We are grateful to the people, missions, archives, and
communities that make this work possible.

Last updated: 2026-11-15.

---

## Data providers & missions

- **NASA/IPAC Infrared Science Archive (IRSA)** — for serving SPHEREx data
  products, TAP/ADQL query services, and cutout access that power WATS target
  discovery, frame retrieval, and analysis.
  <https://irsa.ipac.caltech.edu/>
- **SPHEREx mission** — *Spectro-Photometer for the History of the Universe,
  Epoch of Reionization and Ices Explorer* — for the public spectral imaging
  data at the heart of this project.
  <https://irsa.ipac.caltech.edu/Missions/spherex.html>
- **ESA Gaia mission and DPAC** — for Gaia DR3 astrometric and photometric data
  used in catalog overlays and cross-checks.
  <https://www.cosmos.esa.int/gaia>
- **WISE/NEOWISE and CatWISE2020 (via IRSA)** — for infrared catalog data and
  proper motions used in overlays and source context.
  <https://irsa.ipac.caltech.edu/Missions/wise.html>

Please see `CREDITS.md` for detailed source, fetch, format, usage, and
attribution information.

---

## Open-source software

Thank you to the maintainers and contributors of the libraries and tools WATS
depends on, including:

- **Python ecosystem:** Flask, requests, astropy, numpy, scipy, Pillow, pytest
- **Optional/build & frontend tooling:** Vite, uPlot, three.js, deck.gl,
  OpenSeadragon (as declared/used)
- **Platform foundations:** Python, SQLite, and the broader open-source
  scientific computing community

---

## Reliability & offline operation

WATS is designed to fail fast and remain usable when upstream services are slow
or unavailable (for example, during IRSA/S3 outages or on restricted hosts). The
app prioritizes locally cached/baked data and deterministic local fallbacks so
features like sky/blink, light curves, spectra, quality/completeness views, and
analysis remain operational; when archive access is restored, targets can be
refreshed from authoritative SPHEREx data.

Thank you to everyone building and maintaining the public data services that
make this possible when they are online, and to the open data practices that
allow resilient local use.
