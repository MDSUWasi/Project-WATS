# Thank You

Project WATS is a community-built research workbench. It exists only because
many people and organizations make space data and tools openly available. Thank
you to all of them.

---

## The SPHEREx mission

Thank you to the **SPHEREx** team and to **NASA's Jet Propulsion Laboratory**
(JPL) and **Caltech** for building the SPHEREx space telescope and for releasing
its spectral imaging data publicly. SPHEREx (Spectro-Photometer for the History
of the Universe, Epoch of Reionization and Ices Explorer) provides the
all-sky infrared spectral images that make every view in this workbench
possible.

## NASA/IPAC Infrared Science Archive (IRSA)

Thank you to **IRSA** for operating one of the most generous and reliable public
astronomy archives in the world. This project relies on IRSA's **TAP/ADQL**
service for observation metadata and its **IBE cutout service** for on-demand
image cutouts — all without requiring an account or an API key. The archive's
stability, documentation, and no-login public-access policy are what let a small
project like this exist.

## The Gaia mission and DPAC

Thank you to the **European Space Agency (ESA)** and the **Gaia Data Processing
and Analysis Consortium (DPAC)** for the Gaia DR3 catalogue, used here as an
astrometric overlay to cross-check moving-object candidates.

## The WISE / NEOWISE and CatWISE teams

Thank you to the **WISE/NEOWISE** mission and the **CatWISE2020** catalogue
team for the infrared proper-motion data that helps flag brown dwarfs and other
high-motion sources between SPHEREx visits.

## The open-source community

Thank you to the maintainers and contributors of the tools this workbench is
built on:

- **Flask**, **Requests**, **Astropy**, **NumPy**, **SciPy**, **Pillow** —
  the Python scientific stack that powers the backend and data processing.
- **pytest** — for making the codebase testable.
- **healpy** — for the reference HEALPix pixel-indexing implementation used by
  the sky-tile indexer (optional dependency).
- **Vite**, **uPlot**, **three.js**, **deck.gl**, **OpenSeadragon** — the
  frontend toolchain and rendering libraries declared for the optional bundled
  and whole-sky views.

These projects are released under open-source licenses (BSD, MIT, Apache-2.0,
GPL, and public-domain). Their licenses are summarized in
[CREDITS.md](CREDITS.md); the full license texts live with each project.

## The users

Finally, thank you to the astronomers, educators, students, and citizen
scientists who point this workbench at new fields. Every annotation, collection,
and discovery card shared through the public layers is a small contribution to
open astronomy.

---

*Project WATS · SPHEREx Research Analysis Workbench ·
<https://github.com/MDSUWasi/project-wats>*

*If you believe a credit here is missing or inaccurate, please open an issue or
pull request — we want this list to be complete and correct.*