"""The interest routes: register and revoke interest in a submission (FR-32, FR-34, NFR-08).

`PUT /submissions/{id}/interest` registers and `DELETE /submissions/{id}/interest` revokes. They are
the HTTP adapter over `nptc.submissions.interest`; they re-implement no domain rule, and a domain
exception carries `http_status`, which `nptc.api.errors` maps.

**Authorisation:** `Permission.INTEREST_REGISTER` gates both routes (FR-44, FR-34). Member, Reviewer
and Administrator hold it; Provisional and Observer do not. The terms gate applies to every non-GET
route, so both inherit it (NFR-45).

**Both routes are idempotent and answer 200 either way.** A caller who already holds interest and
sends `PUT`, or holds none and sends `DELETE`, gets the same body as a caller whose request changed
something, and the repeat writes no row and emits no audit event. The body says where things now
stand, so a client needs no second request.

**A refusal is a 404 or a 409.** An unknown submission is a 404. A caller's own submission, and a
closed submission, are each a 409 with a sentence that says which: both are conflicts with the
submission's state, not mistakes in the request. A closed submission refuses a revocation too, so the
count that stood at closure stays what it was.

**`interest_count` follows `Permission.INTEREST_VIEW_COUNTS`** (FR-34). It is null for a caller
without it. Every role that may register also holds it, so today it is always a number here.
"""

from __future__ import annotations

import uuid
from typing import Any, Final

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from nptc.api.dependencies import AuditContextDep, CurrentPrincipal, permission_dep
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.submissions import SessionDep
from nptc.auth.permissions import Permission
from nptc.submissions.interest import interest_summaries, register_interest, revoke_interest

router = APIRouter(prefix="/submissions", tags=["submissions"])

_REGISTER = Depends(permission_dep(Permission.INTEREST_REGISTER))

_RESPONSE_401: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No credential, or one that could not be verified.",
}
_RESPONSE_403: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The caller is authenticated but does not hold `interest.register`, or has not accepted "
        "the current terms of use (the body then carries a `code` that says so)."
    ),
}
_RESPONSE_404: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No submission has the identifier.",
}
_RESPONSE_409: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The submission is the caller's own, so they cannot register interest in it, or it is "
        "closed (`Published in release`, `Rejected` or `Withdrawn`), so its interest can no "
        "longer be registered or revoked. `detail` says which. Nothing was changed."
    ),
}
_RESPONSE_422: Final[dict[str, Any]] = {
    "description": "The identifier is not a UUID.",
    "content": {
        "application/json": {
            "schema": {"anyOf": [{"$ref": "#/components/schemas/HTTPValidationError"}]}
        }
    },
}
_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    401: _RESPONSE_401,
    403: _RESPONSE_403,
    404: _RESPONSE_404,
    409: _RESPONSE_409,
    422: _RESPONSE_422,
}


class InterestResponse(BaseModel):
    """Where the caller's interest in the submission now stands. `has_registered_interest` is true
    when the caller holds a live record. `interest_count` is the number of live records from every
    user, null when the caller may not see counts."""

    model_config = ConfigDict(frozen=True)

    has_registered_interest: bool
    interest_count: int | None


def _standing(
    session: SessionDep, principal: CurrentPrincipal, submission_id: uuid.UUID
) -> InterestResponse:
    summary = interest_summaries(session, [submission_id], viewer_id=principal.user_id)[
        submission_id
    ]
    return InterestResponse(
        has_registered_interest=summary.registered,
        interest_count=(summary.count if principal.has(Permission.INTEREST_VIEW_COUNTS) else None),
    )


@router.put(
    "/{submission_id}/interest",
    summary="Register interest in a submission",
    response_model=InterestResponse,
    responses=_RESPONSES,
    dependencies=[_REGISTER],
)
def register_submission_interest(
    submission_id: uuid.UUID,
    session: SessionDep,
    ctx: AuditContextDep,
    principal: CurrentPrincipal,
) -> InterestResponse:
    """Idempotent: a caller who already holds interest gets the same answer and nothing is
    written. The organisation recorded with the interest is the caller's profile value now."""
    register_interest(
        session,
        ctx,
        submission_id=submission_id,
        profile_organisation=principal.user_ref.organisation if principal.user_ref else None,
    )
    return _standing(session, principal, submission_id)


@router.delete(
    "/{submission_id}/interest",
    summary="Revoke interest in a submission",
    response_model=InterestResponse,
    responses=_RESPONSES,
    dependencies=[_REGISTER],
)
def revoke_submission_interest(
    submission_id: uuid.UUID,
    session: SessionDep,
    ctx: AuditContextDep,
    principal: CurrentPrincipal,
) -> InterestResponse:
    """Idempotent: a caller with no live interest gets the same answer and nothing is written. The
    revoked record is kept, with its organisation, and no longer counts."""
    revoke_interest(session, ctx, submission_id=submission_id)
    return _standing(session, principal, submission_id)
