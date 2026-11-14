"""Initial WATS schema (v1)."""

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS targets (
    key               TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    ra                REAL NOT NULL,
    dec               REAL NOT NULL,
    fov               REAL NOT NULL,
    naxis             INTEGER NOT NULL,
    detector_filter   TEXT DEFAULT 'all',
    max_frames        INTEGER NOT NULL,
    data_release      TEXT,
    created           TEXT NOT NULL,
    updated           TEXT NOT NULL,
    completeness_json TEXT
);

CREATE TABLE IF NOT EXISTS visits (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    target_key        TEXT NOT NULL REFERENCES targets(key) ON DELETE CASCADE,
    obs_id            TEXT NOT NULL,
    obs_base          TEXT,
    dither            INTEGER DEFAULT 0,
    detector          INTEGER NOT NULL,
    detector_name     TEXT,
    mjd               REAL NOT NULL,
    time_bounds_lower REAL,
    time_bounds_upper REAL,
    bandpass          TEXT,
    wavelength_min_um REAL,
    wavelength_max_um REAL,
    exptime           REAL DEFAULT 0,
    uri               TEXT NOT NULL,
    url               TEXT,
    release           TEXT,
    version           TEXT,
    fetch_status      TEXT DEFAULT 'pending',  -- pending|fetched|failed|skipped
    local_path        TEXT,
    quality_json      TEXT,
    created           TEXT NOT NULL,
    UNIQUE(target_key, obs_id, detector)
);

CREATE TABLE IF NOT EXISTS frames (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    target_key        TEXT NOT NULL REFERENCES targets(key) ON DELETE CASCADE,
    "index"           INTEGER NOT NULL,
    mjd               REAL NOT NULL,
    time              TEXT,
    obs_id            TEXT,
    obs_base          TEXT,
    detectors         TEXT,            -- JSON list of detector names
    n_detectors       INTEGER DEFAULT 0,
    n_channels        INTEGER DEFAULT 0,
    wavelength_um     REAL,
    wavelengths_json  TEXT,            -- JSON dict detector_name -> um
    bandpass_um_lo    REAL,
    bandpass_um_hi    REAL,
    exptime           REAL DEFAULT 0,
    release           TEXT,
    version           TEXT,
    gray_path         TEXT,
    color_path        TEXT,
    gray_sub_path     TEXT,
    color_sub_path    TEXT,
    scale_json        TEXT,            -- JSON per-channel display scales
    background_mjy_sr REAL DEFAULT 0,
    noise_mjy_sr      REAL DEFAULT 0,
    peak_mjy_sr       REAL DEFAULT 0,
    flags_total       INTEGER DEFAULT 0,
    flags_bad         INTEGER DEFAULT 0,
    flags_bad_fraction REAL DEFAULT 0,
    sources_json      TEXT,            -- JSON list of cutout URIs used
    fov_deg           REAL,
    naxis             INTEGER,
    created           TEXT NOT NULL,
    UNIQUE(target_key, "index")
);

CREATE TABLE IF NOT EXISTS spectra (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    target_key        TEXT NOT NULL REFERENCES targets(key) ON DELETE CASCADE,
    frame_index       INTEGER NOT NULL,
    detector          INTEGER NOT NULL,
    detector_name     TEXT,
    wavelength_um     REAL NOT NULL,
    bandwidth_um      REAL,
    flux_jy           REAL,
    error_jy          REAL,
    background_mjy_sr REAL,
    peak_mjy_sr       REAL,
    x                 INTEGER,
    y                 INTEGER,
    mjd               REAL,
    time              TEXT,
    obs_id            TEXT,
    UNIQUE(target_key, frame_index, detector)
);

CREATE TABLE IF NOT EXISTS sources (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    target_key        TEXT NOT NULL REFERENCES targets(key) ON DELETE CASCADE,
    frame_index       INTEGER NOT NULL,
    ra                REAL,
    dec               REAL,
    flux              REAL,
    flags             INTEGER DEFAULT 0,
    match_gaia        TEXT,
    match_catwise     TEXT,
    pm_ra             REAL,
    pm_dec            REAL,
    separation_arcsec REAL,
    x                 INTEGER,
    y                 INTEGER
);

