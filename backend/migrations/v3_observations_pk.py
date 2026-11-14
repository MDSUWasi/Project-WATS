"""v3: make the observations primary key composite (obs_id, detector).

The original schema used ``obs_id TEXT PRIMARY KEY``, but a single SPHEREx
observation (one pointing) is delivered as several detector rows (D1..D6).  With
``obs_id`` alone as the key, ``upsert_observation`` silently overwrote all but
the last detector for every observation.  Rebuild the table with a composite
primary key so each (obs_id, detector) pair is preserved.
"""

SCHEMA = """
CREATE TABLE observations_new (
    obs_id          TEXT NOT NULL,
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
    version         TEXT,
    PRIMARY KEY (obs_id, detector)
);
INSERT INTO observations_new
    (obs_id, obs_base, dither, detector, mjd, healpix, footprint_wkt,
     exptime, quality, uri, release, version)
SELECT
    obs_id, obs_base, dither, detector, mjd, healpix, footprint_wkt,
    exptime, quality, uri, release, version
FROM observations;
DROP TABLE observations;
ALTER TABLE observations_new RENAME TO observations;
CREATE INDEX IF NOT EXISTS idx_obs_healpix ON observations(healpix);
CREATE INDEX IF NOT EXISTS idx_obs_detector ON observations(detector);
CREATE INDEX IF NOT EXISTS idx_obs_obs_id ON observations(obs_id);
"""

MIGRATIONS = [
    ("0003_observations_composite_pk", SCHEMA),
]