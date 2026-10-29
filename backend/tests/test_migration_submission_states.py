"""Migration 0031: the `submission.state` check allows the three closed states (FR-28, FR-42).

Runs on its own database, because it moves the schema back and forward.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from testcontainers.community.postgres import PostgresContainer

REPO_ROOT = Path(__file__).resolve().parents[2]
_DB = "aptc_submission_states"
_CHECK_VIOLATION = "23514"

_USER = text(
    "INSERT INTO app_user (username, display_name) VALUES ('migration-submitter', 'Migration') "
    "ON CONFLICT (username) DO UPDATE SET username = EXCLUDED.username RETURNING id"
)
_SUBMISSION = text(
    "INSERT INTO submission (kind, state, preferred_term, reference_url, reference_checked_at, "
    "reference_status, submitter_id) "
    "VALUES ('new_test', :state, 'Serum sodium', 'https://example.org/evidence', now(), 200, "
    ":submitter_id)"
)


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


def _at(engine: Engine, revision: str) -> None:
    """Brings the database to exactly `revision`, whichever side of it it is on. Revisions are
    zero-padded, so a string comparison orders them."""
    current = _revision(engine)
    if current is None or current < revision:
        _migrate(engine, "upgrade", revision)
    elif current > revision:
        _migrate(engine, "downgrade", revision)


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


def _insert(engine: Engine, state: str) -> None:
    with engine.begin() as connection:
        submitter_id = connection.execute(_USER).scalar_one()
        connection.execute(_SUBMISSION, {"state": state, "submitter_id": submitter_id})


def _clear(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM submission"))


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Published in release", "Rejected", "Withdrawn"])
def test_the_upgrade_allows_each_closed_state(engine: Engine, state: str) -> None:
    _at(engine, "0030")
    with pytest.raises(IntegrityError) as before:
        _insert(engine, state)
    assert before.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]

    _migrate(engine, "upgrade", "0031")

    _insert(engine, state)
    _clear(engine)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_upgrade_still_refuses_an_unknown_state(engine: Engine) -> None:
    _at(engine, "0031")

    with pytest.raises(IntegrityError) as exc_info:
        _insert(engine, "Approved")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_downgrade_runs_when_every_row_is_submitted(engine: Engine) -> None:
    _at(engine, "0031")
    _clear(engine)
    _insert(engine, "Submitted")

    _migrate(engine, "downgrade", "0030")

    assert _revision(engine) == "0030"
    _clear(engine)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_downgrade_refuses_while_a_row_holds_a_widened_state(engine: Engine) -> None:
    _at(engine, "0031")
    _clear(engine)
    _insert(engine, "Rejected")

    with pytest.raises(RuntimeError, match="cannot downgrade"):
        _migrate(engine, "downgrade", "0030")

    assert _revision(engine) == "0031"
    _clear(engine)
