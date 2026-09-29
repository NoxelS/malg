"""Tests for bundled-head schema readiness."""

from pathlib import Path

import pytest
from alembic.util import CommandError
from sqlalchemy import create_engine, text

from malg.database.wait_for_schema import current_revision, required_revision, wait_for_schema


def _write_config(root: Path, revisions: list[tuple[str, str | None]]) -> Path:
    versions = root / "migrations" / "versions"
    versions.mkdir(parents=True)
    for revision, down_revision in revisions:
        (versions / f"{revision}.py").write_text(
            f"from alembic import op\n"
            f"revision = {revision!r}\n"
            f"down_revision = {down_revision!r}\n"
            "branch_labels = None\n"
            "depends_on = None\n\n"
            "def upgrade():\n    pass\n\n"
            "def downgrade():\n    pass\n"
        )
    config = root / "alembic.ini"
    config.write_text(
        "[alembic]\nscript_location = %(here)s/migrations\nprepend_sys_path = %(here)s\n"
    )
    return config


def test_required_revision_reads_the_bundled_single_head(monkeypatch, tmp_path):
    config = _write_config(tmp_path, [("base", None), ("new_head", "base")])
    monkeypatch.setenv("ALEMBIC_CONFIG", str(config))

    assert required_revision() == "new_head"


def test_required_revision_rejects_missing_and_multiple_heads(monkeypatch, tmp_path):
    missing = tmp_path / "missing.ini"
    monkeypatch.setenv("ALEMBIC_CONFIG", str(missing))
    with pytest.raises((CommandError, FileNotFoundError, OSError, KeyError)):
        required_revision()

    config = _write_config(
        tmp_path / "multiple", [("base", None), ("left", "base"), ("right", "base")]
    )
    monkeypatch.setenv("ALEMBIC_CONFIG", str(config))
    with pytest.raises(RuntimeError, match="exactly one Alembic head"):
        required_revision()


def test_wait_for_schema_requires_exact_database_revision_without_writing(tmp_path, monkeypatch):
    config = _write_config(tmp_path / "graph", [("base", None), ("head", "base")])
    monkeypatch.setenv("ALEMBIC_CONFIG", str(config))
    database = tmp_path / "schema.db"
    url = f"sqlite:///{database}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        connection.execute(text("INSERT INTO alembic_version VALUES ('old')"))
    monkeypatch.setattr("malg.database.wait_for_schema.database_url", lambda: url)

    assert current_revision(url) == "old"
    assert not wait_for_schema(1)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalars().all() == ["old"]

    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'head'"))
    assert wait_for_schema(1)
    engine.dispose()
