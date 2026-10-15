"""`submission_working_copy` constraint, privilege and audit-policy tests (FR-23, FR-06, NFR-08).

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
from nptc.db.models.submission_working_copy import SubmissionWorkingCopy

_CHECK_VIOLATION = "23514"
_FOREIGN_KEY_VIOLATION = "23503"
_UNIQUE_VIOLATION = "23505"
_INSUFFICIENT_PRIVILEGE = "42501"

_VALID_SCTID = "26604007"
_INSERT_USER = text(
    "INSERT INTO app_user (username, display_name) VALUES (:username, 'Working copy tester') "
    "RETURNING id"
)
_INSERT_SUBMISSION = text(
    "INSERT INTO submission (kind, preferred_term, reference_url, reference_checked_at, "
    "reference_status, submitter_id) "
    "VALUES ('new_test', 'Serum sodium', 'https://example.org/evidence', now(), 200, "
    ":submitter_id) RETURNING id"
)
_INSERT_COPY = text(
    "INSERT INTO submission_working_copy (submission_id, preferred_term, synonyms, snomed_code, "
    "snomed_fsn, property_values, notes) "
    "VALUES (:submission_id, :preferred_term, CAST(:synonyms AS jsonb), :snomed_code, :snomed_fsn, "
    "CAST(:property_values AS jsonb), :notes)"
)


def _insert_submission(connection: Connection) -> object:
    submitter = connection.execute(_INSERT_USER, {"username": f"wc-{uuid.uuid4()}"}).scalar_one()
    return connection.execute(_INSERT_SUBMISSION, {"submitter_id": submitter}).scalar_one()


def _insert_copy(
    connection: Connection,
    submission_id: object,
    *,
    preferred_term: str = "Serum sodium (edited)",
    synonyms: str = "[]",
    snomed_code: str | None = None,
    snomed_fsn: str | None = None,
    property_values: str = "{}",
    notes: str | None = None,
) -> None:
    connection.execute(
        _INSERT_COPY,
        {
            "submission_id": submission_id,
            "preferred_term": preferred_term,
            "synonyms": synonyms,
            "snomed_code": snomed_code,
            "snomed_fsn": snomed_fsn,
            "property_values": property_values,
            "notes": notes,
        },
    )


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_app_role_can_insert_and_select_a_working_copy(app_db: Connection) -> None:
    submission_id = _insert_submission(app_db)

    _insert_copy(
        app_db,
        submission_id,
        synonyms='["Sodium, serum"]',
        snomed_code=_VALID_SCTID,
        snomed_fsn="Serum sodium (observable entity)",
        property_values='{"unit": [{"value": "mmol/L"}]}',
        notes="Checked against the lab sheet.",
    )

    row = app_db.execute(
        text("SELECT * FROM submission_working_copy WHERE submission_id = :id"),
        {"id": submission_id},
    ).one()
    assert row.preferred_term == "Serum sodium (edited)"
    assert row.synonyms == ["Sodium, serum"]
    assert row.snomed_code == _VALID_SCTID
    assert row.snomed_fsn == "Serum sodium (observable entity)"
    assert row.property_values == {"unit": [{"value": "mmol/L"}]}
    assert row.notes == "Checked against the lab sheet."


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_submission_has_at_most_one_working_copy(db: Connection) -> None:
    submission_id = _insert_submission(db)
    _insert_copy(db, submission_id)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_copy(db, submission_id)

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_working_copy_must_belong_to_a_real_submission(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_copy(db, "00000000-0000-0000-0000-000000000000")

    assert exc_info.value.orig.sqlstate == _FOREIGN_KEY_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-06")
@pytest.mark.integration
@pytest.mark.parametrize(
    "code",
    ["26604008", "0123", "not-a-code"],
    ids=["bad_check_digit", "too_short", "not_digits"],
)
def test_a_working_copy_refuses_an_invalid_sctid(db: Connection, code: str) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_copy(db, _insert_submission(db), snomed_code=code, snomed_fsn="Anything")

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_a_working_copy_code_and_fsn_are_both_set_or_both_absent(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_copy(db, _insert_submission(db), snomed_code=_VALID_SCTID)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize(
    "overrides",
    [
        {"preferred_term": "   "},
        {"synonyms": '{"a": 1}'},
        {"property_values": "[]"},
        {"notes": "  "},
    ],
    ids=["blank_term", "synonyms_not_array", "properties_not_object", "blank_notes"],
)
def test_a_working_copy_keeps_the_content_checks_of_a_submission(
    db: Connection, overrides: dict[str, str]
) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_copy(db, _insert_submission(db), **overrides)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_app_role_can_update_the_content_of_a_working_copy(app_db: Connection) -> None:
    submission_id = _insert_submission(app_db)
    _insert_copy(app_db, submission_id)

    app_db.execute(
        text(
            "UPDATE submission_working_copy SET preferred_term = 'Renamed', "
            "synonyms = CAST('[\"x\"]' AS jsonb), notes = 'n', updated_at = now() "
            "WHERE submission_id = :id"
        ),
        {"id": submission_id},
    )

    assert (
        app_db.execute(
            text("SELECT preferred_term FROM submission_working_copy WHERE submission_id = :id"),
            {"id": submission_id},
        ).scalar_one()
        == "Renamed"
    )


#: One refused statement per test: a privilege error aborts the transaction. The parameter ids name
#: the column or verb, so a failure says which guarantee broke.
_REFUSED_STATEMENTS = {
    "update_submission_id": "UPDATE submission_working_copy SET submission_id = submission_id",
    "update_created_at": "UPDATE submission_working_copy SET created_at = now()",
    "delete": "DELETE FROM submission_working_copy",
    "truncate": "TRUNCATE submission_working_copy",
}


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize("statement", _REFUSED_STATEMENTS.values(), ids=_REFUSED_STATEMENTS.keys())
def test_app_role_cannot_move_or_remove_a_working_copy(app_db: Connection, statement: str) -> None:
    _insert_copy(app_db, _insert_submission(app_db))

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text(statement))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_the_submission_content_stays_immutable_for_the_app_role(app_db: Connection) -> None:
    submission_id = _insert_submission(app_db)

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(
            text("UPDATE submission SET preferred_term = 'changed' WHERE id = :id"),
            {"id": submission_id},
        )

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("NFR-08")
def test_the_audit_policy_classifies_every_content_column() -> None:
    policy = policy_for(SubmissionWorkingCopy)

    assert policy.withheld == frozenset()
    assert policy.auditable == frozenset(
        {"preferred_term", "synonyms", "snomed_code", "snomed_fsn", "property_values", "notes"}
    )
