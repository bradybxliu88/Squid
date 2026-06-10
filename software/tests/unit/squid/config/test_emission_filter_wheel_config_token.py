"""Guards the filter-wheel config token rename (ports upstream #543).

arch_v2 code reads ``USE_EMISSION_FILTER_WHEEL`` / ``EMISSION_FILTER_WHEEL_TYPE``
from ``_def`` (see ``squid/core/config/__init__.py``). The legacy ``.ini`` token
``filter_controller_enable`` is dead — nothing reads it. These tests ensure the
shipped configs use the live token name (lowercased, as ConfigParser exposes it)
so the ``.ini`` actually controls the feature, and that the dead token is gone.
"""

import configparser
from pathlib import Path

import pytest

CONFIG_DIR = Path(__file__).resolve().parents[4] / "configurations"


def _configs_with_filter_token():
    """Shipped configs that historically carried the filter-wheel toggle."""
    matches = []
    for ini in sorted(CONFIG_DIR.glob("configuration_*.ini")):
        text = ini.read_text(encoding="utf-8", errors="ignore")
        if "filter_controller_enable" in text or "USE_EMISSION_FILTER_WHEEL" in text:
            matches.append(ini)
    return matches


def test_some_configs_carry_the_token():
    """Sanity: the test is actually scanning real config files."""
    assert _configs_with_filter_token(), f"No configs found under {CONFIG_DIR}"


@pytest.mark.parametrize("ini", _configs_with_filter_token(), ids=lambda p: p.name)
def test_no_dead_filter_controller_enable_token(ini):
    """The dead ``filter_controller_enable`` token must not appear."""
    text = ini.read_text(encoding="utf-8", errors="ignore")
    assert "filter_controller_enable" not in text, (
        f"{ini.name} still uses the dead 'filter_controller_enable' token; "
        "rename to USE_EMISSION_FILTER_WHEEL (nothing reads the old name)."
    )


@pytest.mark.parametrize("ini", _configs_with_filter_token(), ids=lambda p: p.name)
def test_uses_live_emission_filter_wheel_token(ini):
    """The live token must be present and lowercased-readable by _def's loader."""
    cfp = configparser.ConfigParser()
    cfp.read(ini)
    opts = cfp.options("GENERAL")
    # _def.py compares ``var_name.lower()`` against cfp.options("GENERAL"),
    # which ConfigParser already lowercases.
    assert "use_emission_filter_wheel" in opts, (
        f"{ini.name} is missing USE_EMISSION_FILTER_WHEEL in [GENERAL]"
    )
    assert "emission_filter_wheel_type" in opts, (
        f"{ini.name} is missing EMISSION_FILTER_WHEEL_TYPE in [GENERAL]"
    )
