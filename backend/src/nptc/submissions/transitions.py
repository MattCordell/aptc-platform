"""Changing a submission's state, one mechanism for every move (FR-28, NFR-08).

**The permitted moves are rows in `PERMITTED_TRANSITIONS`,** keyed by `(from_state, to_state)`. A
move that has no row is refused, so a closed submission is never reinterpreted: a second withdrawal
finds `Withdrawn` with no way out. A new state or move is a new row, never a second code path.
Each row names the permission a move needs and whether the actor must be the submitter, so
authorisation is a permission and never a role name (FR-44). `available_transitions` reads the same
table to say which moves a caller may make now, so no screen keeps a second copy of the graph.

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
from nptc.auth.errors_authorisation import MfaRequiredError, PermissionDeniedError
from nptc.auth.permissions import Permission, permissions_for_roles
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
    "TRANSITION_PERMISSIONS",
    "PermittedTransition",
    "TransitionRequirement",
    "available_transitions",
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


_REVIEW = TransitionRequirement(Permission.SUBMISSION_TRANSITION_REVIEW, submitter_only=False)
_WITHDRAW_OWN = TransitionRequirement(Permission.SUBMISSION_WITHDRAW_OWN, submitter_only=True)
_WITHDRAW_ANY = TransitionRequirement(Permission.SUBMISSION_WITHDRAW_ANY, submitter_only=False)
_WITHDRAWN = "submission.withdrawn"

# A review move has its own audit action, so a reader of the log tells a triage from a rejection
# without comparing the two states.
_TRIAGED = "submission.triaged"
_REVIEW_STARTED = "submission.review_started"
_REJECTED = "submission.rejected"
_REOPENED = "submission.reopened"
_READIED_FOR_APPROVAL = "submission.readied_for_approval"

# The Administrator holds `.any` and not `.own`, so an Administrator's own submission needs `.any`
# on the same row as the submitter's `.own`. Only `.any` reaches `Approved` and `Published in
# release`: the submitter's withdrawal ends once a reviewer has accepted the proposal.
_WITHDRAW_OPEN = (_WITHDRAW_OWN, _WITHDRAW_ANY)

PERMITTED_TRANSITIONS: Final[
    Mapping[tuple[SubmissionState, SubmissionState], PermittedTransition]
] = {
    (SubmissionState.SUBMITTED, SubmissionState.TRIAGE): PermittedTransition(_TRIAGED, (_REVIEW,)),
    (SubmissionState.TRIAGE, SubmissionState.UNDER_REVIEW): PermittedTransition(
        _REVIEW_STARTED, (_REVIEW,)
    ),
    (SubmissionState.TRIAGE, SubmissionState.REJECTED): PermittedTransition(_REJECTED, (_REVIEW,)),
    (SubmissionState.REJECTED, SubmissionState.TRIAGE): PermittedTransition(_REOPENED, (_REVIEW,)),
    (SubmissionState.UNDER_REVIEW, SubmissionState.READY_FOR_APPROVAL): PermittedTransition(
        _READIED_FOR_APPROVAL, (_REVIEW,)
    ),
    (SubmissionState.SUBMITTED, SubmissionState.WITHDRAWN): PermittedTransition(
        _WITHDRAWN, _WITHDRAW_OPEN
    ),
    (SubmissionState.TRIAGE, SubmissionState.WITHDRAWN): PermittedTransition(
        _WITHDRAWN, _WITHDRAW_OPEN
    ),
    (SubmissionState.APPROVED, SubmissionState.WITHDRAWN): PermittedTransition(
        _WITHDRAWN, (_WITHDRAW_ANY,)
    ),
    (SubmissionState.PUBLISHED_IN_RELEASE, SubmissionState.WITHDRAWN): PermittedTransition(
        _WITHDRAWN, (_WITHDRAW_ANY,)
    ),
}

#: Every permission some move of the table names. A caller with none of them can make no move, so
#: the route turns them away before the service judges the particular one. Derived from the table, so
#: a new row can never leave the route behind.
TRANSITION_PERMISSIONS: Final[frozenset[Permission]] = frozenset(
    requirement.permission
    for permitted in PERMITTED_TRANSITIONS.values()
    for requirement in permitted.requirements
)


def available_transitions(
    principal: Principal, *, from_state: SubmissionState, submitter_id: uuid.UUID
) -> tuple[SubmissionState, ...]:
    """The states `principal` may move a submission to now, in table order. It applies the test
    `transition_submission` applies, so the list and the endpoint cannot disagree. It does not read
    the reason or the `row_version`, which only the request carries."""
    return tuple(
        to_state
        for (source, to_state), permitted in PERMITTED_TRANSITIONS.items()
        if source is from_state
        and any(
            _met(requirement, principal, submitter_id) for requirement in permitted.requirements
        )
    )


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
    who meets none of the move's requirements (`MfaRequiredError` when a role suppressed for want of
    MFA would grant one), and `SubmissionVersionConflictError` for a stale `expected_row_version`.
    A refusal writes nothing.
    """
    cleaned = clean_transition_reason(reason)
    acquire_append_lock(session)
    submission = _submission(session, submission_id)
    from_state = SubmissionState(submission.state)

    permitted = PERMITTED_TRANSITIONS.get((from_state, to_state))
    if permitted is None:
        raise IllegalSubmissionTransitionError(from_state.value, to_state.value)
    if not any(
        _met(requirement, principal, submission.submitter_id)
        for requirement in permitted.requirements
    ):
        raise _denial(permitted, principal)
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


def _met(requirement: TransitionRequirement, principal: Principal, submitter_id: uuid.UUID) -> bool:
    if not principal.has(requirement.permission):
        return False
    return not requirement.submitter_only or (
        principal.user_id is not None and principal.user_id == submitter_id
    )


def _denial(permitted: PermittedTransition, principal: Principal) -> PermissionDeniedError:
    """`MfaRequiredError` when a role suppressed for want of MFA would grant a permission the move
    names, so a caller who holds another role, and so passes the route gate, is still told that
    stepping up would help. A plain refusal otherwise, as `require_permission` decides."""
    message = _refusal_message(permitted, principal)
    suppressed = permissions_for_roles(principal.mfa_suppressed_roles)
    if any(requirement.permission in suppressed for requirement in permitted.requirements):
        return MfaRequiredError(message)
    return PermissionDeniedError(message)


def _refusal_message(permitted: PermittedTransition, principal: Principal) -> str:
    """Names the real cause for the log: a caller who holds a permission of the move and fails only
    the submitter test is told apart from one who lacks every permission."""
    held = [r for r in permitted.requirements if principal.has(r.permission)]
    if held:
        return f"permission {held[0].permission.value!r} is held, but only the submitter may use it"
    names = " or ".join(repr(r.permission.value) for r in permitted.requirements)
    return f"permission {names} is required for this move"
