"""The route that approves a submission and writes the catalogue entry (FR-28, FR-29, FR-35, FR-44,
FR-81, NFR-08).

`POST /submissions/{id}/approve` moves a `Ready for approval` submission to `Approved`. A new test
becomes an `active` entry, and an amendment is applied to the entry it names, in one transaction
with the transition row and the audit events. It is an HTTP adapter over
`aptc.submissions.approval`: the order of the checks, the optimistic lock and every catalogue write
live in the service, and a domain exception carries `http_status`, which `aptc.api.errors` maps.

**Authorisation:** `Permission.SUBMISSION_TRANSITION_APPROVE` (FR-44), held by the Administrator
alone, so a Reviewer, a Member and an Observer are turned away at the gate before the submission is
looked up, and an Administrator must have stepped up with MFA. The terms gate applies as to every
non-GET route (NFR-45).

**The reason is the changelog note** (FR-37). It becomes the published History text of each
catalogue write, so it is held to the catalogue's rules: at least ten characters, and not a stock
phrase such as "approved".

**A refusal is a 403, a 404, a 409 or a 422.** An unknown submission is a 404. A submission that is
not `Ready for approval` is a 409, and so is a stale `expected_row_version`, which carries the
current one. An amendment whose entry is no longer active, or that no longer applies because the
entry gained a proposed synonym or already carries the proposed code, is a 409 that says which. A
preferred term that collides with another entry's is a 409 with the collision report (FR-05). A
blank or weak reason, or a property value the datatype refuses, is a 422. A refusal writes nothing,
so the submission stays `Ready for approval` and the catalogue is as it was.

**The general transition route refuses this move.** It carries only a reason, so
`POST /submissions/{id}/transitions` answers 409 for it and names this route.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, Field

from aptc.api.dependencies import AuditContextDep, CurrentPrincipal, permission_dep
from aptc.api.errors import SubmissionFieldRefusalResponse, SubmissionVersionConflictResponse
from aptc.api.routers.auth import ErrorResponse
from aptc.api.routers.submission_transitions import SubmissionTransitionResponse, _answer
from aptc.api.routers.submissions import RegistryDep, SessionDep
from aptc.auth.permissions import Permission
from aptc.submissions.approval import approve_submission

__all__ = ["ApproveRequest", "ApproveResponse", "router"]

router = APIRouter(prefix="/submissions", tags=["submissions"])

_APPROVE = Depends(permission_dep(Permission.SUBMISSION_TRANSITION_APPROVE))

_MAX_REASON_LENGTH: Final = 2_000

_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: {
        "model": ErrorResponse,
        "description": "No credential, or one that could not be verified.",
    },
    403: {
        "model": ErrorResponse,
        "description": (
            "The caller does not hold `submission.transition.approve`, which only an "
            "Administrator does, or is an Administrator who has not stepped up with MFA (the "
            "response then carries a `WWW-Authenticate` challenge), or has not accepted the "
            "current terms of use (the body then carries a `code` that says so)."
        ),
    },
    404: {
        "model": ErrorResponse,
        "description": (
            "No submission has the identifier. Also returned when a property the submission "
            "carries no longer has a definition, which is a fault to report."
        ),
    },
    409: {
        "model": SubmissionVersionConflictResponse | ErrorResponse,
        "description": (
            "The submission is not `Ready for approval`. Or `expected_row_version` is not the "
            "current one, because someone else changed the submission after it was read; the "
            "body then carries `current_row_version`. Or an amendment cannot be applied: its "
            "entry is not `active`, the entry already has a synonym it proposes, or the entry "
            "already carries the code it proposes, or its code was bound after the amendment was "
            "proposed. Or a preferred term or synonym collides "
            "with another entry's (the body then carries the collision report), the proposed "
            "code is already bound to another entry, a property is deprecated, or the entry "
            "changed while the approval ran. Nothing was changed."
        ),
    },
    422: {
        "model": SubmissionFieldRefusalResponse,
        "description": (
            "`reason` is blank or carries an invisible character (`field` is `reason`). Or "
            "`reason` is not a meaningful changelog note: shorter than ten characters, a stock "
            'phrase such as "approved", or without a letter; the body then carries a message '
            "and no `field`. Or a property value in the submission fails its datatype or "
            "cardinality rule. An identifier that is not a UUID, a `reason` "
            "over the size bound or a field the request should not carry fails validation "
            "before the route runs. Nothing was changed."
        ),
    },
}


class ApproveRequest(BaseModel):
    """`reason` is required. It is kept with the transition and is the changelog note of every
    catalogue write the approval makes (FR-37). `expected_row_version` is the `row_version` the
    caller read on the submission, so an approval never lands on a submission that changed after
    the caller looked at it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(max_length=_MAX_REASON_LENGTH)
    expected_row_version: int = Field(ge=1)


class ApproveResponse(SubmissionTransitionResponse):
    """Where the submission now stands, with the entry the approval created or amended.
    `entry_created` is true for a new test and false for an amendment. `entry_row_version` is the
    `row_version` to send with the next change to the entry."""

    entry_id: uuid.UUID
    business_key: str
    entry_created: bool
    entry_row_version: int


@router.post(
    "/{submission_id}/approve",
    summary="Approve a submission and write the catalogue entry",
    response_model=ApproveResponse,
    responses=_RESPONSES,
    dependencies=[_APPROVE],
)
def approve_a_submission(
    submission_id: uuid.UUID,
    body: Annotated[ApproveRequest, Body()],
    session: SessionDep,
    ctx: AuditContextDep,
    principal: CurrentPrincipal,
    registry: RegistryDep,
) -> ApproveResponse:
    """Moves a `Ready for approval` submission to `Approved`. A new test becomes an `active`
    entry, and an amendment is applied to its entry, in one transaction with the transition row
    and the audit events."""
    approval = approve_submission(
        session,
        ctx,
        principal,
        submission_id=submission_id,
        reason=body.reason,
        expected_row_version=body.expected_row_version,
        registry=registry,
    )
    moved = _answer(approval.submission, principal)
    return ApproveResponse(
        **moved.model_dump(),
        entry_id=approval.entry.id,
        business_key=approval.entry.business_key,
        entry_created=approval.submission.created_entry_id is not None,
        entry_row_version=approval.entry.row_version,
    )
