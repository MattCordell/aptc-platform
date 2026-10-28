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

**A move that needs more than a reason has its own service** (`needs_payload`): the two moves of
`Awaiting terminology` (FR-30) carry a record or a code, and approval (FR-28, FR-29) writes the
catalogue entry. They are rows of the table, so the graph, the permissions and `available_transitions`
stay in one place, but `transition_submission` refuses them once the permission is met.
`aptc.submissions.terminology_request` and `aptc.submissions.approval` make them through `check_move`
and `perform_move`, the two halves of this module's own steps.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from aptc.audit.diffing import ChangeKind
from aptc.audit.recording import record_change
from aptc.audit.writer import AuditContext, acquire_append_lock
from aptc.auth.errors_authorisation import MfaRequiredError, PermissionDeniedError
from aptc.auth.permissions import Permission, permissions_for_roles
from aptc.auth.principal import Principal
from aptc.db.models.submission import Submission, SubmissionState
from aptc.db.models.submission_transition import SubmissionTransition
from aptc.submissions.errors import (
    FreeTextField,
    IllegalSubmissionTransitionError,
    SubmissionNotFoundError,
    SubmissionVersionConflictError,
    TransitionNeedsOwnRouteError,
    TransitionReasonRequiredError,
)
from aptc.submissions.shared import clean_free_text

