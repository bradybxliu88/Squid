"""User preferences persistence for onboarding and QoL features.

Stores per-user preferences like first-run state, tip visibility, and
dismissed onboarding items. Settings are stored as YAML in the cache directory.

Typical usage:
    prefs = load_preferences()
    if not prefs.first_run_completed:
        show_welcome_dialog()
        prefs = prefs.with_update(first_run_completed=True)
        save_preferences(prefs)
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Optional

import yaml

import squid.core.logging

_log = squid.core.logging.get_logger(__name__)


def _get_cache_path() -> Path:
    """Get the preferences file path, creating parent dir if necessary."""
    cache_dir = Path(__file__).parent.parent.parent.parent / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "user_preferences.yaml"


@dataclass(frozen=True)
class UserPreferences:
    """Container for user onboarding and QoL preferences.

    Attributes:
        first_run_completed: Whether the user has seen the welcome dialog.
        show_tips: Whether contextual tips should appear on first use of features.
        show_status_hints: Whether rotating hints should appear in the status bar.
        show_getting_started: Whether the Getting Started checklist panel is visible.
        dismissed_tip_ids: List of tip IDs the user has permanently dismissed.
    """

    first_run_completed: bool = False
    show_tips: bool = True
    show_status_hints: bool = True
    show_getting_started: bool = True
    dismissed_tip_ids: List[str] = field(default_factory=list)

    def with_update(self, **kwargs) -> "UserPreferences":
        """Return a new UserPreferences with the given fields updated."""
        return replace(self, **kwargs)

    def is_tip_dismissed(self, tip_id: str) -> bool:
        """Check if a specific tip has been permanently dismissed."""
        return tip_id in self.dismissed_tip_ids

    def dismiss_tip(self, tip_id: str) -> "UserPreferences":
        """Return a new UserPreferences with the tip added to dismissed list."""
        if tip_id in self.dismissed_tip_ids:
            return self
        return self.with_update(dismissed_tip_ids=[*self.dismissed_tip_ids, tip_id])


def load_preferences(cache_path: Optional[Path] = None) -> UserPreferences:
    """Load user preferences from YAML cache file.

    Returns default preferences if the file is missing or corrupted.
    This function is fail-safe - errors are logged but never raised.
    """
    if cache_path is None:
        cache_path = _get_cache_path()

    if not cache_path.exists():
        _log.debug("No user preferences file found at %s, using defaults", cache_path)
        return UserPreferences()

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            _log.warning("User preferences file is not a dict, using defaults")
            return UserPreferences()

        return UserPreferences(
            first_run_completed=data.get("first_run_completed", False),
            show_tips=data.get("show_tips", True),
            show_status_hints=data.get("show_status_hints", True),
            show_getting_started=data.get("show_getting_started", True),
            dismissed_tip_ids=data.get("dismissed_tip_ids", []),
        )
    except Exception:
        _log.exception("Failed to load user preferences from %s, using defaults", cache_path)
        return UserPreferences()


def save_preferences(prefs: UserPreferences, cache_path: Optional[Path] = None) -> None:
    """Save user preferences to YAML cache file.

    This function is fail-safe - errors are logged but never raised,
    allowing application shutdown to continue.
    """
    if cache_path is None:
        cache_path = _get_cache_path()

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "first_run_completed": prefs.first_run_completed,
            "show_tips": prefs.show_tips,
            "show_status_hints": prefs.show_status_hints,
            "show_getting_started": prefs.show_getting_started,
            "dismissed_tip_ids": list(prefs.dismissed_tip_ids),
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False)
        _log.debug("Saved user preferences to %s", cache_path)
    except Exception:
        _log.exception("Failed to save user preferences to %s", cache_path)
