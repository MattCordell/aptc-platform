"""Posting, listing and withdrawing internal comments (FR-95, NFR-08, NFR-23).

**A comment targets a submission or a catalogue entry,** named by `SubmissionTarget` or
`EntryTarget`. An unknown target is a 404, the same refusal the rest of the API gives.

**Nothing is written to the target.** A comment write inserts or updates one `internal_comment` row
and appends one audit event. The submission or entry row is only read, so its `row_version` and
`updated_at` show a submitter nothing about comments.

**The audit event is about the comment,** never the entry. `catalogue_entry` events are published as
the entry's history (`nptc.catalogue.history`), so a comment event recorded under that entity type
would leak. The comment is the entity, and its body is withheld from the event (NFR-26).

**A withdrawal keeps the text.** It sets who, when and why, once. A second withdrawal is refused and
changes nothing, so the first reason stands.

**Each writer takes the audit append lock first,** ahead of every read (`test_lock_ordering.py`). The
lock serialises two withdrawals of one comment, so the second one sees the first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, DateTime, func, select
from sqlalchemy.orm import Session, aliased

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.auth.identity import UserRef
from nptc.catalogue.entries import load_entry_for_update
from nptc.comments.errors import (
    CommentAlreadyWithdrawnError,
    CommentNotFoundError,
    CommentTextRequiredError,
)
from nptc.db.models.internal_comment import InternalComment
from nptc.db.models.submission import Submission
from nptc.db.models.user import User
from nptc.submissions.errors import FreeTextField, SubmissionNotFoundError
from nptc.submissions.shared import clean_free_text

__all__ = [
    "CommentRecord",
    "CommentTarget",
    "EntryTarget",
    "SubmissionTarget",
    "list_comments",
    "post_comment",
    "withdraw_comment",
]


@dataclass(frozen=True, slots=True)
class SubmissionTarget:
    submission_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class EntryTarget:
    business_key: str


CommentTarget = SubmissionTarget | EntryTarget


@dataclass(frozen=True, slots=True)
class CommentRecord:
    """One comment as the API shows it. People come back as `UserRef` (NFR-04), so no internal
    identifier leaves this module. The three `withdrawn_*` values are all set or all `None`."""

    id: uuid.UUID
    body: str
    created_at: datetime
    author: UserRef
    withdrawn_at: datetime | None
    withdrawn_by: UserRef | None
    withdrawn_reason: str | None


def post_comment(
    session: Session, ctx: AuditContext, target: CommentTarget, *, body: str
) -> CommentRecord:
    """Adds a comment to `target` and appends its audit event.

    Raises `CommentTextRequiredError` for a blank body, `FreeTextRefusedError` for one with an
    invisible character, and `SubmissionNotFoundError` or `EntryNotFoundError` for an unknown target.
    A refusal writes nothing.
    """
    cleaned = _clean(body, FreeTextField.BODY)
    acquire_append_lock(session)
    resolved = _resolve(session, target)
    comment = InternalComment(
        author_id=_actor(ctx),
        body=cleaned,
        submission_id=resolved.submission_id,
        entry_id=resolved.entry_id,
    )
    session.add(comment)
    record_change(session, ctx, action="comment.created", instance=comment, kind=ChangeKind.CREATED)
    return _record(session, comment.id)


def list_comments(session: Session, target: CommentTarget) -> tuple[CommentRecord, ...]:
    """Every comment on `target`, oldest first, withdrawn ones included with their text.

    Raises `SubmissionNotFoundError` or `EntryNotFoundError` for an unknown target.
    """
    return tuple(_records(session, _resolve(session, target).matches()))


def withdraw_comment(
    session: Session,
    ctx: AuditContext,
    target: CommentTarget,
    comment_id: uuid.UUID,
    *,
    reason: str,
) -> CommentRecord:
    """Marks the comment withdrawn by `ctx.actor_user_id`, with `reason`, and appends its audit event.
    The text stays.

    Raises `CommentTextRequiredError` for a blank reason, `FreeTextRefusedError` for one with an
    invisible character, `SubmissionNotFoundError` or `EntryNotFoundError` for an unknown target,
    `CommentNotFoundError` for a comment that is not on `target`, and `CommentAlreadyWithdrawnError`
    for one withdrawn before. A refusal writes nothing.
    """
    cleaned = _clean(reason, FreeTextField.REASON)
    acquire_append_lock(session)
    resolved = _resolve(session, target)
    comment = session.execute(
        select(InternalComment)
        .where(InternalComment.id == comment_id, resolved.matches())
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if comment is None:
        raise CommentNotFoundError("no comment has that identifier on that target")
    if comment.withdrawn_at is not None:
        raise CommentAlreadyWithdrawnError("the comment was withdrawn before")

    comment.withdrawn_at = _clock_now(session)
    comment.withdrawn_by = _actor(ctx)
    comment.withdrawn_reason = cleaned
    record_change(
        session,
        ctx,
        action="comment.withdrawn",
        instance=comment,
        kind=ChangeKind.UPDATED,
        reason=cleaned,
    )
    return _record(session, comment_id)


def _clean(text: str, field: FreeTextField) -> str:
    cleaned = clean_free_text(text, field=field, multiline=True)
    if cleaned is None:
        raise CommentTextRequiredError(field)
    return cleaned


def _actor(ctx: AuditContext) -> uuid.UUID:
    if ctx.actor_user_id is None:
        raise ValueError("a comment belongs to a person, but the audit context has none")
    return ctx.actor_user_id


@dataclass(frozen=True, slots=True)
class _Resolved:
    """A target that exists: exactly one of the two ids is set, as the table's check requires."""

    submission_id: uuid.UUID | None = None
    entry_id: uuid.UUID | None = None

    def matches(self) -> ColumnElement[bool]:
        if self.submission_id is not None:
            return InternalComment.submission_id == self.submission_id
        return InternalComment.entry_id == self.entry_id


