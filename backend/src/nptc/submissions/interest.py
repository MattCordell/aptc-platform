"""Registering and revoking interest in a submission, and counting it (FR-32, FR-34, NFR-08, NFR-17).

**Both writers are idempotent, and a repeat writes nothing.** A caller already in the state asked for
gets `False` back, with no row and no audit event, as `nptc.auth.identity.close_account` does:
`record_change` refuses an empty diff, so the no-op returns before it (ADR-0018). A second
registration therefore leaves one live record, and a second revocation changes nothing.

**Each writer takes the audit append lock first**, ahead of every read (`test_lock_ordering.py`). The
lock also serialises two concurrent registrations by one user, so the partial unique index on live
rows is the backstop, never the guard that decides.

**A closed submission's interest is frozen.** `CLOSED_SUBMISSION_STATES` names the closed states, so a
state added later counts as open until it is listed. Registering is refused there, because it would
back a proposal that is over. Revoking is refused too, so the count that stood at closure stays what
it was. Live records on a closed submission keep counting.

**A submitter cannot register on their own submission.** Interest is other people's backing.

**The organisation is a copy taken at registration.** It comes from the caller's profile, and it
outlives the account, which closure pseudonymises and never deletes (NFR-17). Registering again after
a revocation takes a fresh copy, because it is a new record.

**Counts are one grouped statement.** `interest_summaries` serves any number of submissions in a
single query, so a list page costs the same however long it is.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, func, select
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.db.models.submission import CLOSED_SUBMISSION_STATES, Submission
from nptc.db.models.submission_interest import SubmissionInterest
from nptc.submissions.errors import (
    ClosedSubmissionInterestError,
    OwnSubmissionInterestError,
    SubmissionNotFoundError,
)

__all__ = ["InterestSummary", "interest_summaries", "register_interest", "revoke_interest"]


@dataclass(frozen=True, slots=True)
class InterestSummary:
    """The live interest in one submission. `registered` is whether the viewer holds one of it."""

    count: int
    registered: bool


def register_interest(
    session: Session,
    ctx: AuditContext,
    *,
    submission_id: uuid.UUID,
    profile_organisation: str | None,
) -> bool:
    """Records `ctx.actor_user_id`'s interest and appends its audit event. Returns `False`, having
    written nothing, when the caller already holds a live record.

    Raises `SubmissionNotFoundError` for an unknown submission, `OwnSubmissionInterestError` when the
    caller submitted it, and `ClosedSubmissionInterestError` when it is in a closed state.
    """
    acquire_append_lock(session)
    user_id = _actor(ctx)
    submission = _submission(session, submission_id)
    if submission.submitter_id == user_id:
        raise OwnSubmissionInterestError("a submitter cannot register interest in their own work")
    _refuse_if_closed(submission)

    if _live_interest(session, submission_id, user_id) is not None:
        return False
    interest = SubmissionInterest(
        submission_id=submission_id, user_id=user_id, organisation=profile_organisation
    )
    session.add(interest)
    record_change(
        session,
        ctx,
        action="submission_interest.registered",
        instance=interest,
        kind=ChangeKind.CREATED,
    )
    return True


def revoke_interest(session: Session, ctx: AuditContext, *, submission_id: uuid.UUID) -> bool:
    """Ends `ctx.actor_user_id`'s live interest and appends its audit event. Returns `False`,
    having written nothing, when the caller holds none.

    Raises `SubmissionNotFoundError` for an unknown submission and `ClosedSubmissionInterestError` when it
    is in a closed state.
    """
    acquire_append_lock(session)
    user_id = _actor(ctx)
    _refuse_if_closed(_submission(session, submission_id))

    interest = _live_interest(session, submission_id, user_id)
    if interest is None:
        return False
    interest.revoked_at = _clock_now(session)
    record_change(
        session,
        ctx,
        action="submission_interest.revoked",
        instance=interest,
        kind=ChangeKind.UPDATED,
    )
    return True


def interest_summaries(
    session: Session, submission_ids: Collection[uuid.UUID], *, viewer_id: uuid.UUID | None
) -> dict[uuid.UUID, InterestSummary]:
    """One summary per id, in one statement. A submission nobody has registered interest in is
    present with a count of zero, so a caller never needs a default."""
    if not submission_ids:
        return {}
    viewer_rows = func.count().filter(SubmissionInterest.user_id == viewer_id)
    rows = session.execute(
        select(SubmissionInterest.submission_id, func.count(), viewer_rows)
        .where(
            SubmissionInterest.submission_id.in_(submission_ids),
            SubmissionInterest.revoked_at.is_(None),
        )
        .group_by(SubmissionInterest.submission_id)
    ).all()
    found = {
        submission_id: InterestSummary(count=count, registered=viewer_count > 0)
        for submission_id, count, viewer_count in rows
    }
    return {
        submission_id: found.get(submission_id, InterestSummary(count=0, registered=False))
        for submission_id in submission_ids
    }


def _actor(ctx: AuditContext) -> uuid.UUID:
    if ctx.actor_user_id is None:
        raise ValueError("interest belongs to a person, but the audit context has none")
    return ctx.actor_user_id


def _submission(session: Session, submission_id: uuid.UUID) -> Submission:
    submission = session.get(Submission, submission_id)
    if submission is None:
        raise SubmissionNotFoundError("no submission has that identifier")
    return submission


def _refuse_if_closed(submission: Submission) -> None:
    if submission.state in CLOSED_SUBMISSION_STATES:
        raise ClosedSubmissionInterestError(submission.state)


def _live_interest(
    session: Session, submission_id: uuid.UUID, user_id: uuid.UUID
) -> SubmissionInterest | None:
    return session.execute(
        select(SubmissionInterest).where(
            SubmissionInterest.submission_id == submission_id,
            SubmissionInterest.user_id == user_id,
            SubmissionInterest.revoked_at.is_(None),
        )
    ).scalar_one_or_none()


def _clock_now(session: Session) -> datetime:
    """The database clock now, read after the lock is held. `now()` would be the transaction's first
    statement, which can precede the commit of a record this revocation can already see, and the
    `revoked_after_created` check would refuse the revocation."""
    return session.execute(select(func.clock_timestamp(type_=DateTime(timezone=True)))).scalar_one()
