"""`submission_terminology_request` constraint, privilege and audit-policy tests (FR-30, FR-31, NFR-08).

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
from nptc.db.models.submission_terminology_request import SubmissionTerminologyRequest

_CHECK_VIOLATION = "23514"
_FOREIGN_KEY_VIOLATION = "23503"
_UNIQUE_VIOLATION = "23505"
_NOT_NULL_VIOLATION = "23502"
_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_USER = text(
    "INSERT INTO app_user (username, display_name) VALUES (:username, 'Terminology tester') "
    "RETURNING id"
)
_INSERT_SUBMISSION = text(
    "INSERT INTO submission (kind, state, preferred_term, reference_url, reference_checked_at, "
    "reference_status, submitter_id) "
    "VALUES ('new_test', 'Awaiting terminology', 'Serum sodium', 'https://example.org/evidence', "
    "now(), 200, :submitter_id) RETURNING id"
)
_INSERT_REQUEST = text(
    "INSERT INTO submission_terminology_request (submission_id, proposed_fsn, target_edition, "
    "justification, external_reference_system, external_reference_ref, raised_date, "
    "expected_release) "
    "VALUES (:submission_id, :proposed_fsn, :target_edition, :justification, "
    ":external_reference_system, :external_reference_ref, :raised_date, :expected_release)"
)


def _insert_submission(connection: Connection) -> object:
    submitter = connection.execute(_INSERT_USER, {"username": f"tr-{uuid.uuid4()}"}).scalar_one()
    return connection.execute(_INSERT_SUBMISSION, {"submitter_id": submitter}).scalar_one()


def _insert_request(
    connection: Connection,
    submission_id: object,
    *,
    proposed_fsn: str | None = "Serum sodium level (observable entity)",
    target_edition: str | None = "au",
    justification: str | None = "No concept covers this measurement.",
    external_reference_system: str | None = "snomed-request-service",
    external_reference_ref: str | None = "REQ-1",
    raised_date: str | None = "2026-10-01",
    expected_release: str | None = "2027-03-31",
) -> None:
    connection.execute(
        _INSERT_REQUEST,
        {
            "submission_id": submission_id,
            "proposed_fsn": proposed_fsn,
            "target_edition": target_edition,
            "justification": justification,
            "external_reference_system": external_reference_system,
            "external_reference_ref": external_reference_ref,
            "raised_date": raised_date,
            "expected_release": expected_release,
        },
    )


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-31")
@pytest.mark.integration
def test_app_role_can_insert_and_select_a_request(app_db: Connection) -> None:
    submission_id = _insert_submission(app_db)

    _insert_request(app_db, submission_id)

    row = app_db.execute(
        text("SELECT * FROM submission_terminology_request WHERE submission_id = :id"),
        {"id": submission_id},
    ).one()
    assert row.proposed_fsn == "Serum sodium level (observable entity)"
    assert row.target_edition == "au"
    assert row.external_reference_system == "snomed-request-service"
    assert row.external_reference_ref == "REQ-1"
    assert str(row.raised_date) == "2026-10-01"
    assert str(row.expected_release) == "2027-03-31"


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_the_expected_release_is_optional(db: Connection) -> None:
    _insert_request(db, _insert_submission(db), expected_release=None)


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_a_submission_has_at_most_one_request(db: Connection) -> None:
    submission_id = _insert_submission(db)
    _insert_request(db, submission_id)

    with pytest.raises(IntegrityError) as exc_info:
        _insert_request(db, submission_id)

    assert exc_info.value.orig.sqlstate == _UNIQUE_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_a_request_must_belong_to_a_real_submission(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_request(db, "00000000-0000-0000-0000-000000000000")

    assert exc_info.value.orig.sqlstate == _FOREIGN_KEY_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-30")
@pytest.mark.integration
@pytest.mark.parametrize("edition", ["AU", "intl", "", "snomed"])
def test_a_request_refuses_an_edition_other_than_au_or_int(db: Connection, edition: str) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_request(db, _insert_submission(db), target_edition=edition)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_a_request_accepts_the_international_edition(db: Connection) -> None:
    _insert_request(db, _insert_submission(db), target_edition="int")


@pytest.mark.req("FR-31")
@pytest.mark.integration
@pytest.mark.parametrize(
    "overrides",
    [
        {"proposed_fsn": "  "},
        {"justification": ""},
        {"external_reference_system": " "},
        {"external_reference_ref": ""},
    ],
    ids=["blank_fsn", "empty_justification", "blank_system", "empty_reference"],
)
def test_a_request_refuses_a_blank_field(db: Connection, overrides: dict[str, str]) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_request(db, _insert_submission(db), **overrides)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-30")
@pytest.mark.integration
@pytest.mark.parametrize(
    "field",
    [
        "proposed_fsn",
        "target_edition",
        "justification",
        "external_reference_system",
        "external_reference_ref",
        "raised_date",
    ],
)
def test_a_request_requires_every_field_but_the_expected_release(
    db: Connection, field: str
) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_request(db, _insert_submission(db), **{field: None})

    assert exc_info.value.orig.sqlstate == _NOT_NULL_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-31")
@pytest.mark.integration
def test_app_role_can_update_the_tracking_fields(app_db: Connection) -> None:
    submission_id = _insert_submission(app_db)
    _insert_request(app_db, submission_id)

    app_db.execute(
        text(
            "UPDATE submission_terminology_request SET external_reference_system = 'other', "
            "external_reference_ref = 'REQ-2', expected_release = NULL, updated_at = now() "
            "WHERE submission_id = :id"
        ),
        {"id": submission_id},
    )

    row = app_db.execute(
        text(
            "SELECT external_reference_system, external_reference_ref, expected_release "
            "FROM submission_terminology_request WHERE submission_id = :id"
        ),
        {"id": submission_id},
    ).one()
    assert (row.external_reference_system, row.external_reference_ref) == ("other", "REQ-2")
    assert row.expected_release is None


#: One refused statement per test: a privilege error aborts the transaction. The parameter ids name
#: the column or verb, so a failure says which guarantee broke.
_REFUSED_STATEMENTS = {
    "update_submission_id": "UPDATE submission_terminology_request SET submission_id = submission_id",
    "update_proposed_fsn": "UPDATE submission_terminology_request SET proposed_fsn = 'x'",
    "update_target_edition": "UPDATE submission_terminology_request SET target_edition = 'int'",
    "update_justification": "UPDATE submission_terminology_request SET justification = 'x'",
    "update_raised_date": "UPDATE submission_terminology_request SET raised_date = now()",
    "update_created_at": "UPDATE submission_terminology_request SET created_at = now()",
    "delete": "DELETE FROM submission_terminology_request",
    "truncate": "TRUNCATE submission_terminology_request",
}


@pytest.mark.req("FR-30")
@pytest.mark.integration
@pytest.mark.parametrize("statement", _REFUSED_STATEMENTS.values(), ids=_REFUSED_STATEMENTS.keys())
def test_app_role_cannot_rewrite_what_was_asked_for_or_remove_a_request(
    app_db: Connection, statement: str
) -> None:
    _insert_request(app_db, _insert_submission(app_db))

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text(statement))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("NFR-08")
def test_the_audit_policy_classifies_every_column() -> None:
    policy = policy_for(SubmissionTerminologyRequest)

    assert policy.withheld == frozenset()
    assert policy.auditable == frozenset(
        {
            "proposed_fsn",
            "target_edition",
            "justification",
            "external_reference_system",
            "external_reference_ref",
            "raised_date",
            "expected_release",
        }
    )
