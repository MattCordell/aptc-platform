"""Migration 0033: the `Triage` state and the `submission_transition` table (FR-28, NFR-08).

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
from sqlalchemy.exc import IntegrityError
from testcontainers.community.postgres import PostgresContainer

REPO_ROOT = Path(__file__).resolve().parents[2]
_DB = "aptc_submission_transition"
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


def _has_transition_table(engine: Engine) -> bool:
    return inspect(engine).has_table("submission_transition")


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_upgrade_allows_the_triage_state(engine: Engine) -> None:
    _at(engine, "0032")
    with pytest.raises(IntegrityError) as before:
        _insert(engine, "Triage")
    assert before.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]

    _migrate(engine, "upgrade", "0033")

    _insert(engine, "Triage")
    _clear(engine)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_upgrade_still_refuses_an_unknown_state(engine: Engine) -> None:
    _at(engine, "0033")

    with pytest.raises(IntegrityError) as exc_info:
        _insert(engine, "Approved")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_the_upgrade_creates_the_transition_table(engine: Engine) -> None:
    _at(engine, "0032")
    assert not _has_transition_table(engine)

    _migrate(engine, "upgrade", "0033")

    assert _has_transition_table(engine)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_downgrade_runs_when_no_submission_is_in_triage(engine: Engine) -> None:
    _at(engine, "0033")
    _clear(engine)
    _insert(engine, "Withdrawn")

    _migrate(engine, "downgrade", "0032")

    assert _revision(engine) == "0032"
    assert not _has_transition_table(engine)
    _clear(engine)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_downgrade_refuses_while_a_submission_is_in_triage(engine: Engine) -> None:
    _at(engine, "0033")
    _clear(engine)
    _insert(engine, "Triage")

    with pytest.raises(RuntimeError, match="cannot downgrade"):
        _migrate(engine, "downgrade", "0032")

    assert _revision(engine) == "0033"
    assert _has_transition_table(engine)
    _clear(engine)