def _resolve(session: Session, target: CommentTarget) -> _Resolved:
    """`target` as ids, after checking it exists. Both lookups read the row and write nothing, and
    `load_entry_for_update` finds an entry of any status, so a draft can be commented on."""
    if isinstance(target, SubmissionTarget):
        submission = session.get(Submission, target.submission_id)
        if submission is None:
            raise SubmissionNotFoundError("no submission has that identifier")
        return _Resolved(submission_id=submission.id)
    return _Resolved(entry_id=load_entry_for_update(session, target.business_key).id)


def _records(session: Session, *where: ColumnElement[bool]) -> list[CommentRecord]:
    author = aliased(User)
    withdrawer = aliased(User)
    rows = session.execute(
        select(InternalComment, author, withdrawer)
        .join(author, author.id == InternalComment.author_id)
        .outerjoin(withdrawer, withdrawer.id == InternalComment.withdrawn_by)
        .where(*where)
        .order_by(InternalComment.created_at, InternalComment.id)
    ).all()
    return [
        CommentRecord(
            id=comment.id,
            body=comment.body,
            created_at=comment.created_at,
            author=UserRef.from_user(comment_author),
            withdrawn_at=comment.withdrawn_at,
            withdrawn_by=UserRef.from_user(who) if who is not None else None,
            withdrawn_reason=comment.withdrawn_reason,
        )
        for comment, comment_author, who in rows
    ]


def _record(session: Session, comment_id: uuid.UUID) -> CommentRecord:
    (record,) = _records(session, InternalComment.id == comment_id)
    return record


def _clock_now(session: Session) -> datetime:
    """The database clock now, so a withdrawal is stamped by the clock that stamped `created_at`
    (`clock_timestamp()`), not by the application host's."""
    return session.execute(select(func.clock_timestamp(type_=DateTime(timezone=True)))).scalar_one()
