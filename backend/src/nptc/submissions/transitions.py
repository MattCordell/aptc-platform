"""Changing a submission's state, one mechanism for every move (FR-28, NFR-08).

**The permitted moves are rows in `PERMITTED_TRANSITIONS`,** keyed by `(from_state, to_state)`. A
move that has no row is refused, so a closed submission is never reinterpreted: a second withdrawal
finds `Withdrawn` with no way out. A new state or move is a new row, never a second code path.
Each row names the permission a move needs and whether the actor must be the submitter, so
authorisation is a permission and never a role name (FR-44).

**One transaction writes the three records.** The new `state`, a `submission_transition` row and the
audit event succeed or fail together. The audit event carries the reason, and the transition row
carries it too, so a reader of either sees why the submission moved.

**The checks run in a fixed order,** each refusing before anything is written: a malformed reason,
an unknown submission (404), a move with no row (409), a caller who may not make it (403), then a
stale `row_version` (409). The move comes before the permission because a permission belongs to a
move, so a caller cannot be judged until the move is known.

**The audit append lock is taken first,** ahead of every read (`test_lock_ordering.py`). It also
serialises two writers of one submission, so the version check decides and `StaleDataError` is only
the backstop for a writer that skipped the lock.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.auth.errors_authorisation import PermissionDeniedError
from nptc.auth.permissions import Permission
from nptc.auth.principal import Principal
from nptc.db.models.submission import Submission, SubmissionState
from nptc.db.models.submission_transition import SubmissionTransition
from nptc.submissions.errors import (
    FreeTextField,
    IllegalSubmissionTransitionError,
    SubmissionNotFoundError,
    SubmissionVersionConflictError,
    TransitionReasonRequiredError,
)
from nptc.submissions.shared import clean_free_text

__all__ = [
    "PERMITTED_TRANSITIONS",
    "PermittedTransition",
    "TransitionRequirement",
    "clean_transition_reason",
    "transition_submission",
]


@dataclass(frozen=True, slots=True)
class TransitionRequirement:
    """One way an actor may make a move: they hold `permission`, and, when `submitter_only`, they
    are the submission's submitter. An actor who meets any one requirement of a move may make it."""

    permission: Permission
    submitter_only: bool


@dataclass(frozen=True, slots=True)
class PermittedTransition:
    """A move the platform allows. `action` is the audit action the move emits."""

    action: str
    requirements: tuple[TransitionRequirement, ...]


_WITHDRAW_OWN = TransitionRequirement(Permission.SUBMISSION_WITHDRAW_OWN, submitter_only=True)
_WITHDRAWN = "submission.withdrawn"

PERMITTED_TRANSITIONS: Final[
    Mapping[tuple[SubmissionState, SubmissionState], PermittedTransition]
] = {
    (SubmissionState.SUBMITTED, SubmissionState.WITHDRAWN): PermittedTransition(
        _WITHDRAWN, (_WITHDRAW_OWN,)
    ),
    (SubmissionState.TRIAGE, SubmissionState.WITHDRAWN): PermittedTransition(
        _WITHDRAWN, (_WITHDRAW_OWN,)
    ),
}


def clean_transition_reason(reason: str) -> str:
    """`reason` cleaned as a note is (FR-63), or a refusal. It touches no session, so
    `transition_submission` runs it ahead of the append lock and a rejected reason takes none."""
    cleaned = clean_free_text(reason, field=FreeTextField.REASON, multiline=True)
    if cleaned is None:
        raise TransitionReasonRequiredError("a change of state needs a reason")
    return cleaned


def transition_submission(
    session: Session,
    ctx: AuditContext,
    principal: Principal,
    *,
    submission_id: uuid.UUID,
    to_state: SubmissionState,
    reason: str,
    expected_row_version: int,
) -> Submission:
    """Moves the submission to `to_state`, writes the transition row and appends the audit event.
    Returns the submission as it now stands.

    Raises `TransitionReasonRequiredError` for a blank reason, `FreeTextRefusedError` for one with an
    invisible character, `SubmissionNotFoundError` for an unknown submission,
    `IllegalSubmissionTransitionError` for a move with no row, `PermissionDeniedError` for a caller
    who meets none of the move's requirements, and `SubmissionVersionConflictError` for a stale
    `expected_row_version`. A refusal writes nothing.
    """
    cleaned = clean_transition_reason(reason)
    acquire_append_lock(session)
    submission = _submission(session, submission_id)
    from_state = SubmissionState(submission.state)

    permitted = PERMITTED_TRANSITIONS.get((from_state, to_state))
    if permitted is None:
        raise IllegalSubmissionTransitionError(from_state.value, to_state.value)
    if not any(_met(requirement, principal, submission) for requirement in permitted.requirements):
        raise PermissionDeniedError(_refusal_message(permitted, principal))
    if submission.row_version != expected_row_version:
        raise SubmissionVersionConflictError(
            expected_row_version=expected_row_version, current_row_version=submission.row_version
        )

    actor_id = principal.user_id
    if actor_id is None:
        raise ValueError("a change of state belongs to a person, but the principal has none")

    savepoint = session.begin_nested()
    try:
        submission.state = to_state.value
        session.add(
            SubmissionTransition(
                submission_id=submission.id,
                from_state=from_state.value,
                to_state=to_state.value,
                actor_id=actor_id,
                reason=cleaned,
            )
        )
        record_change(
            session,
            ctx,
            action=permitted.action,
            instance=submission,
            kind=ChangeKind.UPDATED,
            reason=cleaned,
        )
    except StaleDataError, ObjectDeletedError:
        savepoint.rollback()
        session.expire(submission)
        current = _submission(session, submission_id)
        raise SubmissionVersionConflictError(
            expected_row_version=expected_row_version, current_row_version=current.row_version
        ) from None
    except BaseException:
        savepoint.rollback()
        raise
    savepoint.commit()
    return submission


def _submission(session: Session, submission_id: uuid.UUID) -> Submission:
    # `populate_existing`: the lock is held, so the row read now is the committed one. A copy
    # already in the session's identity map would otherwise be returned without a query.
    submission = session.get(Submission, submission_id, populate_existing=True)
    if submission is None:
        raise SubmissionNotFoundError("no submission has that identifier")
    return submission


def _met(requirement: TransitionRequirement, principal: Principal, submission: Submission) -> bool:
    if not principal.has(requirement.permission):
        return False
    return not requirement.submitter_only or (
        principal.user_id is not None and principal.user_id == submission.submitter_id
    )


def _refusal_message(permitted: PermittedTransition, principal: Principal) -> str:
    """Names the real cause for the log: a caller who holds a permission of the move and fails only
    the submitter test is told apart from one who lacks every permission."""
    held = [r for r in permitted.requirements if principal.has(r.permission)]
    if held:
        return f"permission {held[0].permission.value!r} is held, but only the submitter may use it"
    names = " or ".join(repr(r.permission.value) for r in permitted.requirements)
    return f"permission {names} is required for this move"
