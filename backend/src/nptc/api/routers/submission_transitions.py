"""The withdraw route: a submitter takes back their own submission (FR-28, FR-44, NFR-08).

`POST /submissions/{id}/withdraw` is the HTTP adapter over `nptc.submissions.transitions`. It
re-implements no rule: the permitted moves, the ownership test and the optimistic lock all live in
the service, and a domain exception carries `http_status`, which `nptc.api.errors` maps.

**Authorisation:** `Permission.SUBMISSION_WITHDRAW_OWN` gates the route (FR-44), so a caller without
it, an Observer for one, gets a 403 before the submission is looked up. A caller who holds it but did
not make the submission gets a 403 from the service. The Administrator holds
`submission.withdraw.any` and not `.own`, and the permitted-moves table has no row for `.any` yet,
so an Administrator cannot withdraw even their own submission through this route.

**A refusal is a 404, a 409 or a 422.** An unknown submission is a 404. A submission that is not
`Submitted` or in `Triage` is a 409, and a closed submission is never reinterpreted, so a second
withdrawal is a 409 too. A stale `expected_row_version` is a 409 that carries the current one. A
blank `reason`, or one with an invisible character, is a 422 that names the field. A refusal writes
no transition row and no audit event.

**The reason is required** and is stored with the transition and in the audit event, so a reader of
either sees why the submission was withdrawn. The terms gate applies to this route as to every
non-GET route (NFR-45).
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from nptc.api.dependencies import AuditContextDep, CurrentPrincipal, permission_dep
from nptc.api.errors import SubmissionFieldRefusalResponse, SubmissionVersionConflictResponse
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.submissions import SessionDep
from nptc.auth.permissions import Permission
from nptc.db.models.submission import SubmissionState
from nptc.submissions.transitions import transition_submission

router = APIRouter(prefix="/submissions", tags=["submissions"])

_WITHDRAW = Depends(permission_dep(Permission.SUBMISSION_WITHDRAW_OWN))

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
            "submission, or has not accepted the current terms of use (the body then carries a "
            "`code` that says so)."
        ),
    },
    404: {"model": ErrorResponse, "description": "No submission has the identifier."},
    409: {
        "model": SubmissionVersionConflictResponse | ErrorResponse,
        "description": (
            "The submission cannot be withdrawn from its current state: only a submission that is "
            "`Submitted` or in `Triage` can be, so a closed one is refused and a second withdrawal "
            "is too. Or `expected_row_version` is not the current one, because someone else changed "
            "the submission after it was read; the body then carries `current_row_version`. "
            "Nothing was changed."
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


class WithdrawRequest(BaseModel):
    """`reason` is required and is kept with the withdrawal. `expected_row_version` is the
    `row_version` the caller read on the submission, so a withdrawal never lands on a submission
    that changed after the caller looked at it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(max_length=_MAX_REASON_LENGTH)
    expected_row_version: int = Field(ge=1)


class SubmissionTransitionResponse(BaseModel):
    """Where the submission now stands: its `state` and the `row_version` to send with the next
    change."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    state: str
    row_version: int


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
    """Moves a `Submitted` or `Triage` submission to `Withdrawn`, with the reason, in one
    transaction with its transition row and audit event."""
    submission = transition_submission(
        session,
        ctx,
        principal,
        submission_id=submission_id,
        to_state=SubmissionState.WITHDRAWN,
        reason=body.reason,
        expected_row_version=body.expected_row_version,
    )
    return SubmissionTransitionResponse(
        id=submission.id, state=submission.state, row_version=submission.row_version
    )