CREATE TABLE IF NOT EXISTS overlays (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    target_key        TEXT NOT NULL REFERENCES targets(key) ON DELETE CASCADE,
    catalog           TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    ra                REAL NOT NULL,
    dec               REAL NOT NULL,
    mag               REAL,
    pm_ra             REAL,
    pm_dec            REAL,
    parallax          REAL,
    x                 INTEGER,
    y                 INTEGER,
    inside            INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    target_key        TEXT REFERENCES targets(key) ON DELETE CASCADE,
    type              TEXT NOT NULL,
    status            TEXT NOT NULL,
    stage             TEXT DEFAULT '',
    progress          INTEGER DEFAULT 0,
    total             INTEGER DEFAULT 0,
    message           TEXT DEFAULT '',
    cancel            INTEGER DEFAULT 0,
    params_json       TEXT,
    error             TEXT,
    created           TEXT NOT NULL,
    updated           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cache_entries (
    path          TEXT PRIMARY KEY,
    bytes         INTEGER NOT NULL DEFAULT 0,
    last_used     REAL NOT NULL,
    pin           INTEGER DEFAULT 0,
    target_key    TEXT,  -- references targets(key) but not enforced (shared cache)
    data_release  TEXT,
    created       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coverage (
    target_key            TEXT PRIMARY KEY REFERENCES targets(key) ON DELETE CASCADE,
    available_visits      INTEGER DEFAULT 0,
    fetched_visits        INTEGER DEFAULT 0,
    available_detectors   INTEGER DEFAULT 0,
    fetched_detectors     INTEGER DEFAULT 0,
    bands_covered         INTEGER DEFAULT 0,
    quality_json          TEXT,
    completeness_score    REAL DEFAULT 0,
    components_json       TEXT,
    updated               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    obs_id          TEXT PRIMARY KEY,
    obs_base        TEXT,
    dither          INTEGER DEFAULT 0,
    detector        INTEGER NOT NULL,
    mjd             REAL,
    healpix         INTEGER,
    footprint_wkt   TEXT,
    exptime         REAL DEFAULT 0,
    quality         TEXT,
    uri             TEXT,
    release         TEXT,
    version         TEXT
);

CREATE INDEX IF NOT EXISTS idx_visits_target ON visits(target_key);
CREATE INDEX IF NOT EXISTS idx_visits_detector ON visits(target_key, detector);
CREATE INDEX IF NOT EXISTS idx_frames_target ON frames(target_key);
CREATE INDEX IF NOT EXISTS idx_spectra_target ON spectra(target_key);
CREATE INDEX IF NOT EXISTS idx_sources_target ON sources(target_key);
CREATE INDEX IF NOT EXISTS idx_overlays_target ON overlays(target_key);
CREATE INDEX IF NOT EXISTS idx_overlays_cat ON overlays(catalog);
CREATE INDEX IF NOT EXISTS idx_jobs_target ON jobs(target_key);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_cache_target ON cache_entries(target_key);
CREATE INDEX IF NOT EXISTS idx_cache_last_used ON cache_entries(last_used);
CREATE INDEX IF NOT EXISTS idx_obs_healpix ON observations(healpix);
CREATE INDEX IF NOT EXISTS idx_obs_detector ON observations(detector);

-- Public annotations no accounts keyed by target_key visible to all
CREATE TABLE IF NOT EXISTS annotations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    target_key    TEXT NOT NULL REFERENCES targets(key) ON DELETE CASCADE,
    author        TEXT DEFAULT 'anonymous',
    body          TEXT NOT NULL,
    created       TEXT NOT NULL,
    updated       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_annotations_target ON annotations(target_key);

-- Saved collections shareable via URL stored server-side public
CREATE TABLE IF NOT EXISTS collections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    target_keys   TEXT NOT NULL,   -- JSON list of target keys
    created       TEXT NOT NULL,
    updated       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_collections_updated ON collections(updated);
"""

MIGRATIONS = [
    ("0001_initial", SCHEMA),
]
