"""Migration 0034: the `internal_comment` table and its grants (FR-95, NFR-08).

Runs on its own database, because it moves the schema back and forward.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from testcontainers.community.postgres import PostgresContainer

REPO_ROOT = Path(__file__).resolve().parents[2]
_DB = "aptc_internal_comment"


def _config() -> Config:
    return Config(toml_file=str(REPO_ROOT / "pyproject.toml"))


def _migrate(engine: Engine, direction: str, revision: str) -> None:
    config = _config()
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        getattr(command, direction)(config, revision)
        connection.commit()


def _revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


@pytest.fixture(scope="module")
def engine(postgres_container: PostgresContainer, migrated: None) -> Iterator[Engine]:
    owner_url = make_url(postgres_container.get_connection_url())
    admin = create_engine(owner_url.render_as_string(hide_password=False))
    with admin.connect() as raw:
        connection = raw.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text(f"DROP DATABASE IF EXISTS {_DB}"))
        connection.execute(text(f"CREATE DATABASE {_DB}"))
    admin.dispose()
    engine = create_engine(owner_url.set(database=_DB).render_as_string(hide_password=False))
    try:
        yield engine
    finally:
        engine.dispose()


def _has_comment_table(engine: Engine) -> bool:
    return inspect(engine).has_table("internal_comment")


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_the_upgrade_creates_the_comment_table_and_the_downgrade_drops_it(engine: Engine) -> None:
    _migrate(engine, "upgrade", "0033")
    assert not _has_comment_table(engine)

    _migrate(engine, "upgrade", "0034")
    assert _revision(engine) == "0034"
    assert _has_comment_table(engine)

    _migrate(engine, "downgrade", "0033")
    assert _revision(engine) == "0033"
    assert not _has_comment_table(engine)
