"""The routes that change a submission's state (FR-28, FR-44, FR-81, NFR-08).

`POST /submissions/{id}/transitions` makes any move in `PERMITTED_TRANSITIONS`, and
`POST /submissions/{id}/withdraw` is the submitter's shorter way to one of them. Both are HTTP
adapters over `aptc.submissions.transitions`. Neither re-implements a rule: the permitted moves, the
permission test and the optimistic lock all live in the service, and a domain exception carries
`http_status`, which `aptc.api.errors` maps.

**Authorisation:** the route gate turns away a caller who holds no permission that any move of the
table names, `TRANSITION_PERMISSIONS`, before the submission is looked up (FR-44). The service then
judges the particular move, so a Member asking for a review move gets a 403 from the service, and an
Observer gets one from the gate. The gate is derived from the table, so a new row cannot leave it
behind. `/withdraw` is gated on `Permission.SUBMISSION_WITHDRAW_OWN` alone, so an Administrator, who
holds `.any` and not `.own`, withdraws through `/transitions`.

**A refusal is a 404, a 409 or a 422.** An unknown submission is a 404. A move the table has no row
for is a 409, and a closed submission is never reinterpreted, so a second withdrawal is a 409 too. A
stale `expected_row_version` is a 409 that carries the current one. A blank `reason`, or one with an
invisible character, is a 422 that names the field, as is a `to_state` that is not a state. A refusal
writes no transition row and no audit event.

**The reason is required** and is stored with the transition and in the audit event, so a reader of
either sees why the submission moved. The terms gate applies to both routes as to every non-GET route
(NFR-45).
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from aptc.api.dependencies import (
    AuditContextDep,
    CurrentPrincipal,
    any_permission_dep,
    permission_dep,
)
from aptc.api.errors import SubmissionFieldRefusalResponse, SubmissionVersionConflictResponse
from aptc.api.routers.auth import ErrorResponse
from aptc.api.routers.submissions import SessionDep
from aptc.auth.permissions import Permission
from aptc.auth.principal import Principal
from aptc.db.models.submission import Submission, SubmissionState
from aptc.submissions.transitions import (
    TRANSITION_PERMISSIONS,
    available_transitions,
    transition_submission,
)

router = APIRouter(prefix="/submissions", tags=["submissions"])

_WITHDRAW = Depends(permission_dep(Permission.SUBMISSION_WITHDRAW_OWN))
_TRANSITION = Depends(any_permission_dep(TRANSITION_PERMISSIONS))

_MAX_REASON_LENGTH: Final = 2_000

_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: {
        "model": ErrorResponse,
        "description": "No credential, or one that could not be verified.",
    },
    403: {
        "model": ErrorResponse,
        "description": (
            "The caller does not hold `submission.withdraw.own`, or is not the submitter of this "
            "submission, or the submission has been approved and only `submission.withdraw.any` "
            "reaches it, or the caller has not accepted the current terms of use (the body then "
            "carries a `code` that says so)."
        ),
    },
    404: {"model": ErrorResponse, "description": "No submission has the identifier."},
    409: {
        "model": SubmissionVersionConflictResponse | ErrorResponse,
        "description": (
            "The submission cannot be withdrawn from its current state: only a submission that is "
            "`Submitted`, in `Triage` or `Under review` can be withdrawn by its submitter, so a "
            "closed one is refused and a second withdrawal is too. Or `expected_row_version` is "
            "not the current one, because someone else changed the submission after it was read; "
            "the body then carries `current_row_version`. "
            "A stale `expected_row_version` sent after someone else has already withdrawn the "
            "submission gets the first reason, not the second, so a client should treat both as "
            '"reload the submission". Nothing was changed.'
        ),
    },
    422: {
        "model": SubmissionFieldRefusalResponse,
        "description": (
            "`reason` is blank, or carries an invisible character such as a zero-width space "
            "(`field` is `reason`). An identifier that is not a UUID, a `reason` over the size "
            "bound or a field the request should not carry fails validation before the route "
            "runs."
        ),
    },
}

_TRANSITION_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    **_RESPONSES,
    403: {
        "model": ErrorResponse,
        "description": (
            "The caller holds none of the permissions any move needs, or lacks the one this move "
            "needs: `submission.transition.review` for a review move, `submission.withdraw.own` "
            "for the submitter's own withdrawal from `Submitted`, `Triage` or `Under review`, and "
            "`submission.withdraw.any` for any other withdrawal. Or the caller has not accepted the "
            "current terms of use (the body then carries a `code` that says so)."
        ),
    },
    409: {
        "model": SubmissionVersionConflictResponse | ErrorResponse,
        "description": (
            "The table has no move from the submission's current state to `to_state`, so a closed "
            "submission is refused, a second withdrawal is too, and so is a move that has its own "
            "route, such as `Approved`, which `POST /submissions/{id}/approve` makes. Or "
            "`expected_row_version` is not the current "
            "one, because someone else changed the submission after it was read; the body then "
            "carries `current_row_version`. A stale `expected_row_version` sent after someone else "
            "has moved the submission gets the first reason, so a client should treat both as "
            '"reload the submission". Nothing was changed.'
        ),
    },
    422: {
        "model": SubmissionFieldRefusalResponse,
        "description": (
            "`reason` is blank, or carries an invisible character such as a zero-width space "
            "(`field` is `reason`). An identifier that is not a UUID, a `to_state` that is not a "
            "submission state, a `reason` over the size bound or a field the request should not "
            "carry fails validation before the route runs."
        ),
    },
}


class WithdrawRequest(BaseModel):
    """`reason` is required and is kept with the withdrawal. `expected_row_version` is the
    `row_version` the caller read on the submission, so a withdrawal never lands on a submission
    that changed after the caller looked at it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(max_length=_MAX_REASON_LENGTH)
    expected_row_version: int = Field(ge=1)


