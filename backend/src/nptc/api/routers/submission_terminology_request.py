"""The routes that wait for, and then attach, a code that does not exist yet (FR-30, FR-31, FR-44,
FR-82, NFR-08).

`POST /submissions/{id}/terminology-request` moves an `Under review` submission to
`Awaiting terminology` and records what was asked for. `POST
/submissions/{id}/terminology-request/attach-code` looks the issued code up on the terminology
server, writes it and its FSN to the working copy and moves the submission to `Ready for approval`.
Both are HTTP adapters over `nptc.submissions.terminology_request`: the permission test, the order of
the checks, the optimistic lock and the audit events all live in the service, and a domain exception
carries `http_status`, which `nptc.api.errors` maps.

**Authorisation:** the request needs `Permission.SUBMISSION_TRANSITION_REVIEW` (FR-44), held by
Reviewer and Administrator. The attach needs `Permission.SUBMISSION_TRANSITION_APPROVE`, held by the
Administrator alone, so a Reviewer is turned away at the gate before the submission is looked up and
no call reaches the terminology server, and an Administrator must have stepped up with MFA. The terms
gate applies as to every non-GET route (NFR-45).

**A refusal is a 404, a 409 or a 422.** An unknown submission is a 404. A submission that is not
`Under review` is a 409, and so is a stale `expected_row_version`, which carries the current one. A
blank reason or text field, one with an invisible character, an edition other than `au` or `int`, or
a date that is not a date is a 422. A refusal writes nothing.

**The external reference is two fields** (FR-31): `external_reference.system` names the system that
holds the concept request and `external_reference.reference` is the identifier inside it. Both are
required. No integration exists yet, so the platform stores them and calls nothing.

**The attach is a 404, a 409, a 422 or a 5xx.** An unknown submission is a 404. A submission that is
not `Awaiting terminology` is a 409, and so is a stale `expected_row_version`. A blank reason, a code
that is malformed, unknown to the AU edition, inactive, without a reported status or without a fully
specified name is a 422 that names the field. The terminology server being unreachable is a 503, and
one that answers with something unusable is a 502. A refusal writes nothing, so the submission stays
`Awaiting terminology` and the same request can be sent again.

**The lookup is always against the AU edition.** `target_edition` on the record says where the
concept was requested and does not change where the code is looked up (FR-82).

**The general transition route refuses both moves.** It carries only a reason, so
`POST /submissions/{id}/transitions` answers 409 for them and names these routes.

**The routes are plain `def`s,** so FastAPI runs them in a worker thread and the terminology call
never stalls the event loop.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, Field

from nptc.api.dependencies import (
    ApiSettingsDep,
    AuditContextDep,
    CurrentPrincipal,
    permission_dep,
)
from nptc.api.errors import SubmissionFieldRefusalResponse, SubmissionVersionConflictResponse
from nptc.api.labels import LabelProvenance, fsn_provenance
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.submission_transitions import SubmissionTransitionResponse, _answer
from nptc.api.routers.submissions import _MAX_SCTID_LENGTH, SessionDep, TerminologyClientDep
from nptc.auth.permissions import Permission
from nptc.db.models.submission_terminology_request import (
    SubmissionTerminologyRequest,
    TargetEdition,
)
from nptc.submissions.terminology_request import (
    TerminologyRequestDetails,
    attach_terminology_code,
    request_terminology,
)

__all__ = [
    "AttachCodeRequest",
    "AttachCodeResponse",
    "ExternalReference",
    "TerminologyRequestMoveResponse",
    "TerminologyRequestRequest",
    "TerminologyRequestView",
    "router",
    "terminology_request_view",
]

router = APIRouter(prefix="/submissions", tags=["submissions"])

_REQUEST = Depends(permission_dep(Permission.SUBMISSION_TRANSITION_REVIEW))
_ATTACH = Depends(permission_dep(Permission.SUBMISSION_TRANSITION_APPROVE))

_MAX_REASON_LENGTH: Final = 2_000
_MAX_PROPOSED_FSN_LENGTH: Final = 500
_MAX_JUSTIFICATION_LENGTH: Final = 4_000
_MAX_SYSTEM_LENGTH: Final = 200
_MAX_REFERENCE_LENGTH: Final = 500

_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: {
        "model": ErrorResponse,
        "description": "No credential, or one that could not be verified.",
    },
    403: {
        "model": ErrorResponse,
        "description": (
            "The caller does not hold `submission.transition.review`, or has not accepted the "
            "current terms of use (the body then carries a `code` that says so)."
        ),
    },
    404: {"model": ErrorResponse, "description": "No submission has the identifier."},
    409: {
        "model": SubmissionVersionConflictResponse | ErrorResponse,
        "description": (
            "The submission is not `Under review`. Or `expected_row_version` is not the current "
            "one, because someone else changed the submission after it was read; the body then "
            "carries `current_row_version`. Nothing was changed."
        ),
    },
    422: {
        "model": SubmissionFieldRefusalResponse,
        "description": (
            "A text field or the `reason` is blank, or carries an invisible character such as a "
            "zero-width space (`field` names it). An identifier that is not a UUID, a "
            "`target_edition` other than `au` or `int`, a date that is not a date, a missing "
            "field, a field over its size bound or a field the request should not carry fails "
            "validation before the route runs."
        ),
    },
}


_ATTACH_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
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
    404: {"model": ErrorResponse, "description": "No submission has the identifier."},
    409: {
        "model": SubmissionVersionConflictResponse | ErrorResponse,
        "description": (
            "The submission is not `Awaiting terminology`. Or `expected_row_version` is not the "
            "current one, because someone else changed the submission after it was read; the body "
            "then carries `current_row_version`. Nothing was changed."
        ),
    },
    422: {
        "model": SubmissionFieldRefusalResponse,
        "description": (
            "`reason` is blank or carries an invisible character (`field` is `reason`), or the "
            "SNOMED CT code is malformed, unknown to the AU edition, inactive, has no reported "
            "status or has no fully specified name (`field` is `snomed_code`). Nothing was "
            "changed, and the submission stays `Awaiting terminology`."
        ),
    },
    502: {
        "model": SubmissionFieldRefusalResponse | ErrorResponse,
        "description": (
            "The terminology server's response could not be used. `field` is `snomed_code`."
        ),
    },
    503: {
        "model": SubmissionFieldRefusalResponse | ErrorResponse,
        "description": (
            "The terminology server could not be reached, or a rate limit persisted through "
            "retries. Nothing was changed, and the same request can be sent again. May carry a "
            "`Retry-After` header."
        ),
    },
}


class ExternalReference(BaseModel):
    """Where the request for the concept is tracked (FR-31): the system that holds it and the
    identifier it has there."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    system: str = Field(max_length=_MAX_SYSTEM_LENGTH)
    reference: str = Field(max_length=_MAX_REFERENCE_LENGTH)


