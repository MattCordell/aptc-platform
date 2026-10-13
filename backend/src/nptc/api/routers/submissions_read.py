"""The submission read routes (FR-28, FR-42, NFR-20).

`GET /submissions` lists and `GET /submissions/{id}` shows one. The write routes are in
`nptc.api.routers.submissions`; both share the `/submissions` prefix.

**Authorisation:** `Permission.SUBMISSION_VIEW` gates both routes (FR-44). Observer, Provisional,
Member, Reviewer and Administrator hold it.

**The submitter-identity rule (FR-42) is applied on the server, field by field.** A caller who holds
`Permission.SUBMITTER_IDENTITY_VIEW` gets the submitter's display name and the submission's own copy
of the organisation. Every other caller gets the username alone, and the response has no name or
organisation key at all, not even a null one, so a client never receives an identity it must hide.
That is why each response comes in two classes and the route's `response_model` is their union: the
schema states the real contract, and a field that is never set cannot leak by being serialised.

**A closed account has no username.** Closing it clears the profile (NFR-17), so the response shows
`CLOSED_ACCOUNT_PLACEHOLDER` in its place. A caller who may see identities still gets the
organisation copy, because that copy is the submission's own and outlives the account.

**The default list is the open submissions.** With no `state`, a closed state (`Published in
release`, `Rejected`, `Withdrawn`) is left out. A caller names any states they want, closed ones
included. `mine` with no `state` returns the caller's own submissions in every state.

**The rule covers the identity fields, not the submitter's free text.** `notes` and `reference_url`
are served to every holder of `submission.view`. A submitter can type their own name into `notes`,
and the server does not search for it. Withholding `notes` would hide the supporting detail from
every caller who may read a submission.

**Interest rides on every item and on the detail (FR-34).** `has_registered_interest` says whether
the caller holds a live record, and `interest_count` is the number of live records from every user,
null for a caller without `Permission.INTEREST_VIEW_COUNTS`. The counts for a page come from one
grouped statement, so a page costs the same however long it is. The identities behind a count are
not here.

**`distinct_organisation_count` is for a caller with `Permission.INTEREST_IDENTITIES_VIEW`**
(FR-33). It is the number of named organisations among the live records, and the key is absent from
the response for anyone else, not null, so a client never receives a figure it must hide. That is a
second axis of response classes beside the identity one, four item and page classes in all, chosen
by `_PAGES`. It is on the list only. The same grouped-statement rule applies, and the people are on
`GET /submissions/{id}/interest`.

**No audit event.** A read changes nothing (NFR-08).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from nptc.api.dependencies import (
    ApiSettingsDep,
    CurrentPrincipal,
    permission_dep,
)
from nptc.api.labels import (
    AU_PREFERRED_TERM_PROVENANCE,
    SYNONYM_PROVENANCE,
    LabelProvenance,
    fsn_provenance,
)
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.submissions import SessionDep, SubmittedPropertyValue
from nptc.auth.permissions import Permission
from nptc.db.models.submission import SubmissionKind, SubmissionState
from nptc.settings import ApiSettings
from nptc.submissions import queries
from nptc.submissions.interest import InterestSummary, interest_summaries
from nptc.submissions.interest_identities import distinct_organisation_counts
from nptc.submissions.queries import SubmissionRecord

router = APIRouter(prefix="/submissions", tags=["submissions"])

#: What a closed account's submitter is called. The tombstone has no username to show (NFR-17).
CLOSED_ACCOUNT_PLACEHOLDER: Final = "Closed account"

_VIEW = Depends(permission_dep(Permission.SUBMISSION_VIEW))

_RESPONSE_401: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No credential, or one that could not be verified.",
}
_RESPONSE_403: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "The caller is authenticated but does not hold `submission.view`, or has not accepted "
        "the current terms of use (the body then carries a `code` that says so)."
    ),
}
_RESPONSE_404: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": "No submission has the identifier.",
}
#: A parameter that fails validation is the framework's body; a cursor this API did not issue is an
#: `ErrorResponse`.
_RESPONSE_422_LIST: Final[dict[str, Any]] = {
    "model": ErrorResponse,
    "description": (
        "A `state` or `kind` that is not one of the listed values, a `limit` outside 1 to 200, or "
        "an `after` cursor this API did not issue."
    ),
    "content": {
        "application/json": {
            "schema": {
                "anyOf": [
                    {"$ref": "#/components/schemas/HTTPValidationError"},
                    {"$ref": "#/components/schemas/ErrorResponse"},
                ]
            }
        }
    },
}
_RESPONSE_422_DETAIL: Final[dict[str, Any]] = {
    "description": "The identifier is not a UUID.",
    "content": {
        "application/json": {
            "schema": {"anyOf": [{"$ref": "#/components/schemas/HTTPValidationError"}]}
        }
    },
}


class PlainSubmitter(BaseModel):
    """The submitter as a caller without `submitter_identity.view` sees them: a username only."""

    model_config = ConfigDict(frozen=True)

    username: str


class IdentifiedSubmitter(BaseModel):
    """The submitter as a caller with `submitter_identity.view` sees them. `organisation` is the
    copy the submitter sent with this submission, which can differ from their profile, and is null
    when they sent none. A closed account's `username` and `display_name` are the placeholder."""

    model_config = ConfigDict(frozen=True)

    username: str
    display_name: str
    organisation: str | None


