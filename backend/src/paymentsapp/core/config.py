"""Settings from the environment (secrets, URLs) and versioned YAML config from config/ (rules, limits)."""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[3]
REPO_DIR = BACKEND_DIR.parent


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Values that differ per environment. Secrets come from the environment or backend/.env, never from YAML."""

    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    environment: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"
    config_dir: Path = REPO_DIR / "config"

    database_url: SecretStr | None = None
    # Tests only: a disposable database whose name ends in _test. The test suite wipes it on every run.
    test_database_url: SecretStr | None = None

    # Where the bank and the admin website reach this backend; used to show the webhook URL to register.
    public_base_url: str = "http://localhost:8001"

    # Field encryption keys "k1=<base64>[,k0=<old>]"; the first encrypts, all decrypt (plan 7.3).
    field_encryption_keys: SecretStr | None = None

    # Admin website sign-in (plan M16)
    # The authenticator code can be switched off for local demos only; staging and production always require it.
    admin_totp_required: bool = True
    admin_session_idle_minutes: int = 15
    admin_session_max_hours: int = 8
    admin_max_failed_logins: int = 5
    admin_lockout_minutes: int = 15
    # CIDRs allowed to reach /admin. Empty allows any address outside production and none in production.
    admin_ip_allowlist: list[str] = Field(default_factory=list)

    mockbank_base_url: str = "https://mockbankforpayments.onrender.com"
    mockbank_api_key: SecretStr | None = None
    mockbank_webhook_secret: SecretStr | None = None
    mockbank_escrow_upi_id: str | None = None
    mockbank_operating_upi_id: str | None = None
    # Test-only UPI ID of an ordinary customer, used by the contract tests
    mockbank_test_customer_upi_id: str | None = Field(default=None)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_yaml_config(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    """Load config/<name>.yaml. An empty file loads as an empty dict."""
    path = (config_dir or get_settings().config_dir) / f"{name}.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return data
