from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path


def test_installed_distribution_version_matches_project_version() -> None:
    repository_root = Path(__file__).resolve().parent.parent
    with (repository_root / "pyproject.toml").open("rb") as project_file:
        project_version = tomllib.load(project_file)["project"]["version"]
    assert version("malg") == project_version
