"""Deterministic missing-field selection for non-destructive CRM hydration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def is_missing(value: Any) -> bool:
    """Return whether a CRM value is empty; numeric zero is populated."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Mapping):
        return not any(is_missing(component) is False for component in value.values())
    if isinstance(value, (list, tuple, set)):
        return len(value) == 0
    return False


def missing_fields(observed: Mapping[str, Any], proposed: Mapping[str, Any]) -> dict[str, Any]:
    """Select only proposed values whose corresponding remote field is empty."""
    return {
        field: value
        for field, value in proposed.items()
        if field in observed and is_missing(observed[field]) and not is_missing(value)
    }