__all__ = [
    "PERMITTED_TRANSITIONS",
    "TRANSITION_PERMISSIONS",
    "PermittedTransition",
    "TransitionRequirement",
    "available_transitions",
    "check_move",
    "check_row_version",
    "clean_transition_reason",
    "perform_move",
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
    """A move the platform allows. `action` is the audit action the move emits. `needs_payload` is
    true for a move that carries more than a reason, which `transition_submission` refuses and its
    own service makes."""

    action: str
    requirements: tuple[TransitionRequirement, ...]
    needs_payload: bool = False


_REVIEW = TransitionRequirement(Permission.SUBMISSION_TRANSITION_REVIEW, submitter_only=False)
_APPROVE = TransitionRequirement(Permission.SUBMISSION_TRANSITION_APPROVE, submitter_only=False)
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
_TERMINOLOGY_REQUESTED = "submission.terminology_requested"
_TERMINOLOGY_ATTACHED = "submission.terminology_attached"
_REVIEW_RESUMED = "submission.review_resumed"
_APPROVED = "submission.approved"

# The Administrator holds `.any` and not `.own`, so an Administrator's own submission needs `.any`
# on the same row as the submitter's `.own`. Only `.any` reaches `Approved` and `Published in
# release`. The submitter's withdrawal also stops at `Under review`, because `Awaiting terminology`
# and `Ready for approval` have no `Withdrawn` row.
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
    # Only an Administrator takes a submission out of `Awaiting terminology` (PRD section 4.5): the
    # code it waits for is the one an Administrator checks and attaches.
    (SubmissionState.UNDER_REVIEW, SubmissionState.AWAITING_TERMINOLOGY): PermittedTransition(
        _TERMINOLOGY_REQUESTED, (_REVIEW,), needs_payload=True
    ),
    (SubmissionState.AWAITING_TERMINOLOGY, SubmissionState.READY_FOR_APPROVAL): PermittedTransition(
        _TERMINOLOGY_ATTACHED, (_APPROVE,), needs_payload=True
    ),
    # Approval writes the catalogue entry in the same transaction (FR-28, FR-29), so it carries
    # the Administrator's reason as the changelog note and has its own service.
    (SubmissionState.READY_FOR_APPROVAL, SubmissionState.APPROVED): PermittedTransition(
        _APPROVED, (_APPROVE,), needs_payload=True
    ),
    # A reviewer can close a proposal they have taken up, and one waiting for approval that an
    # Administrator will not give, so neither state is a dead end.
    (SubmissionState.UNDER_REVIEW, SubmissionState.REJECTED): PermittedTransition(
        _REJECTED, (_REVIEW,)
    ),
    (SubmissionState.READY_FOR_APPROVAL, SubmissionState.REJECTED): PermittedTransition(
        _REJECTED, (_REVIEW,)
    ),
    # A request the authority declines, or one nobody pursues, must not strand the submission: the
    # reviewer takes it back to `Under review`. The record stays as the history of the request.
    (SubmissionState.AWAITING_TERMINOLOGY, SubmissionState.UNDER_REVIEW): PermittedTransition(
        _REVIEW_RESUMED, (_REVIEW,)
    ),
    (SubmissionState.SUBMITTED, SubmissionState.WITHDRAWN): PermittedTransition(
        _WITHDRAWN, _WITHDRAW_OPEN
    ),
    (SubmissionState.TRIAGE, SubmissionState.WITHDRAWN): PermittedTransition(
        _WITHDRAWN, _WITHDRAW_OPEN
    ),
    (SubmissionState.UNDER_REVIEW, SubmissionState.WITHDRAWN): PermittedTransition(
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
    MFA would grant one), `TransitionNeedsOwnRouteError` for a move that carries more than a reason,
    and `SubmissionVersionConflictError` for a stale `expected_row_version`. A refusal writes
    nothing.
    """
    cleaned = clean_transition_reason(reason)
    acquire_append_lock(session)
    submission = load_submission_locked(session, submission_id)

    permitted = check_move(principal, submission, to_state)
    if permitted.needs_payload:
        raise TransitionNeedsOwnRouteError(submission.state, to_state.value)
    check_row_version(submission, expected_row_version)

    return perform_move(
        session,
        principal,
        submission,
        to_state=to_state,
        reason=cleaned,
        expected_row_version=expected_row_version,
        write=lambda: record_change(
            session,
            ctx,
            action=permitted.action,
            instance=submission,
            kind=ChangeKind.UPDATED,
            reason=cleaned,
        ),
    )


def load_submission_locked(session: Session, submission_id: uuid.UUID) -> Submission:
    """The submission as committed, or `SubmissionNotFoundError`. Call it with the append lock held:
    `populate_existing` makes the read reach the database, because a copy already in the session's
    identity map would otherwise be returned without a query."""
    submission = session.get(Submission, submission_id, populate_existing=True)
    if submission is None:
        raise SubmissionNotFoundError("no submission has that identifier")
    return submission


def check_move(
    principal: Principal,
    submission: Submission,
    to_state: SubmissionState,
    *,
    required_from: SubmissionState | None = None,
) -> PermittedTransition:
    """The table row for moving `submission` to `to_state`, once the caller may make the move.
    `required_from` names the one state a caller of a dedicated service accepts, so a submission
    elsewhere is refused as having no such move even when the table has another row to `to_state`.

    Raises `IllegalSubmissionTransitionError` for a move with no row, then `PermissionDeniedError`
    (`MfaRequiredError` when a role suppressed for want of MFA would grant a permission of the move)
    for a caller who meets none of its requirements. The move comes first because a permission
    belongs to a move."""
    from_state = SubmissionState(submission.state)
    permitted = PERMITTED_TRANSITIONS.get((from_state, to_state))
    if permitted is None or (required_from is not None and from_state is not required_from):
        raise IllegalSubmissionTransitionError(from_state.value, to_state.value)
    if not any(
        _met(requirement, principal, submission.submitter_id)
        for requirement in permitted.requirements
    ):
        raise _denial(permitted, principal)
    return permitted


def check_row_version(submission: Submission, expected_row_version: int) -> None:
    """Raises `SubmissionVersionConflictError` when `submission` is not at `expected_row_version`."""
    if submission.row_version != expected_row_version:
        raise SubmissionVersionConflictError(
            expected_row_version=expected_row_version, current_row_version=submission.row_version
        )


def perform_move(
    session: Session,
    principal: Principal,
    submission: Submission,
    *,
    to_state: SubmissionState,
    reason: str,
    expected_row_version: int,
    write: Callable[[], object],
) -> Submission:
    """Sets the state, adds the transition row and runs `write`, which appends the audit event and
    any rows the move needs, in one savepoint. A stale write rolls the savepoint back and becomes
    `SubmissionVersionConflictError`. Call it under the append lock after `check_move` and
    `check_row_version`. `write` runs before any flush, because the audit diff reads attribute
    history that a flush clears."""
    actor_id = principal.user_id
    if actor_id is None:
        raise ValueError("a change of state belongs to a person, but the principal has none")

    from_state = SubmissionState(submission.state)
    submission_id = submission.id
    savepoint = session.begin_nested()
    try:
        submission.state = to_state.value
        session.add(
            SubmissionTransition(
                submission_id=submission_id,
                from_state=from_state.value,
                to_state=to_state.value,
                actor_id=actor_id,
                reason=reason,
            )
        )
        write()
    except StaleDataError, ObjectDeletedError:
        savepoint.rollback()
        session.expire(submission)
        current = load_submission_locked(session, submission_id)
        raise SubmissionVersionConflictError(
            expected_row_version=expected_row_version, current_row_version=current.row_version
        ) from None
    except BaseException:
        savepoint.rollback()
        raise
    savepoint.commit()
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
