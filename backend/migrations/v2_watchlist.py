"""v2: add watchlist table for saved-coordinate alerts."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ra            REAL NOT NULL,
    dec           REAL NOT NULL,
    fov           REAL DEFAULT 0.15,
    name          TEXT DEFAULT 'custom',
    release       TEXT DEFAULT 'dr1',
    max_frames    INTEGER DEFAULT 6,
    last_check    TEXT,
    last_visit_mjd REAL,
    created       TEXT NOT NULL,
    updated       TEXT NOT NULL,
    UNIQUE(ra, dec, fov, release)
);
CREATE INDEX IF NOT EXISTS idx_watchlist_updated ON watchlist(updated);
"""

MIGRATIONS = [
    ("0002_watchlist", SCHEMA),
]