"""The submission read layer (FR-28, FR-42).

Every submission list and detail the API serves is read through this module, in one joined
statement that also carries the submitter and the amended entry's business key, so a page costs a
fixed number of queries whatever its size.

**"Open" is "not closed".** With no state filter, a read excludes `CLOSED_SUBMISSION_STATES` and
nothing else, so a state added later is open without a change here. `mine` with no state filter
excludes nothing.

**The submitter comes back as a `UserRef`** (NFR-04): the internal id stays in this module. Whether
the caller may see the name or the organisation is not decided here; the route applies FR-42's rule
to what this module returns.

Keyset paging on `(created_at, id)`, newest first, never `OFFSET` (ADR-0024). `id` breaks a tie
between two rows saved in one instant, so the order is total. The cursor is opaque to a client.

**The terminology request is a separate read too (FR-30).** `get_terminology_request` returns the
current request, the newest row, or `None` when the submission never waited for terminology, and
only a caller who may see it asks.

**The working copy is a separate read (FR-23).** `get_working_copy` returns the reviewer's edited
copy or `None`. Only a caller who may see it asks, so a response for anyone else never reads the
table.

**A submission's transition history is not paged.** One submission changes state a handful of times,
so `list_transitions` returns every row, oldest first, in one joined statement. It reads
`submission_transition` and the actor's user row and nothing else, so no comment text can reach it
(FR-95).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import Select, select, tuple_
from sqlalchemy.orm import Session

from nptc.auth.identity import UserRef
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.submission import CLOSED_SUBMISSION_STATES, Submission
from nptc.db.models.submission_terminology_request import SubmissionTerminologyRequest
from nptc.db.models.submission_transition import SubmissionTransition
from nptc.db.models.submission_working_copy import SubmissionWorkingCopy
from nptc.db.models.user import User
from nptc.submissions.errors import MalformedSubmissionCursorError, SubmissionNotFoundError

__all__ = [
    "SubmissionRecord",
    "SubmissionRecordPage",
    "TransitionRecord",
    "get_submission",
    "get_terminology_request",
    "get_working_copy",
    "list_submissions",
    "list_transitions",
]

_CURSOR_SEPARATOR: Final = "|"


@dataclass(frozen=True, slots=True)
class SubmissionRecord:
    """One submission with what a response needs beside it. `is_own` is decided here, from the
    stored submitter and the caller's id, so no caller needs the submitter's id."""

    submission: Submission
    entry_business_key: str | None
    submitter: UserRef
    is_own: bool


@dataclass(frozen=True, slots=True)
class SubmissionRecordPage:
    """`next_cursor` is `None` exactly when this is the last page, decided by the one extra row
    `list_submissions` asked for."""

    records: tuple[SubmissionRecord, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """One change of state. `actor` is a `UserRef`, so the actor's internal id stays in this module."""

    from_state: str
    to_state: str
    reason: str
    created_at: datetime
    actor: UserRef


def _encode_cursor(created_at: datetime, submission_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}{_CURSOR_SEPARATOR}{submission_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Refuses anything `_encode_cursor` did not produce. Checking that a decoded cursor encodes
    back to the same text rules out the many spellings `fromisoformat` and `UUID` would accept."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_text, _, id_text = (
            base64.urlsafe_b64decode(padded).decode().partition(_CURSOR_SEPARATOR)
        )
        created_at = datetime.fromisoformat(created_text)
        submission_id = uuid.UUID(id_text)
    except binascii.Error, UnicodeDecodeError, ValueError:
        raise MalformedSubmissionCursorError("submission cursor is not well formed") from None
    if created_at.tzinfo is None or _encode_cursor(created_at, submission_id) != cursor:
        raise MalformedSubmissionCursorError("submission cursor is not one this API issued")
    return created_at, submission_id


def _joined() -> Select[Submission, str, User]:
    return (
        select(Submission, CatalogueEntry.business_key, User)
        .join(User, User.id == Submission.submitter_id)
        .outerjoin(CatalogueEntry, CatalogueEntry.id == Submission.entry_id)
    )


