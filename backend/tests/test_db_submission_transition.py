"""`submission_transition` constraint, privilege and audit-policy tests (FR-28, NFR-08).

`backend/tests` shares one Postgres container, so every row here is made by the test and read back by
its own ids. Each violation gets its own test, because a failed statement aborts the surrounding
transaction (25P02).
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from nptc.audit.policy import policy_for
from nptc.db.models.submission import SubmissionState
from nptc.db.models.submission_transition import (
    _FROM_STATE_CHECK_SQL,
    _TO_STATE_CHECK_SQL,
    SubmissionTransition,
)

_CHECK_VIOLATION = "23514"
_FOREIGN_KEY_VIOLATION = "23503"
_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_USER = text(
    "INSERT INTO app_user (username, display_name) VALUES (:username, 'Transition tester') "
    "RETURNING id"
)
_INSERT_SUBMISSION = text(
    "INSERT INTO submission (kind, preferred_term, reference_url, reference_checked_at, "
    "reference_status, submitter_id) "
    "VALUES ('new_test', 'Serum sodium', 'https://example.org/evidence', now(), 200, "
    ":submitter_id) RETURNING id"
)
_INSERT_TRANSITION = text(
    "INSERT INTO submission_transition (submission_id, from_state, to_state, actor_id, reason) "
    "VALUES (:submission_id, :from_state, :to_state, :actor_id, :reason) RETURNING id"
)


def _insert_user(connection: Connection) -> object:
    return connection.execute(_INSERT_USER, {"username": f"tr-{uuid.uuid4()}"}).scalar_one()


def _insert_submission(connection: Connection, submitter_id: object) -> object:
    return connection.execute(_INSERT_SUBMISSION, {"submitter_id": submitter_id}).scalar_one()


def _insert_transition(connection: Connection, **overrides: object) -> object:
    """Inserts a valid row, then lets `overrides` replace any column, so a test aims at one
    constraint at a time."""
    actor_id = _insert_user(connection)
    values: dict[str, object] = {
        "submission_id": _insert_submission(connection, actor_id),
        "from_state": "Submitted",
        "to_state": "Withdrawn",
        "actor_id": actor_id,
        "reason": "Entered in error",
    }
    values.update(overrides)
    return connection.execute(_INSERT_TRANSITION, values).scalar_one()


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_app_role_can_insert_and_select_a_transition(app_db: Connection) -> None:
    transition_id = _insert_transition(app_db)

    row = app_db.execute(
        text(
            "SELECT from_state, to_state, reason, created_at "
            "FROM submission_transition WHERE id = :id"
        ),
        {"id": transition_id},
    ).one()
    assert row.from_state == "Submitted"
    assert row.to_state == "Withdrawn"
    assert row.reason == "Entered in error"
    assert row.created_at is not None


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_submission_can_have_more_than_one_transition(app_db: Connection) -> None:
    first = _insert_transition(app_db, from_state="Submitted", to_state="Triage")
    submission_id = app_db.execute(
        text("SELECT submission_id FROM submission_transition WHERE id = :id"), {"id": first}
    ).scalar_one()
    actor_id = _insert_user(app_db)

    app_db.execute(
        _INSERT_TRANSITION,
        {
            "submission_id": submission_id,
            "from_state": "Triage",
            "to_state": "Withdrawn",
            "actor_id": actor_id,
            "reason": "Taken back",
        },
    )

    count = app_db.execute(
        text("SELECT count(*) FROM submission_transition WHERE submission_id = :s"),
        {"s": submission_id},
    ).scalar_one()
    assert count == 2


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("column", ["from_state", "to_state"])
def test_a_transition_refuses_a_state_the_submission_cannot_hold(
    db: Connection, column: str
) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_transition(db, **{column: "Archived"})

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_transition_must_change_the_state(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_transition(db, from_state="Triage", to_state="Triage")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("reason", ["", "   "])
def test_a_transition_refuses_a_blank_reason(db: Connection, reason: str) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_transition(db, reason=reason)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("column", ["submission_id", "actor_id"])
def test_a_transition_must_reference_a_real_submission_and_actor(
    db: Connection, column: str
) -> None:
    nil = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(IntegrityError) as exc_info:
        _insert_transition(db, **{column: nil})

    assert exc_info.value.orig.sqlstate == _FOREIGN_KEY_VIOLATION  # type: ignore[union-attr]


#: One refused statement per test: a privilege error aborts the transaction. The parameter ids name
#: the column or verb, so a failure says which guarantee broke.
_REFUSED_STATEMENTS = {
    "update_reason": "UPDATE submission_transition SET reason = 'changed'",
    "update_to_state": "UPDATE submission_transition SET to_state = from_state",
    "update_actor": "UPDATE submission_transition SET actor_id = actor_id",
    "update_created_at": "UPDATE submission_transition SET created_at = now()",
    "delete": "DELETE FROM submission_transition",
    "truncate": "TRUNCATE submission_transition",
}


@pytest.mark.req("FR-28")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize("statement", _REFUSED_STATEMENTS.values(), ids=_REFUSED_STATEMENTS.keys())
def test_app_role_can_never_edit_or_remove_a_transition(app_db: Connection, statement: str) -> None:
    _insert_transition(app_db)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text(statement))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-06")
def test_the_state_checks_list_exactly_the_submission_states() -> None:
    expected = {s.value for s in SubmissionState}
    assert set(re.findall(r"'([^']*)'", _FROM_STATE_CHECK_SQL)) == expected
    assert set(re.findall(r"'([^']*)'", _TO_STATE_CHECK_SQL)) == expected


@pytest.mark.req("NFR-08")
def test_the_audit_policy_classifies_every_column() -> None:
    policy = policy_for(SubmissionTransition)

    assert policy.withheld == frozenset()
    assert policy.auditable == frozenset(
        {"submission_id", "from_state", "to_state", "actor_id", "reason"}
    )
