"""`nptc.comments.service` tests (FR-95, FR-63, NFR-08).

Each refusal asserts that no comment row and no audit event was added. `backend/tests` shares one
Postgres container (see `CLAUDE.md`), so every query is scoped to the rows this test created, and an
audit event is found by the id of the comment it describes.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.audit.diffing import REDACTED_KEY
from nptc.audit.writer import AuditContext
from nptc.catalogue.errors import EntryNotFoundError
from nptc.comments.errors import (
    CommentAlreadyWithdrawnError,
    CommentNotFoundError,
    CommentTextRequiredError,
)
from nptc.comments.service import (
    CommentTarget,
    EntryTarget,
    SubmissionTarget,
    list_comments,
    post_comment,
    withdraw_comment,
)
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.internal_comment import InternalComment
from nptc.db.models.submission import Submission, SubmissionKind
from nptc.db.models.user import User
from nptc.submissions.errors import FreeTextField, FreeTextRefusedError, SubmissionNotFoundError

_BODY = "Check the reference range with the lab."
_REASON = "Posted on the wrong submission"
_ZERO_WIDTH_SPACE = chr(0x200B)


def _context(user: User) -> AuditContext:
    return AuditContext(
        actor_user_id=user.id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )


def _user(session: Session) -> User:
    user = User(username=f"comment-{uuid.uuid4()}", display_name="Comment Tester")
    session.add(user)
    session.flush()
    return user


def _submission(session: Session, submitter: User) -> Submission:
    submission = Submission(
        kind=SubmissionKind.NEW_TEST.value,
        preferred_term=f"Comment submission {uuid.uuid4()}",
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=submitter.id,
        organisation="Submitter Lab",
    )
    session.add(submission)
    session.flush()
    return submission


def _entry(session: Session, *, status: CatalogueEntryStatus) -> CatalogueEntry:
    entry = CatalogueEntry(
        business_key=f"NPTC-9{random.SystemRandom().randrange(10**8):08d}",
        preferred_term=f"Comment entry {uuid.uuid4()}",
        status=status.value,
    )
    session.add(entry)
    session.flush()
    return entry


def _comments(session: Session, **target: uuid.UUID) -> list[InternalComment]:
    (column, value), *_ = target.items()
    return list(
        session.execute(
            select(InternalComment).where(getattr(InternalComment, column) == value)
        ).scalars()
    )


def _events(session: Session, comment_id: uuid.UUID) -> list[AuditEvent]:
    return list(
        session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == "internal_comment",
                AuditEvent.entity_id == str(comment_id),
            )
            .order_by(AuditEvent.sequence)
        ).scalars()
    )


def _events_naming(session: Session, target_id: uuid.UUID) -> list[AuditEvent]:
    """Events that name `target_id` under the target's own entity type, which a comment must never
    add: the public history of an entry is built from them (`nptc.catalogue.history`)."""
    return list(
        session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type.in_(["catalogue_entry", "submission"]),
                AuditEvent.entity_id == str(target_id),
            )
        ).scalars()
    )


# --- posting (FR-95, NFR-08) ---------------------------------------------------------------


@pytest.mark.req("FR-95")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_comment_on_a_submission_is_stored_and_audited_once(app_session: Session) -> None:
    author = _user(app_session)
    submission = _submission(app_session, _user(app_session))

    record = post_comment(
        app_session, _context(author), SubmissionTarget(submission.id), body=_BODY
    )

    assert record.body == _BODY
    assert record.author.username == author.username
    assert record.withdrawn_at is None
    assert record.withdrawn_by is None
    assert record.withdrawn_reason is None
    [row] = _comments(app_session, submission_id=submission.id)
    assert row.id == record.id
    assert row.entry_id is None
    [event] = _events(app_session, record.id)
    assert event.action == "comment.created"
    assert event.actor_user_id == author.id


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize(
    "status",
    [CatalogueEntryStatus.DRAFT, CatalogueEntryStatus.ACTIVE],
    ids=["draft", "active"],
)
def test_a_comment_on_an_entry_is_stored_whatever_the_status(
    app_session: Session, status: CatalogueEntryStatus
) -> None:
    entry = _entry(app_session, status=status)

    record = post_comment(
        app_session, _context(_user(app_session)), EntryTarget(entry.business_key), body=_BODY
    )

    [row] = _comments(app_session, entry_id=entry.id)
    assert row.id == record.id
    assert row.submission_id is None


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_the_audit_event_records_that_a_body_was_set_and_never_what_it_said(
    app_session: Session,
) -> None:
    submission = _submission(app_session, _user(app_session))

    record = post_comment(
        app_session,
        _context(_user(app_session)),
        SubmissionTarget(submission.id),
        body="An unmistakable sentence",
    )

    [event] = _events(app_session, record.id)
    assert event.after is not None
    assert event.after[REDACTED_KEY] == ["body"]
    assert "An unmistakable sentence" not in str(event.after)
    assert event.before is None


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_comment_adds_no_event_under_the_entry_or_submission(app_session: Session) -> None:
    entry = _entry(app_session, status=CatalogueEntryStatus.ACTIVE)
    submission = _submission(app_session, _user(app_session))
    before_entry = _events_naming(app_session, entry.id)
    before_submission = _events_naming(app_session, submission.id)

    author = _context(_user(app_session))
    post_comment(app_session, author, EntryTarget(entry.business_key), body=_BODY)
    post_comment(app_session, author, SubmissionTarget(submission.id), body=_BODY)

    assert _events_naming(app_session, entry.id) == before_entry
    assert _events_naming(app_session, submission.id) == before_submission


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_comment_does_not_touch_the_row_it_is_on(app_session: Session) -> None:
    entry = _entry(app_session, status=CatalogueEntryStatus.ACTIVE)
    submission = _submission(app_session, _user(app_session))
    seen = (entry.row_version, entry.updated_at, submission.row_version, submission.updated_at)

    author = _context(_user(app_session))
    post_comment(app_session, author, EntryTarget(entry.business_key), body=_BODY)
    post_comment(app_session, author, SubmissionTarget(submission.id), body=_BODY)
    app_session.expire_all()

    assert (
        entry.row_version,
        entry.updated_at,
        submission.row_version,
        submission.updated_at,
    ) == seen


# --- cleaning (FR-63, NFR-23) --------------------------------------------------------------


@pytest.mark.req("FR-95")
@pytest.mark.req("NFR-23")
@pytest.mark.integration
def test_markup_is_stored_and_returned_as_literal_text(app_session: Session) -> None:
    submission = _submission(app_session, _user(app_session))
    markup = '<script>alert("x")</script> <b>bold</b> &amp;'

    record = post_comment(
        app_session, _context(_user(app_session)), SubmissionTarget(submission.id), body=markup
    )

    assert record.body == markup
    [row] = _comments(app_session, submission_id=submission.id)
    assert row.body == markup


@pytest.mark.req("FR-95")
@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_line_breaks_are_kept_and_surrounding_space_is_trimmed(app_session: Session) -> None:
    submission = _submission(app_session, _user(app_session))

    record = post_comment(
        app_session,
        _context(_user(app_session)),
        SubmissionTarget(submission.id),
        body="  first line\nsecond line \n",
    )

    assert record.body == "first line\nsecond line"


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("body", ["", "   ", "\n\t  \n"], ids=["empty", "spaces", "whitespace"])
def test_a_blank_body_is_refused_and_writes_nothing(app_session: Session, body: str) -> None:
    submission = _submission(app_session, _user(app_session))

    with pytest.raises(CommentTextRequiredError) as refused:
        post_comment(
            app_session, _context(_user(app_session)), SubmissionTarget(submission.id), body=body
        )

    assert refused.value.field is FreeTextField.COMMENT
    assert _comments(app_session, submission_id=submission.id) == []


@pytest.mark.req("FR-95")
@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_a_body_with_an_invisible_character_is_refused_and_writes_nothing(
    app_session: Session,
) -> None:
    submission = _submission(app_session, _user(app_session))

    with pytest.raises(FreeTextRefusedError) as refused:
        post_comment(
            app_session,
            _context(_user(app_session)),
            SubmissionTarget(submission.id),
            body=f"hidden{_ZERO_WIDTH_SPACE}text",
        )

    assert refused.value.field is FreeTextField.COMMENT
    assert _comments(app_session, submission_id=submission.id) == []


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize(
    "target, refusal",
    [
        (SubmissionTarget(uuid.UUID(int=0)), SubmissionNotFoundError),
        (EntryTarget("NPTC-999999999"), EntryNotFoundError),
    ],
    ids=["submission", "entry"],
)
def test_an_unknown_target_is_refused(
    app_session: Session, target: CommentTarget, refusal: type[Exception]
) -> None:
    ctx = _context(_user(app_session))

    with pytest.raises(refusal):
        post_comment(app_session, ctx, target, body=_BODY)
    with pytest.raises(refusal):
        list_comments(app_session, target)
    with pytest.raises(refusal):
        withdraw_comment(app_session, ctx, target, uuid.uuid4(), reason=_REASON)


# --- listing (FR-95) -----------------------------------------------------------------------


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_listing_holds_one_targets_comments_oldest_first(app_session: Session) -> None:
    submission = _submission(app_session, _user(app_session))
    other = _submission(app_session, _user(app_session))
    ctx = _context(_user(app_session))
    first = post_comment(app_session, ctx, SubmissionTarget(submission.id), body="one")
    second = post_comment(app_session, ctx, SubmissionTarget(submission.id), body="two")
    post_comment(app_session, ctx, SubmissionTarget(other.id), body="elsewhere")

    listed = list_comments(app_session, SubmissionTarget(submission.id))

    assert [record.id for record in listed] == [first.id, second.id]


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_target_with_no_comments_lists_none(app_session: Session) -> None:
    entry = _entry(app_session, status=CatalogueEntryStatus.ACTIVE)

    assert list_comments(app_session, EntryTarget(entry.business_key)) == ()


# --- withdrawing (FR-95, NFR-08) -----------------------------------------------------------


@pytest.mark.req("FR-95")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_withdrawal_keeps_the_text_and_is_audited_once(app_session: Session) -> None:
    submission = _submission(app_session, _user(app_session))
    target = SubmissionTarget(submission.id)
    posted = post_comment(app_session, _context(_user(app_session)), target, body=_BODY)
    withdrawer = _user(app_session)

    withdrawn = withdraw_comment(
        app_session, _context(withdrawer), target, posted.id, reason=_REASON
    )

    assert withdrawn.body == _BODY
    assert withdrawn.withdrawn_reason == _REASON
    assert withdrawn.withdrawn_at is not None
    assert withdrawn.withdrawn_by is not None
    assert withdrawn.withdrawn_by.username == withdrawer.username
    assert list_comments(app_session, target)[0] == withdrawn
    created, event = _events(app_session, posted.id)
    assert created.action == "comment.created"
    assert event.action == "comment.withdrawn"
    assert event.reason == _REASON
    assert event.actor_user_id == withdrawer.id
    assert event.after is not None
    assert event.after["withdrawn_reason"] == _REASON
    assert "body" not in event.after


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_second_withdrawal_is_refused_and_the_first_reason_stands(app_session: Session) -> None:
    submission = _submission(app_session, _user(app_session))
    target = SubmissionTarget(submission.id)
    posted = post_comment(app_session, _context(_user(app_session)), target, body=_BODY)
    ctx = _context(_user(app_session))
    first = withdraw_comment(app_session, ctx, target, posted.id, reason=_REASON)

    with pytest.raises(CommentAlreadyWithdrawnError):
        withdraw_comment(app_session, ctx, target, posted.id, reason="A different reason")

    assert list_comments(app_session, target) == (first,)
    assert len(_events(app_session, posted.id)) == 2


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("reason", ["", "  ", "\n\t"], ids=["empty", "spaces", "whitespace"])
def test_a_blank_reason_is_refused_and_withdraws_nothing(app_session: Session, reason: str) -> None:
    submission = _submission(app_session, _user(app_session))
    target = SubmissionTarget(submission.id)
    ctx = _context(_user(app_session))
    posted = post_comment(app_session, ctx, target, body=_BODY)

    with pytest.raises(CommentTextRequiredError) as refused:
        withdraw_comment(app_session, ctx, target, posted.id, reason=reason)

    assert refused.value.field is FreeTextField.REASON
    assert list_comments(app_session, target) == (posted,)
    assert len(_events(app_session, posted.id)) == 1


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_reason_with_an_invisible_character_is_refused(app_session: Session) -> None:
    submission = _submission(app_session, _user(app_session))
    target = SubmissionTarget(submission.id)
    ctx = _context(_user(app_session))
    posted = post_comment(app_session, ctx, target, body=_BODY)

    with pytest.raises(FreeTextRefusedError) as refused:
        withdraw_comment(app_session, ctx, target, posted.id, reason=f"hid{_ZERO_WIDTH_SPACE}den")

    assert refused.value.field is FreeTextField.REASON
    assert list_comments(app_session, target) == (posted,)


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_comment_cannot_be_withdrawn_through_another_target(app_session: Session) -> None:
    submission = _submission(app_session, _user(app_session))
    other = _submission(app_session, _user(app_session))
    entry = _entry(app_session, status=CatalogueEntryStatus.ACTIVE)
    ctx = _context(_user(app_session))
    posted = post_comment(app_session, ctx, SubmissionTarget(submission.id), body=_BODY)

    for wrong in (SubmissionTarget(other.id), EntryTarget(entry.business_key)):
        with pytest.raises(CommentNotFoundError):
            withdraw_comment(app_session, ctx, wrong, posted.id, reason=_REASON)

    assert list_comments(app_session, SubmissionTarget(submission.id)) == (posted,)
    assert len(_events(app_session, posted.id)) == 1


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_an_unknown_comment_is_refused(app_session: Session) -> None:
    submission = _submission(app_session, _user(app_session))

    with pytest.raises(CommentNotFoundError):
        withdraw_comment(
            app_session,
            _context(_user(app_session)),
            SubmissionTarget(submission.id),
            uuid.uuid4(),
            reason=_REASON,
        )
