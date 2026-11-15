"""WSGI entry point for PythonAnywhere."""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Apply deployment profile before importing the app.
os.environ.setdefault("WATS_PROFILE", "free")
os.environ.setdefault("WATS_CACHE_BUDGET_MB", "64")
# On restricted hosts (e.g. PythonAnywhere free tier) outbound internet to
# IRSA/S3 may be blocked. Cap network timeouts so the app fails fast and
# serves locally cached data instead of hanging request threads.
os.environ.setdefault("WATS_NETWORK_TIMEOUT_S", os.environ.get("WATS_REQUEST_TIMEOUT_S", "15"))

from backend.app import app as application  # noqa: E402

# Alias for WSGI servers that look for 'app'
app = application