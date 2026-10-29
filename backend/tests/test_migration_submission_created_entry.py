"""Migration 0038: `submission.created_entry_id` and its grant (FR-28, NFR-08).

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
_DB = "aptc_created_entry"
_CHECK_VIOLATION = "23514"
_UNIQUE_VIOLATION = "23505"

_USER = text(
    "INSERT INTO app_user (username, display_name) VALUES ('migration-submitter', 'Migration') "
    "ON CONFLICT (username) DO UPDATE SET username = EXCLUDED.username RETURNING id"
)
_ENTRY = text(
    "INSERT INTO catalogue_entry (business_key, preferred_term, status) "
    "VALUES (:business_key, :term, 'active') RETURNING id"
)
_SUBMISSION = text(
    "INSERT INTO submission (kind, preferred_term, reference_url, reference_checked_at, "
    "reference_status, submitter_id, entry_id, created_entry_id) "
    "VALUES (:kind, 'Serum sodium', 'https://example.org/evidence', now(), 200, :submitter_id, "
    ":entry_id, :created_entry_id)"
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


def _columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("submission")}


def _entry(engine: Engine, business_key: str) -> object:
    with engine.begin() as connection:
        return connection.execute(
            _ENTRY, {"business_key": business_key, "term": business_key}
        ).scalar_one()


def _insert(engine: Engine, kind: str, entry_id: object, created_entry_id: object) -> None:
    with engine.begin() as connection:
        submitter_id = connection.execute(_USER).scalar_one()
        connection.execute(
            _SUBMISSION,
            {
                "kind": kind,
                "submitter_id": submitter_id,
                "entry_id": entry_id,
                "created_entry_id": created_entry_id,
            },
        )


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_upgrade_adds_the_column_and_the_downgrade_drops_it(engine: Engine) -> None:
    _migrate(engine, "upgrade", "0037")
    assert "created_entry_id" not in _columns(engine)

    _migrate(engine, "upgrade", "0038")
    assert _revision(engine) == "0038"
    assert "created_entry_id" in _columns(engine)

    _migrate(engine, "downgrade", "0037")
    assert _revision(engine) == "0037"
    assert "created_entry_id" not in _columns(engine)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_app_role_may_update_the_column_and_still_no_content_column(engine: Engine) -> None:
    _migrate(engine, "upgrade", "0038")
    with engine.connect() as connection:
        granted = connection.execute(
            text(
                "SELECT has_column_privilege('aptc_app', 'submission', 'created_entry_id', "
                "'UPDATE')"
            )
        ).scalar_one()
        content_granted = connection.execute(
            text(
                "SELECT has_column_privilege('aptc_app', 'submission', 'preferred_term', 'UPDATE')"
            )
        ).scalar_one()
    assert granted is True
    assert content_granted is False


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_new_test_may_record_the_entry_it_created(engine: Engine) -> None:
    _migrate(engine, "upgrade", "0038")
    entry_id = _entry(engine, "APTC-380001")

    _insert(engine, "new_test", None, entry_id)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_amendment_cannot_record_a_created_entry(engine: Engine) -> None:
    _migrate(engine, "upgrade", "0038")
    entry_id = _entry(engine, "APTC-380002")
    created_id = _entry(engine, "APTC-380003")

    with pytest.raises(IntegrityError) as refused:
        _insert(engine, "amendment", entry_id, created_id)
    assert refused.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_one_entry_cannot_be_the_product_of_two_submissions(engine: Engine) -> None:
    _migrate(engine, "upgrade", "0038")
    entry_id = _entry(engine, "APTC-380004")
    _insert(engine, "new_test", None, entry_id)

    with pytest.raises(IntegrityError) as refused:
        _insert(engine, "new_test", None, entry_id)
    assert refused.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]