class TransitionRequest(BaseModel):
    """`to_state` is the state to move to. `reason` is required and is kept with the move.
    `expected_row_version` is the `row_version` the caller read on the submission, so a move never
    lands on a submission that changed after the caller looked at it. `available_transitions` on
    the submission lists the states the caller may ask for."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    to_state: SubmissionState
    reason: str = Field(max_length=_MAX_REASON_LENGTH)
    expected_row_version: int = Field(ge=1)


class SubmissionTransitionResponse(BaseModel):
    """Where the submission now stands: its `state` and the `row_version` to send with the next
    change. `available_transitions` is what the caller may do next, as on `GET /submissions/{id}`, so
    a screen that holds the submission can update itself without a second read."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    state: str
    row_version: int
    available_transitions: list[SubmissionState]


def _answer(submission: Submission, principal: Principal) -> SubmissionTransitionResponse:
    return SubmissionTransitionResponse(
        id=submission.id,
        state=submission.state,
        row_version=submission.row_version,
        available_transitions=list(
            available_transitions(
                principal,
                from_state=SubmissionState(submission.state),
                submitter_id=submission.submitter_id,
            )
        ),
    )


@router.post(
    "/{submission_id}/withdraw",
    summary="Withdraw your own submission",
    response_model=SubmissionTransitionResponse,
    responses=_RESPONSES,
    dependencies=[_WITHDRAW],
)
def withdraw_submission(
    submission_id: uuid.UUID,
    body: WithdrawRequest,
    session: SessionDep,
    ctx: AuditContextDep,
    principal: CurrentPrincipal,
) -> SubmissionTransitionResponse:
    """Moves a `Submitted`, `Triage` or `Under review` submission to `Withdrawn`, with the reason,
    in one transaction with its transition row and audit event."""
    submission = transition_submission(
        session,
        ctx,
        principal,
        submission_id=submission_id,
        to_state=SubmissionState.WITHDRAWN,
        reason=body.reason,
        expected_row_version=body.expected_row_version,
    )
    return _answer(submission, principal)


@router.post(
    "/{submission_id}/transitions",
    summary="Move a submission to another state",
    response_model=SubmissionTransitionResponse,
    responses=_TRANSITION_RESPONSES,
    dependencies=[_TRANSITION],
)
def transition_submission_state(
    submission_id: uuid.UUID,
    body: TransitionRequest,
    session: SessionDep,
    ctx: AuditContextDep,
    principal: CurrentPrincipal,
) -> SubmissionTransitionResponse:
    """Moves the submission to `to_state` when the table of permitted moves has a row for it and the
    caller may make it, with the reason, in one transaction with its transition row and audit
    event. `available_transitions` on `GET /submissions/{id}` says which moves the caller may make."""
    submission = transition_submission(
        session,
        ctx,
        principal,
        submission_id=submission_id,
        to_state=body.to_state,
        reason=body.reason,
        expected_row_version=body.expected_row_version,
    )
    return _answer(submission, principal)
