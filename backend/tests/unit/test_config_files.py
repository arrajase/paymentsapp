"""Guards on the reviewed config files: a bad edit to them fails CI instead of loosening production."""

from typing import Any

import pytest

from paymentsapp.core.config import Environment, load_yaml_config

ENVIRONMENTS = [e.value for e in Environment]


@pytest.fixture(scope="module")
def providers() -> dict[str, Any]:
    return load_yaml_config("providers")


@pytest.fixture(scope="module")
def controls() -> dict[str, Any]:
    return load_yaml_config("security_controls")


def test_providers_reference_only_declared_plugins_interfaces_and_modes(providers: dict[str, Any]) -> None:
    plugins = providers["plugins"]
    for env in ENVIRONMENTS:
        for interface, choices in providers["environments"][env]["allowed"].items():
            assert interface in providers["interfaces"], f"{env}: unknown interface {interface}"
            for plugin, modes in choices.items():
                assert interface in plugins[plugin]["implements"], f"{env}: {plugin} does not implement {interface}"
                assert set(modes) <= set(plugins[plugin]["modes"]), f"{env}: {plugin} has no such mode"


def test_non_production_environments_never_use_production_mode(providers: dict[str, Any]) -> None:
    for env in ("development", "staging"):
        for choices in providers["environments"][env]["allowed"].values():
            for modes in choices.values():
                assert "production" not in modes, f"{env} must never move real money"


def test_production_uses_production_mode_only(providers: dict[str, Any]) -> None:
    for choices in providers["environments"]["production"]["allowed"].values():
        for modes in choices.values():
            assert modes == ["production"]


def test_every_control_covers_every_environment_with_a_valid_default(controls: dict[str, Any]) -> None:
    for name, control in controls["controls"].items():
        for env in ENVIRONMENTS:
            default = control["default"][env]
            if control["kind"] == "state":
                assert default in control["allowed"][env], f"{name}/{env}: default not allowed"
            else:
                low, high = control["bounds"][env]
                assert low <= default <= high, f"{name}/{env}: default out of bounds"


def test_production_never_offers_off(controls: dict[str, Any]) -> None:
    for name, control in controls["controls"].items():
        if control["kind"] == "state":
            # An unquoted off loads as False, which would make the check below pass by accident.
            assert all(isinstance(s, str) for env in ENVIRONMENTS for s in control["allowed"][env]), name
            assert "off" not in control["allowed"]["production"], f"{name} must not be switchable off in production"


def test_production_is_never_looser_than_staging(controls: dict[str, Any]) -> None:
    strictness = {"off": 0, "monitor": 1, "enforce": 2}
    for name, control in controls["controls"].items():
        if control["kind"] == "state":
            weakest_prod = min(strictness[s] for s in control["allowed"]["production"])
            weakest_staging = min(strictness[s] for s in control["allowed"]["staging"])
            assert weakest_prod >= weakest_staging, name


def test_number_controls_say_which_direction_loosens_them(controls: dict[str, Any]) -> None:
    for name, control in controls["controls"].items():
        if control["kind"] == "number":
            assert control.get("looser") in {"lower", "higher"}, name


def test_features_have_valid_defaults_and_investments_stay_off_in_production() -> None:
    features = load_yaml_config("features")["features"]
    for name, feature in features.items():
        for env in ENVIRONMENTS:
            default = feature["default"][env]
            allowed = feature.get("allowed", {}).get(env, [True, False])
            assert isinstance(default, bool) and default in allowed, f"{name}/{env}"
    assert features["ai_deals_investments"]["allowed"]["production"] == [False]


def test_catalogue_loads_for_every_environment() -> None:
    from paymentsapp.modules.controls.catalogue import load_control_catalogue

    for env in Environment:
        catalogue = load_control_catalogue(env)
        for spec in catalogue.controls.values():
            assert spec.validate(spec.default) is None, f"{env.value}: {spec.key} default is not allowed"