class TerminologyRequestRequest(BaseModel):
    """The body of `POST /submissions/{id}/terminology-request`.

    Every field but `expected_release` is required. `target_edition` is `au` or `int`.
    `raised_date` is when the request was raised with the terminology authority, and
    `expected_release` is when its code is expected, if known. `reason` says why the submission
    waits, and `expected_row_version` is the `row_version` the caller read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposed_fsn: str = Field(max_length=_MAX_PROPOSED_FSN_LENGTH)
    target_edition: TargetEdition
    justification: str = Field(max_length=_MAX_JUSTIFICATION_LENGTH)
    external_reference: ExternalReference
    raised_date: date
    expected_release: date | None = None
    reason: str = Field(max_length=_MAX_REASON_LENGTH)
    expected_row_version: int = Field(ge=1)


class AttachCodeRequest(BaseModel):
    """The body of `POST /submissions/{id}/terminology-request/attach-code`.

    `snomed_code` is the code the terminology authority issued, as a string (FR-06). The server
    supplies the FSN, so a request never carries one (FR-82). `reason` says why the submission can
    move on, and `expected_row_version` is the `row_version` the caller read.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snomed_code: str = Field(max_length=_MAX_SCTID_LENGTH)
    reason: str = Field(max_length=_MAX_REASON_LENGTH)
    expected_row_version: int = Field(ge=1)