class _SummaryFields(BaseModel):
    """What a list item and a detail share. `snomed_fsn` is the label the terminology server
    returned for `snomed_code` (FR-82), and `label_provenance` says which designation each label
    is (FR-98). `entry_business_key` is the entry an amendment proposes to change, and null for a
    new test. `is_own` is true when the caller made the submission. `has_registered_interest` is
    true when the caller holds live interest in it, and `interest_count` is how many users do, null
    when the caller may not see counts (FR-34)."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    kind: str
    state: str
    entry_business_key: str | None
    preferred_term: str
    snomed_code: str | None
    snomed_fsn: str | None
    created_at: datetime
    is_own: bool
    has_registered_interest: bool
    interest_count: int | None
    label_provenance: dict[str, LabelProvenance]


class _DetailFields(_SummaryFields):
    """The full submitted content. The three `reference_*` fields describe one check and are all
    present or all absent. `duplicate_confirmed_at` is when a submitter confirmed a match was a
    different test (FR-25), and null otherwise."""

    synonyms: list[str]
    property_values: dict[str, list[SubmittedPropertyValue]]
    notes: str | None
    reference_url: str | None
    reference_checked_at: datetime | None
    reference_status: int | None
    duplicate_confirmed_at: datetime | None


class SubmissionSummary(_SummaryFields):
    submitter: PlainSubmitter


class IdentifiedSubmissionSummary(_SummaryFields):
    submitter: IdentifiedSubmitter


class SubmissionDetail(_DetailFields):
    submitter: PlainSubmitter


class IdentifiedSubmissionDetail(_DetailFields):
    submitter: IdentifiedSubmitter


class CountedSubmissionSummary(SubmissionSummary):
    """`SubmissionSummary` for a caller with `interest.identities.view`: it adds the number of named
    organisations among the live interest records (FR-33)."""

    distinct_organisation_count: int


class CountedIdentifiedSubmissionSummary(IdentifiedSubmissionSummary):
    """`IdentifiedSubmissionSummary` with `distinct_organisation_count`."""

    distinct_organisation_count: int


class SubmissionPage(BaseModel):
    """One page, newest first. A `null` `next_cursor` means there is no following page."""

    model_config = ConfigDict(frozen=True)

    items: list[SubmissionSummary]
    next_cursor: str | None


class IdentifiedSubmissionPage(BaseModel):
    """`SubmissionPage` for a caller with `submitter_identity.view`."""

    model_config = ConfigDict(frozen=True)

    items: list[IdentifiedSubmissionSummary]
    next_cursor: str | None


class CountedSubmissionPage(BaseModel):
    """`SubmissionPage` for a caller with `interest.identities.view` alone."""

    model_config = ConfigDict(frozen=True)

    items: list[CountedSubmissionSummary]
    next_cursor: str | None


class CountedIdentifiedSubmissionPage(BaseModel):
    """`SubmissionPage` for a caller with both `submitter_identity.view` and
    `interest.identities.view`."""

    model_config = ConfigDict(frozen=True)

    items: list[CountedIdentifiedSubmissionSummary]
    next_cursor: str | None


#: The item and page classes for a caller: whether they may see submitter identities, and whether
#: they may see the organisation count. A caller without a permission gets a class with no such key.
_PageClass = (
    type[SubmissionPage]
    | type[IdentifiedSubmissionPage]
    | type[CountedSubmissionPage]
    | type[CountedIdentifiedSubmissionPage]
)
_PAGES: Final[dict[tuple[bool, bool], tuple[type[BaseModel], _PageClass]]] = {
    (False, False): (SubmissionSummary, SubmissionPage),
    (True, False): (IdentifiedSubmissionSummary, IdentifiedSubmissionPage),
    (False, True): (CountedSubmissionSummary, CountedSubmissionPage),
    (True, True): (CountedIdentifiedSubmissionSummary, CountedIdentifiedSubmissionPage),
}

_STATE_PARAMETER = Query(
    description=(
        "Keep only submissions in these states. Repeat the parameter to name more than one. "
        "Omitted, the list holds every open submission: all states except `Published in release`, "
        "`Rejected` and `Withdrawn`. With `mine=true` and no `state`, every state is listed."
    )
)


def _provenance(settings: ApiSettings) -> dict[str, LabelProvenance]:
    return {
        "preferred_term": AU_PREFERRED_TERM_PROVENANCE,
        "synonyms": SYNONYM_PROVENANCE,
        "snomed_fsn": fsn_provenance(settings),
    }


def _summary_fields(
    record: SubmissionRecord,
    settings: ApiSettings,
    interest: InterestSummary,
    *,
    counts_visible: bool,
) -> dict[str, Any]:
    submission = record.submission
    return {
        "id": submission.id,
        "kind": submission.kind,
        "state": submission.state,
        "entry_business_key": record.entry_business_key,
        "preferred_term": submission.preferred_term,
        "snomed_code": submission.snomed_code,
        "snomed_fsn": submission.snomed_fsn,
        "created_at": submission.created_at,
        "is_own": record.is_own,
        "has_registered_interest": interest.registered,
        "interest_count": interest.count if counts_visible else None,
        "label_provenance": _provenance(settings),
    }


def _detail_fields(
    record: SubmissionRecord,
    settings: ApiSettings,
    interest: InterestSummary,
    *,
    counts_visible: bool,
) -> dict[str, Any]:
    submission = record.submission
    return {
        **_summary_fields(record, settings, interest, counts_visible=counts_visible),
        "synonyms": list(submission.synonyms),
        "property_values": {
            key: [
                SubmittedPropertyValue(value=item["value"], justification=item["justification"])
                for item in items
            ]
            for key, items in submission.property_values.items()
        },
        "notes": submission.notes,
        "reference_url": submission.reference_url,
        "reference_checked_at": submission.reference_checked_at,
        "reference_status": submission.reference_status,
        "duplicate_confirmed_at": submission.duplicate_confirmed_at,
    }


def _organisation_counts(
    session: SessionDep, principal: CurrentPrincipal, submission_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int] | None:
    """`None` for a caller without `interest.identities.view`: no figure is looked up for them."""
    if not principal.has(Permission.INTEREST_IDENTITIES_VIEW):
        return None
    return distinct_organisation_counts(session, submission_ids)


def _username(record: SubmissionRecord) -> str:
    return record.submitter.username or CLOSED_ACCOUNT_PLACEHOLDER


def _plain_submitter(record: SubmissionRecord) -> PlainSubmitter:
    """The whole of what a caller without `submitter_identity.view` learns about a submitter."""
    return PlainSubmitter(username=_username(record))


def _identified_submitter(record: SubmissionRecord) -> IdentifiedSubmitter:
    return IdentifiedSubmitter(
        username=_username(record),
        display_name=record.submitter.display_name or CLOSED_ACCOUNT_PLACEHOLDER,
        organisation=record.submission.organisation,
    )


@router.get(
    "",
    summary="List submissions",
    response_model=(
        SubmissionPage
        | IdentifiedSubmissionPage
        | CountedSubmissionPage
        | CountedIdentifiedSubmissionPage
    ),
    responses={
        401: _RESPONSE_401,
        403: _RESPONSE_403,
        422: _RESPONSE_422_LIST,
    },
    dependencies=[_VIEW],
)
def list_submissions(
    session: SessionDep,
    principal: CurrentPrincipal,
    settings: ApiSettingsDep,
    state: Annotated[list[SubmissionState] | None, _STATE_PARAMETER] = None,
    mine: Annotated[
        bool,
        Query(description="Send `true` to keep only the caller's own submissions, in any state."),
    ] = False,
    kind: Annotated[
        SubmissionKind | None, Query(description="Keep only new tests or only amendments.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Maximum submissions in a page.")] = 50,
    after: Annotated[
        str | None,
        Query(
            max_length=200,
            description="The `next_cursor` of the previous page, passed back unmodified.",
        ),
    ] = None,
) -> (
    SubmissionPage
    | IdentifiedSubmissionPage
    | CountedSubmissionPage
    | CountedIdentifiedSubmissionPage
):
    """Newest first, paged by cursor. Pass the response's `next_cursor` back as `after` for the
    following page. There is no `offset` and no total, on purpose (ADR-0024).

    A caller with `submitter_identity.view` gets each submitter's display name and the
    submission's organisation. Every other caller gets the username only.
    """
    page = queries.list_submissions(
        session,
        viewer_id=principal.user_id,
        limit=limit,
        after=after,
        states=[item.value for item in state or ()],
        mine=mine,
        kind=kind.value if kind else None,
    )
    interest = interest_summaries(
        session, [record.submission.id for record in page.records], viewer_id=principal.user_id
    )
    counts_visible = principal.has(Permission.INTEREST_VIEW_COUNTS)
    identified = principal.has(Permission.SUBMITTER_IDENTITY_VIEW)
    organisations = _organisation_counts(
        session, principal, [record.submission.id for record in page.records]
    )
    summary_class, page_class = _PAGES[(identified, organisations is not None)]
    items = [
        summary_class(
            **_summary_fields(
                record, settings, interest[record.submission.id], counts_visible=counts_visible
            ),
            **(
                {"distinct_organisation_count": organisations[record.submission.id]}
                if organisations is not None
                else {}
            ),
            submitter=(_identified_submitter if identified else _plain_submitter)(record),
        )
        for record in page.records
    ]
    return page_class(items=items, next_cursor=page.next_cursor)


@router.get(
    "/{submission_id}",
    summary="Show one submission",
    response_model=SubmissionDetail | IdentifiedSubmissionDetail,
    responses={
        401: _RESPONSE_401,
        403: _RESPONSE_403,
        404: _RESPONSE_404,
        422: _RESPONSE_422_DETAIL,
    },
    dependencies=[_VIEW],
)
def get_submission(
    submission_id: uuid.UUID,
    session: SessionDep,
    principal: CurrentPrincipal,
    settings: ApiSettingsDep,
) -> SubmissionDetail | IdentifiedSubmissionDetail:
    """One submission in any state, with the content the submitter sent. The submitter appears as
    on the list: a username only, unless the caller holds `submitter_identity.view`."""
    record = queries.get_submission(session, submission_id, viewer_id=principal.user_id)
    interest = interest_summaries(session, [submission_id], viewer_id=principal.user_id)[
        submission_id
    ]
    counts_visible = principal.has(Permission.INTEREST_VIEW_COUNTS)
    fields = _detail_fields(record, settings, interest, counts_visible=counts_visible)
    if principal.has(Permission.SUBMITTER_IDENTITY_VIEW):
        return IdentifiedSubmissionDetail(**fields, submitter=_identified_submitter(record))
    return SubmissionDetail(**fields, submitter=_plain_submitter(record))
