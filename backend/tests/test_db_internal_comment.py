"""`internal_comment` constraint, privilege and audit-policy tests (FR-95, NFR-08).

`backend/tests` shares one Postgres container, so every row here is made by the test and read back by
its own ids.
"""

from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError

from nptc.audit.policy import policy_for
from nptc.db.models.internal_comment import InternalComment

_CHECK_VIOLATION = "23514"
_FOREIGN_KEY_VIOLATION = "23503"
_INSUFFICIENT_PRIVILEGE = "42501"

_INSERT_USER = text(
    "INSERT INTO app_user (username, display_name) VALUES (:username, 'Comment tester') "
    "RETURNING id"
)
_INSERT_SUBMISSION = text(
    "INSERT INTO submission (kind, preferred_term, reference_url, reference_checked_at, "
    "reference_status, submitter_id) "
    "VALUES ('new_test', 'Serum sodium', 'https://example.org/evidence', now(), 200, "
    ":submitter_id) RETURNING id"
)
_INSERT_ENTRY = text(
    "INSERT INTO catalogue_entry (business_key, preferred_term, status) "
    "VALUES (:business_key, 'Comment target', 'draft') RETURNING id"
)
_INSERT_COMMENT = text(
    "INSERT INTO internal_comment (submission_id, entry_id, author_id, body) "
    "VALUES (:submission_id, :entry_id, :author_id, :body) RETURNING id"
)


def _insert_user(connection: Connection) -> object:
    return connection.execute(_INSERT_USER, {"username": f"cmt-{uuid.uuid4()}"}).scalar_one()


def _insert_submission(connection: Connection) -> object:
    return connection.execute(
        _INSERT_SUBMISSION, {"submitter_id": _insert_user(connection)}
    ).scalar_one()


def _insert_entry(connection: Connection) -> object:
    business_key = f"NPTC-9{random.SystemRandom().randrange(10**8):08d}"
    return connection.execute(_INSERT_ENTRY, {"business_key": business_key}).scalar_one()


