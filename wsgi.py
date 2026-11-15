"""WSGI entry point for PythonAnywhere."""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Apply deployment profile before importing the app.
os.environ.setdefault("WATS_PROFILE", "free")
os.environ.setdefault("WATS_CACHE_BUDGET_MB", "64")

from backend.app import app as application  # noqa: E402