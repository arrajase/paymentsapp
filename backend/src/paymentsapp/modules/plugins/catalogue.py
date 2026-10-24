"""What plugins exist and what each environment may do with them, read from config/providers.yaml (plan 3.1)."""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from paymentsapp.core.config import Environment, get_settings, load_yaml_config

UPI_ID_PATTERN = re.compile(r"^[a-z0-9._-]{2,64}@[a-z][a-z0-9]{1,31}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    kind: str  # "url", "upi_id" or "text"
    env: str | None


@dataclass(frozen=True)
class PluginSpec:
    key: str
    description: str
    implements: tuple[str, ...]
    modes: tuple[str, ...]
    config: tuple[FieldSpec, ...]
    secrets: tuple[FieldSpec, ...]

    def config_field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.config if f.name == name), None)

    def secret_field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.secrets if f.name == name), None)


@dataclass(frozen=True)
class Catalogue:
    plugins: dict[str, PluginSpec]
    # interface -> plugin -> modes allowed in the current environment
    allowed: dict[str, dict[str, tuple[str, ...]]]

    def allowed_modes(self, plugin_key: str) -> tuple[str, ...]:
        """Modes this environment allows for the plugin, across every interface it serves."""
        modes: list[str] = []
        for choices in self.allowed.values():
            for mode in choices.get(plugin_key, ()):
                if mode not in modes:
                    modes.append(mode)
        return tuple(modes)

    def interfaces_served(self, plugin_key: str) -> tuple[str, ...]:
        return tuple(i for i, choices in self.allowed.items() if plugin_key in choices)


def _fields(raw: dict[str, Any] | None) -> tuple[FieldSpec, ...]:
    return tuple(
        FieldSpec(name=name, label=spec.get("label", name), kind=spec.get("kind", "text"), env=spec.get("env"))
        for name, spec in (raw or {}).items()
    )


def load_catalogue(environment: Environment | None = None) -> Catalogue:
    data = load_yaml_config("providers")
    env = (environment or get_settings().environment).value
    plugins = {
        key: PluginSpec(
            key=key,
            description=spec.get("description", key),
            implements=tuple(spec.get("implements", ())),
            modes=tuple(spec.get("modes", ())),
            config=_fields(spec.get("config")),
            secrets=_fields(spec.get("secrets")),
        )
        for key, spec in data["plugins"].items()
    }
    allowed_raw = data["environments"][env]["allowed"] or {}
    allowed = {
        interface: {plugin: tuple(modes) for plugin, modes in choices.items()}
        for interface, choices in allowed_raw.items()
    }
    return Catalogue(plugins=plugins, allowed=allowed)


@lru_cache
def get_catalogue() -> Catalogue:
    return load_catalogue()


def validate_config_value(field: FieldSpec, value: str, environment: Environment) -> str | None:
    """A reason the value is unacceptable, or None."""
    if not value or len(value) > 512 or value != value.strip():
        return "must be 1-512 characters with no leading or trailing spaces"
    if field.kind == "url":
        parsed = urlparse(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            return "must be a full URL, such as https://bank.example.com"
        if parsed.scheme == "http" and not (environment == Environment.DEVELOPMENT and parsed.hostname in _LOCAL_HOSTS):
            return "must use https (plain http is allowed only for localhost in development)"
        if parsed.query or parsed.fragment or parsed.username:
            return "must not contain a query, fragment or credentials"
    elif field.kind == "upi_id" and not UPI_ID_PATTERN.match(value):
        return "must be a UPI ID such as name@bank"
    return None


def validate_secret_value(value: str) -> str | None:
    if not value or len(value) > 512 or any(c.isspace() for c in value):
        return "must be 1-512 characters with no spaces"
    return None
