"""Shared test setup. Runs before any test module imports the app."""

import os

from paymentsapp.core.crypto import generate_key

# Tests encrypt with a throwaway key, never the developer's real one from backend/.env.
os.environ["FIELD_ENCRYPTION_KEYS"] = f"test={generate_key()}"
os.environ["BLIND_INDEX_KEY"] = generate_key()

# Pin the settings tests depend on, so a developer's backend/.env (for example a local demo with the
# authenticator code switched off) can't change what the tests check. Environment variables win over .env.
os.environ["ENVIRONMENT"] = "development"
os.environ["ADMIN_TOTP_REQUIRED"] = "true"
os.environ["ADMIN_IP_ALLOWLIST"] = "[]"
