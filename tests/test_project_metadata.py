from __future__ import annotations

from importlib.metadata import version


def test_installed_distribution_version_matches_project_version() -> None:
    assert version("malg") == "0.1.0"
