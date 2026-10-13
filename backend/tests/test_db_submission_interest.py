"""`submission_interest` constraint, privilege and audit-policy tests (FR-32, FR-34, NFR-08, NFR-17).

`backend/tests` shares one Postgres container, so every row here is made by the test and read back by
its own ids.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from nptc.audit.policy import policy_for
from nptc.db.models.submission_interest import SubmissionInterest

_CHECK_VIOLATION = "23514"
_FOREIGN_KEY_VIOLATION = "23503"
_UNIQUE_VIOLATION = "23505"
_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_USER = text(
    "INSERT INTO app_user (username, display_name) VALUES (:username, 'Interest tester') "
    "RETURNING id"
)
_INSERT_SUBMISSION = text(
    "INSERT INTO submission (kind, preferred_term, reference_url, reference_checked_at, "
    "reference_status, submitter_id) "
    "VALUES ('new_test', 'Serum sodium', 'https://example.org/evidence', now(), 200, "
    ":submitter_id) RETURNING id"
)
_INSERT_INTEREST = text(
    "INSERT INTO submission_interest (submission_id, user_id, organisation) "
    "VALUES (:submission_id, :user_id, :organisation) RETURNING id"
)


def _insert_user(connection: Connection) -> object:
    return connection.execute(_INSERT_USER, {"username": f"int-{uuid.uuid4()}"}).scalar_one()


def _insert_submission(connection: Connection) -> object:
    return connection.execute(
        _INSERT_SUBMISSION, {"submitter_id": _insert_user(connection)}
    ).scalar_one()


def _insert_interest(
    connection: Connection,
    submission_id: object,
    user_id: object,
    organisation: str | None = "Example Pathology",
) -> object:
    return connection.execute(
        _INSERT_INTEREST,
        {"submission_id": submission_id, "user_id": user_id, "organisation": organisation},
    ).scalar_one()


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_app_role_can_insert_and_select_an_interest(app_db: Connection) -> None:
    submission_id = _insert_submission(app_db)
    user_id = _insert_user(app_db)

    interest_id = _insert_interest(app_db, submission_id, user_id)

    row = app_db.execute(
        text(
            "SELECT submission_id, user_id, organisation, revoked_at "
            "FROM submission_interest WHERE id = :id"
        ),
        {"id": interest_id},
    ).one()
    assert row.submission_id == submission_id
    assert row.user_id == user_id
    assert row.organisation == "Example Pathology"
    assert row.revoked_at is None


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_a_user_has_one_live_interest_in_a_submission(db: Connection) -> None:
    submission_id = _insert_submission(db)
    user_id = _insert_user(db)
    _insert_interest(db, submission_id, user_id)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_interest(db, submission_id, user_id)

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_a_revoked_interest_does_not_block_a_new_one(app_db: Connection) -> None:
    submission_id = _insert_submission(app_db)
    user_id = _insert_user(app_db)
    first = _insert_interest(app_db, submission_id, user_id)
    app_db.execute(
        text("UPDATE submission_interest SET revoked_at = now() WHERE id = :id"), {"id": first}
    )

    second = _insert_interest(app_db, submission_id, user_id, organisation="Another Lab")

    assert second != first
    live = app_db.execute(
        text(
            "SELECT count(*) FROM submission_interest "
            "WHERE submission_id = :s AND user_id = :u AND revoked_at IS NULL"
        ),
        {"s": submission_id, "u": user_id},
    ).scalar_one()
    assert live == 1


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_two_users_can_each_hold_an_interest_in_one_submission(db: Connection) -> None:
    submission_id = _insert_submission(db)

    _insert_interest(db, submission_id, _insert_user(db))
    _insert_interest(db, submission_id, _insert_user(db))


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_an_interest_must_reference_a_real_submission_and_user(db: Connection) -> None:
    nil = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(IntegrityError) as exc_info:
        _insert_interest(db, nil, _insert_user(db))

    assert exc_info.value.orig.sqlstate == _FOREIGN_KEY_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_an_interest_cannot_be_revoked_before_it_was_registered(db: Connection) -> None:
    interest_id = _insert_interest(db, _insert_submission(db), _insert_user(db))

    with pytest.raises(IntegrityError) as exc_info:
        db.execute(
            text(
                "UPDATE submission_interest SET revoked_at = created_at - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": interest_id},
        )

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_app_role_can_revoke_an_interest(app_db: Connection) -> None:
    interest_id = _insert_interest(app_db, _insert_submission(app_db), _insert_user(app_db))

    app_db.execute(
        text("UPDATE submission_interest SET revoked_at = now() WHERE id = :id"),
        {"id": interest_id},
    )

    revoked = app_db.execute(
        text("SELECT revoked_at FROM submission_interest WHERE id = :id"), {"id": interest_id}
    ).scalar_one()
    assert revoked is not None


#: One refused statement per test: a privilege error aborts the transaction. The parameter ids name
#: the column or verb, so a failure says which guarantee broke.
_REFUSED_STATEMENTS = {
    "update_organisation": "UPDATE submission_interest SET organisation = 'changed'",
    "update_user": "UPDATE submission_interest SET user_id = user_id",
    "update_submission": "UPDATE submission_interest SET submission_id = submission_id",
    "update_created_at": "UPDATE submission_interest SET created_at = now()",
    "delete": "DELETE FROM submission_interest",
    "truncate": "TRUNCATE submission_interest",
}


@pytest.mark.req("FR-32")
@pytest.mark.req("NFR-17")
@pytest.mark.integration
@pytest.mark.parametrize("statement", _REFUSED_STATEMENTS.values(), ids=_REFUSED_STATEMENTS.keys())
def test_app_role_can_only_revoke_never_edit_or_remove_an_interest(
    app_db: Connection, statement: str
) -> None:
    _insert_interest(app_db, _insert_submission(app_db), _insert_user(app_db))

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text(statement))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("NFR-08")
def test_the_audit_policy_withholds_the_organisation_and_records_the_rest() -> None:
    policy = policy_for(SubmissionInterest)

    assert policy.withheld == frozenset({"organisation"})
    assert policy.auditable == frozenset({"submission_id", "user_id", "revoked_at"})
