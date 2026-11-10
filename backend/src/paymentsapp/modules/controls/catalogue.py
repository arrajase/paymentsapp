"""Runtime controls and feature switches, and what each environment allows (plan 3.2).

Read from config/security_controls.yaml and config/features.yaml. The admin website can choose only values these
files allow for the current environment.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from paymentsapp.core.config import Environment, get_settings, load_yaml_config

Kind = Literal["state", "number", "feature"]
Value = str | int | bool

STATE_STRICTNESS = {"off": 0, "monitor": 1, "enforce": 2}


@dataclass(frozen=True)
class ControlSpec:
    key: str
    kind: Kind
    description: str
    default: Value
    allowed: tuple[Value, ...]  # state and feature controls
    bounds: tuple[int, int] | None  # number controls
    looser: Literal["lower", "higher"] | None  # number controls: which direction weakens security
    client: bool  # included in GET /v1/app-config

    def validate(self, value: Any) -> str | None:
        """A reason the value is not allowed here, or None."""
        if self.kind == "number":
            if not isinstance(value, int) or isinstance(value, bool):
                return "must be a whole number"
            assert self.bounds is not None
            low, high = self.bounds
            if not low <= value <= high:
                return f"must be between {low} and {high} here"
            return None
        if self.kind == "feature" and not isinstance(value, bool):
            return "must be on or off"
        if self.kind == "state" and not isinstance(value, str):
            return "must be one of the states"
        if value not in self.allowed:
            options = ", ".join(_show(v) for v in self.allowed)
            return f"must be one of: {options} (in this environment)"
        return None

    def is_looser(self, old: Value, new: Value) -> bool:
        """Whether moving from old to new weakens security. Feature switches are not security controls."""
        if self.kind == "state":
            return STATE_STRICTNESS[str(new)] < STATE_STRICTNESS[str(old)]
        if self.kind == "number":
            return (int(new) < int(old)) if self.looser == "lower" else (int(new) > int(old))
        return False


def _show(value: Value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


@dataclass(frozen=True)
class ControlCatalogue:
    environment: Environment
    controls: dict[str, ControlSpec]
    loosening_expiry_hours: int


def load_control_catalogue(environment: Environment) -> ControlCatalogue:
    env = environment.value
    security = load_yaml_config("security_controls")
    features = load_yaml_config("features")
    controls: dict[str, ControlSpec] = {}
    for key, spec in security["controls"].items():
        kind: Kind = spec["kind"]
        if kind == "state":
            states = [*spec["allowed"][env], spec["default"][env]]
            unknown = [s for s in states if s not in STATE_STRICTNESS]
            if unknown:
                # Usually an unquoted off, which YAML reads as false.
                raise ValueError(f'{key}: states must be quoted "off", "monitor" or "enforce"; got {unknown}')
        controls[key] = ControlSpec(
            key=key,
            kind=kind,
            description=spec["description"],
            default=spec["default"][env],
            allowed=tuple(spec.get("allowed", {}).get(env, ())),
            bounds=(int(spec["bounds"][env][0]), int(spec["bounds"][env][1])) if kind == "number" else None,
            looser=spec.get("looser"),
            client=bool(spec.get("client", False)),
        )
    for key, spec in features["features"].items():
        if key in controls:
            raise ValueError(f"{key} is defined as both a security control and a feature")
        controls[key] = ControlSpec(
            key=key,
            kind="feature",
            description=spec["description"],
            default=bool(spec["default"][env]),
            allowed=tuple(spec.get("allowed", {}).get(env, (True, False))),
            bounds=None,
            looser=None,
            client=True,
        )
    return ControlCatalogue(
        environment=environment,
        controls=controls,
        loosening_expiry_hours=int(security.get("production_loosening_expiry_hours", 24)),
    )


@lru_cache
def _cached(environment: Environment) -> ControlCatalogue:
    return load_control_catalogue(environment)


def get_control_catalogue() -> ControlCatalogue:
    return _cached(get_settings().environment)
