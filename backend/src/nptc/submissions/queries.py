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
from nptc.db.models.user import User
from nptc.submissions.errors import MalformedSubmissionCursorError, SubmissionNotFoundError

__all__ = ["SubmissionRecord", "SubmissionRecordPage", "get_submission", "list_submissions"]

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