def _record(
    submission: Submission,
    entry_business_key: str | None,
    user: User,
    *,
    viewer_id: uuid.UUID | None,
) -> SubmissionRecord:
    return SubmissionRecord(
        submission=submission,
        entry_business_key=entry_business_key,
        submitter=UserRef.from_user(user),
        is_own=viewer_id is not None and submission.submitter_id == viewer_id,
    )


def list_submissions(
    session: Session,
    *,
    viewer_id: uuid.UUID | None,
    limit: int,
    after: str | None = None,
    states: Sequence[str] = (),
    mine: bool = False,
    kind: str | None = None,
) -> SubmissionRecordPage:
    """One page of submissions, newest first.

    With no `states` the page holds every open state, except that `mine` lifts the default: a
    submitter's own list is their whole history, closed submissions included. A non-empty `states`
    is taken as given in both cases.
    """
    statement = _joined()
    if states:
        statement = statement.where(Submission.state.in_(states))
    elif not mine:
        statement = statement.where(Submission.state.not_in(CLOSED_SUBMISSION_STATES))
    if mine:
        statement = statement.where(Submission.submitter_id == viewer_id)
    if kind is not None:
        statement = statement.where(Submission.kind == kind)
    if after is not None:
        statement = statement.where(
            tuple_(Submission.created_at, Submission.id) < tuple_(*_decode_cursor(after))
        )
    rows = session.execute(
        statement.order_by(Submission.created_at.desc(), Submission.id.desc()).limit(limit + 1)
    ).all()
    records = tuple(_record(*row, viewer_id=viewer_id) for row in rows)
    if len(records) > limit:
        page = records[:limit]
        last = page[-1].submission
        return SubmissionRecordPage(
            records=page, next_cursor=_encode_cursor(last.created_at, last.id)
        )
    return SubmissionRecordPage(records=records, next_cursor=None)


def get_submission(
    session: Session, submission_id: uuid.UUID, *, viewer_id: uuid.UUID | None
) -> SubmissionRecord:
    """One submission in any state, or `SubmissionNotFoundError`."""
    row = session.execute(_joined().where(Submission.id == submission_id)).one_or_none()
    if row is None:
        raise SubmissionNotFoundError("no submission has that identifier")
    return _record(*row, viewer_id=viewer_id)


def list_transitions(session: Session, submission_id: uuid.UUID) -> tuple[TransitionRecord, ...]:
    """Every change of state of one submission, oldest first, or `SubmissionNotFoundError`. A
    submission that has never changed state gives an empty tuple. `id` breaks a tie between two rows
    written in one instant, so the order is total."""
    if session.scalar(select(Submission.id).where(Submission.id == submission_id)) is None:
        raise SubmissionNotFoundError("no submission has that identifier")
    rows = session.execute(
        select(SubmissionTransition, User)
        .join(User, User.id == SubmissionTransition.actor_id)
        .where(SubmissionTransition.submission_id == submission_id)
        .order_by(SubmissionTransition.created_at, SubmissionTransition.id)
    ).all()
    return tuple(
        TransitionRecord(
            from_state=transition.from_state,
            to_state=transition.to_state,
            reason=transition.reason,
            created_at=transition.created_at,
            actor=UserRef.from_user(user),
        )
        for transition, user in rows
    )


def get_working_copy(session: Session, submission_id: uuid.UUID) -> SubmissionWorkingCopy | None:
    """The submission's working copy, or `None` when no reviewer has edited it."""
    return session.get(SubmissionWorkingCopy, submission_id)


def get_terminology_request(
    session: Session, submission_id: uuid.UUID, *, populate_existing: bool = False
) -> SubmissionTerminologyRequest | None:
    """The submission's current terminology request, the newest of its rows, or `None` when it has
    never waited for terminology. An earlier request stays as history. A writer that holds the
    append lock passes `populate_existing`, so the row read is the committed one."""
    return session.execute(
        select(SubmissionTerminologyRequest)
        .where(SubmissionTerminologyRequest.submission_id == submission_id)
        .order_by(
            SubmissionTerminologyRequest.created_at.desc(), SubmissionTerminologyRequest.id.desc()
        )
        .limit(1),
        execution_options={"populate_existing": populate_existing},
    ).scalar_one_or_none()
