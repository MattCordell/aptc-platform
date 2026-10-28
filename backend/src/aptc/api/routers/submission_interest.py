"""The interest routes: register and revoke interest in a submission (FR-32, FR-34, NFR-08).

`PUT /submissions/{id}/interest` registers and `DELETE /submissions/{id}/interest` revokes. They are
the HTTP adapter over `aptc.submissions.interest`; they re-implement no domain rule, and a domain
exception carries `http_status`, which `aptc.api.errors` maps.

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

**`GET /submissions/{id}/interest` names the people** (FR-33, FR-34).
`Permission.INTEREST_IDENTITIES_VIEW` gates it, so a caller without it gets a 403 before the
submission is looked up. It is a read, so it writes nothing and emits no audit event (NFR-08). A
closed account's person reads `CLOSED_ACCOUNT_PLACEHOLDER`, and its record still counts under the
organisation it registered with.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from aptc.api.dependencies import AuditContextDep, CurrentPrincipal, permission_dep
from aptc.api.routers.auth import ErrorResponse
from aptc.api.routers.submissions import SessionDep
from aptc.api.routers.submissions_read import CLOSED_ACCOUNT_PLACEHOLDER
from aptc.auth.permissions import Permission
from aptc.submissions.interest import interest_summaries, register_interest, revoke_interest
from aptc.submissions.interest_identities import InterestIdentities, list_interest_identities

router = APIRouter(prefix="/submissions", tags=["submissions"])

_REGISTER = Depends(permission_dep(Permission.INTEREST_REGISTER))
_IDENTITIES = Depends(permission_dep(Permission.INTEREST_IDENTITIES_VIEW))

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
_RESPONSE_403_IDENTITIES: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The caller is authenticated but does not hold `interest.identities.view`, or has not "
        "accepted the current terms of use (the body then carries a `code` that says so)."
    ),
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


class InterestPerson(BaseModel):
    """One live registration. `username` and `display_name` are the placeholder for a closed
    account. `organisation` is the copy recorded at registration, which can differ from the person's
    profile now, and is null when none was given. `registered_at` is when the person registered."""

    model_config = ConfigDict(frozen=True)

    username: str
    display_name: str
    organisation: str | None
    registered_at: datetime


class InterestOrganisation(BaseModel):
    """One organisation represented, and how many live records name it. Spellings that differ only
    by letter case or surrounding space are one organisation, shown in its most common spelling.
    `organisation` is null for the one group of records that named none, and `label` then reads
    "No organisation given"."""

    model_config = ConfigDict(frozen=True)

    organisation: str | None
    label: str
    count: int


class InterestIdentitiesResponse(BaseModel):
    """`interest_count` is the number of live records. `distinct_organisation_count` is the number
    of named organisations in `distinct_organisations`, so the group with no organisation is not
    counted. `records` is newest first."""

    model_config = ConfigDict(frozen=True)

    interest_count: int
    distinct_organisation_count: int
    distinct_organisations: list[InterestOrganisation]
    records: list[InterestPerson]


def _identities_response(identities: InterestIdentities) -> InterestIdentitiesResponse:
    return InterestIdentitiesResponse(
        interest_count=len(identities.records),
        distinct_organisation_count=identities.distinct_organisation_count,
        distinct_organisations=[
            InterestOrganisation(
                organisation=group.organisation, label=group.label, count=group.count
            )
            for group in identities.organisations
        ],
        records=[
            InterestPerson(
                username=record.person.username or CLOSED_ACCOUNT_PLACEHOLDER,
                display_name=record.person.display_name or CLOSED_ACCOUNT_PLACEHOLDER,
                organisation=record.organisation,
                registered_at=record.registered_at,
            )
            for record in identities.records
        ],
    )


@router.get(
    "/{submission_id}/interest",
    summary="Show who registered interest in a submission",
    response_model=InterestIdentitiesResponse,
    responses={
        401: _RESPONSE_401,
        403: _RESPONSE_403_IDENTITIES,
        404: _RESPONSE_404,
        422: _RESPONSE_422,
    },
    dependencies=[_IDENTITIES],
)
def show_submission_interest(
    submission_id: uuid.UUID, session: SessionDep
) -> InterestIdentitiesResponse:
    """The live records and the organisations behind them, for any submission, closed ones
    included. Revoked records are left out of both."""
    return _identities_response(list_interest_identities(session, submission_id))