def _insert_comment(
    connection: Connection,
    *,
    submission_id: object | None = None,
    entry_id: object | None = None,
    author_id: object | None = None,
    body: str = "Check the reference range with the lab.",
) -> object:
    return connection.execute(
        _INSERT_COMMENT,
        {
            "submission_id": submission_id,
            "entry_id": entry_id,
            "author_id": author_id or _insert_user(connection),
            "body": body,
        },
    ).scalar_one()


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_app_role_can_insert_and_select_a_comment_on_a_submission(app_db: Connection) -> None:
    submission_id = _insert_submission(app_db)
    author_id = _insert_user(app_db)

    comment_id = _insert_comment(app_db, submission_id=submission_id, author_id=author_id)

    row = app_db.execute(
        text(
            "SELECT submission_id, entry_id, author_id, body, withdrawn_at "
            "FROM internal_comment WHERE id = :id"
        ),
        {"id": comment_id},
    ).one()
    assert row.submission_id == submission_id
    assert row.entry_id is None
    assert row.author_id == author_id
    assert row.body == "Check the reference range with the lab."
    assert row.withdrawn_at is None


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_app_role_can_insert_and_select_a_comment_on_an_entry(app_db: Connection) -> None:
    entry_id = _insert_entry(app_db)

    comment_id = _insert_comment(app_db, entry_id=entry_id)

    row = app_db.execute(
        text("SELECT submission_id, entry_id FROM internal_comment WHERE id = :id"),
        {"id": comment_id},
    ).one()
    assert row.submission_id is None
    assert row.entry_id == entry_id


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_comment_needs_a_target(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_comment(db)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_comment_cannot_target_both_a_submission_and_an_entry(db: Connection) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_comment(db, submission_id=_insert_submission(db), entry_id=_insert_entry(db))

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("body", ["", "   ", "\n\t "], ids=["empty", "spaces", "whitespace"])
def test_a_comment_body_cannot_be_blank(db: Connection, body: str) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        _insert_comment(db, submission_id=_insert_submission(db), body=body)

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_comment_must_reference_a_real_target_and_author(db: Connection) -> None:
    nil = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(IntegrityError) as exc_info:
        _insert_comment(db, submission_id=nil)

    assert exc_info.value.orig.sqlstate == _FOREIGN_KEY_VIOLATION  # type: ignore[union-attr]


_WITHDRAW = text(
    "UPDATE internal_comment SET withdrawn_at = now(), withdrawn_by = :by, "
    "withdrawn_reason = :reason WHERE id = :id"
)


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_app_role_can_withdraw_a_comment_and_the_text_stays(app_db: Connection) -> None:
    comment_id = _insert_comment(app_db, submission_id=_insert_submission(app_db))
    withdrawer = _insert_user(app_db)

    app_db.execute(_WITHDRAW, {"id": comment_id, "by": withdrawer, "reason": "Posted in error"})

    row = app_db.execute(
        text(
            "SELECT body, withdrawn_at, withdrawn_by, withdrawn_reason "
            "FROM internal_comment WHERE id = :id"
        ),
        {"id": comment_id},
    ).one()
    assert row.body == "Check the reference range with the lab."
    assert row.withdrawn_at is not None
    assert row.withdrawn_by == withdrawer
    assert row.withdrawn_reason == "Posted in error"


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE internal_comment SET withdrawn_at = now() WHERE id = :id",
        "UPDATE internal_comment SET withdrawn_by = author_id WHERE id = :id",
        "UPDATE internal_comment SET withdrawn_reason = 'x' WHERE id = :id",
        "UPDATE internal_comment SET withdrawn_at = now(), withdrawn_by = author_id WHERE id = :id",
        "UPDATE internal_comment SET withdrawn_at = now(), withdrawn_reason = 'x' WHERE id = :id",
    ],
    ids=["only_at", "only_by", "only_reason", "no_reason", "no_by"],
)
def test_a_withdrawal_must_set_all_three_columns_together(db: Connection, statement: str) -> None:
    comment_id = _insert_comment(db, submission_id=_insert_submission(db))

    with pytest.raises(IntegrityError) as exc_info:
        db.execute(text(statement), {"id": comment_id})

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_withdrawal_reason_cannot_be_blank(db: Connection) -> None:
    comment_id = _insert_comment(db, submission_id=_insert_submission(db))

    with pytest.raises(IntegrityError) as exc_info:
        db.execute(_WITHDRAW, {"id": comment_id, "by": _insert_user(db), "reason": "  "})

    assert exc_info.value.orig.sqlstate == _CHECK_VIOLATION  # type: ignore[union-attr]


#: One refused statement per test: a privilege error aborts the transaction. The parameter ids name
#: the column or verb, so a failure says which guarantee broke.
_REFUSED_STATEMENTS = {
    "update_body": "UPDATE internal_comment SET body = 'changed'",
    "update_author": "UPDATE internal_comment SET author_id = author_id",
    "update_created_at": "UPDATE internal_comment SET created_at = now()",
    "update_submission": "UPDATE internal_comment SET submission_id = submission_id",
    "update_entry": "UPDATE internal_comment SET entry_id = entry_id",
    "delete": "DELETE FROM internal_comment",
    "truncate": "TRUNCATE internal_comment",
}


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("statement", _REFUSED_STATEMENTS.values(), ids=_REFUSED_STATEMENTS.keys())
def test_app_role_can_only_withdraw_never_edit_or_remove_a_comment(
    app_db: Connection, statement: str
) -> None:
    _insert_comment(app_db, submission_id=_insert_submission(app_db))

    with pytest.raises(ProgrammingError) as exc_info:
        app_db.execute(text(statement))

    assert exc_info.value.orig.sqlstate == _INSUFFICIENT_PRIVILEGE  # type: ignore[union-attr]


@pytest.mark.req("NFR-08")
def test_the_audit_policy_withholds_the_body_and_records_the_rest() -> None:
    policy = policy_for(InternalComment)

    assert policy.withheld == frozenset({"body"})
    assert policy.auditable == frozenset(
        {
            "submission_id",
            "entry_id",
            "author_id",
            "withdrawn_at",
            "withdrawn_by",
        }
    )
