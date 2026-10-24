"""Shared test setup. Runs before any test module imports the app."""

import os

from paymentsapp.core.crypto import generate_key

# Tests encrypt with a throwaway key, never the developer's real one from backend/.env.
os.environ["FIELD_ENCRYPTION_KEYS"] = f"test={generate_key()}"
