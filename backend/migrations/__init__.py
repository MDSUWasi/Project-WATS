from backend.migrations.v1_initial import MIGRATIONS, SCHEMA  # noqa: F401
from backend.migrations.v2_watchlist import MIGRATIONS as _V2_MIGRATIONS, SCHEMA as _V2_SCHEMA  # noqa: F401
from backend.migrations.v3_observations_pk import MIGRATIONS as _V3_MIGRATIONS, SCHEMA as _V3_SCHEMA  # noqa: F401

# Append new migrations after the initial schema.
MIGRATIONS = list(MIGRATIONS) + list(_V2_MIGRATIONS) + list(_V3_MIGRATIONS)
SCHEMA = SCHEMA + "\n" + _V2_SCHEMA + "\n" + _V3_SCHEMA

__all__ = ["MIGRATIONS", "SCHEMA"]