class AttachCodeResponse(SubmissionTransitionResponse):
    """Where the submission now stands, with the code and the FSN the terminology server returned
    for it, as the working copy now holds them. `label_provenance` states which designation the FSN
    is (FR-98)."""

    snomed_code: str
    snomed_fsn: str
    label_provenance: dict[str, LabelProvenance]


class TerminologyRequestView(BaseModel):
    """The terminology request of a submission. `updated_at` is the latest change to a tracking
    field."""

    model_config = ConfigDict(frozen=True)

    proposed_fsn: str
    target_edition: TargetEdition
    justification: str
    external_reference: ExternalReference
    raised_date: date
    expected_release: date | None
    updated_at: datetime


class TerminologyRequestMoveResponse(SubmissionTransitionResponse):
    """Where the submission now stands, with the request that was recorded."""

    terminology_request: TerminologyRequestView


def terminology_request_view(record: SubmissionTerminologyRequest) -> TerminologyRequestView:
    return TerminologyRequestView(
        proposed_fsn=record.proposed_fsn,
        target_edition=TargetEdition(record.target_edition),
        justification=record.justification,
        external_reference=ExternalReference(
            system=record.external_reference_system, reference=record.external_reference_ref
        ),
        raised_date=record.raised_date,
        expected_release=record.expected_release,
        updated_at=record.updated_at,
    )


@router.post(
    "/{submission_id}/terminology-request",
    summary="Move a submission to Awaiting terminology",
    response_model=TerminologyRequestMoveResponse,
    responses=_RESPONSES,
    dependencies=[_REQUEST],
)
def request_submission_terminology(
    submission_id: uuid.UUID,
    body: Annotated[TerminologyRequestRequest, Body()],
    session: SessionDep,
    ctx: AuditContextDep,
    principal: CurrentPrincipal,
) -> TerminologyRequestMoveResponse:
    """Moves an `Under review` submission to `Awaiting terminology` and records what was asked for,
    in one transaction with the transition row and the audit events."""
    submission = request_terminology(
        session,
        ctx,
        principal,
        submission_id=submission_id,
        details=TerminologyRequestDetails(
            proposed_fsn=body.proposed_fsn,
            target_edition=body.target_edition,
            justification=body.justification,
            external_reference_system=body.external_reference.system,
            external_reference_ref=body.external_reference.reference,
            raised_date=body.raised_date,
            expected_release=body.expected_release,
        ),
        reason=body.reason,
        expected_row_version=body.expected_row_version,
    )
    record = session.get(SubmissionTerminologyRequest, submission.id, populate_existing=True)
    if record is None:
        raise RuntimeError("the terminology request was written but cannot be read back")
    moved = _answer(submission, principal)
    return TerminologyRequestMoveResponse(
        **moved.model_dump(), terminology_request=terminology_request_view(record)
    )


@router.post(
    "/{submission_id}/terminology-request/attach-code",
    summary="Attach the issued code and move a submission to Ready for approval",
    response_model=AttachCodeResponse,
    responses=_ATTACH_RESPONSES,
    dependencies=[_ATTACH],
)
def attach_submission_terminology_code(
    submission_id: uuid.UUID,
    body: Annotated[AttachCodeRequest, Body()],
    session: SessionDep,
    ctx: AuditContextDep,
    principal: CurrentPrincipal,
    terminology_client: TerminologyClientDep,
    settings: ApiSettingsDep,
) -> AttachCodeResponse:
    """Looks the code up on the terminology server, writes it and its FSN to the working copy and
    moves an `Awaiting terminology` submission to `Ready for approval`, in one transaction with the
    transition row and the audit event."""
    attachment = attach_terminology_code(
        session,
        ctx,
        principal,
        submission_id=submission_id,
        snomed_code=body.snomed_code,
        reason=body.reason,
        expected_row_version=body.expected_row_version,
        terminology_client=terminology_client,
    )
    code = attachment.content.snomed_code
    fsn = attachment.content.snomed_fsn
    if code is None or fsn is None:
        raise RuntimeError("the code was attached but the working content carries none")
    moved = _answer(attachment.submission, principal)
    return AttachCodeResponse(
        **moved.model_dump(),
        snomed_code=code,
        snomed_fsn=fsn,
        label_provenance={"snomed_fsn": fsn_provenance(settings)},
    )
