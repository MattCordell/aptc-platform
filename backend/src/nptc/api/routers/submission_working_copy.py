"""The route that edits a submission's working copy (FR-23, FR-44, NFR-08).

`PATCH /submissions/{id}/working-copy` is the HTTP adapter over `nptc.submissions.working_copy`. It
re-implements no rule: the state check, the optimistic lock, the code lookup and the property check
all live in the service, and a domain exception carries `http_status`, which `nptc.api.errors` maps.

**Authorisation:** `Permission.SUBMISSION_TRANSITION_REVIEW` (FR-44), held by Reviewer and
Administrator. No other caller can edit, and the submitter never sees that a working copy exists:
`GET /submissions/{id}` leaves `working_copy` out for them. The terms gate applies as to every
non-GET route (NFR-45).

**The body is partial.** A field left out stays as it is. `synonyms` and `property_values` replace
the whole field, and a blank `notes` clears the note. A field sent as `null` is a 422, because
there is no way to clear a code, so `null` would mean two things. There is no `snomed_fsn`: the label
is whatever the terminology server returns for the code (FR-82).

**A refusal is a 404, a 409, a 422 or a 5xx.** An unknown submission is a 404. A state outside
Triage, Under review and Ready for approval is a 409, and so is a stale `expected_row_version`, which
carries the current one. A blank reason, an unusable term, a refused code, a property problem or a
field an amendment does not propose is a 422. The terminology server being unreachable is a 503. A
refusal writes nothing.

**An edit that changes nothing is a 200 with `changed` false.** Nothing was written, the
`row_version` is the one the caller sent, and no audit event was appended.

**The route is a plain `def`,** so FastAPI runs it in a worker thread and the terminology call never
stalls the event loop.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Final

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from nptc.api.dependencies import (
    ApiSettingsDep,
    AuditContextDep,
    permission_dep,
)
from nptc.api.errors import (
    PropertyValidationResponse,
    SubmissionFieldRefusalResponse,
    SubmissionVersionConflictResponse,
)
from nptc.api.labels import (
    AU_PREFERRED_TERM_PROVENANCE,
    SYNONYM_PROVENANCE,
    LabelProvenance,
    fsn_provenance,
)
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.submissions import (
    _MAX_NOTES_LENGTH,
    _MAX_PROPERTIES,
    _MAX_PROPERTY_KEY_LENGTH,
    _MAX_SCTID_LENGTH,
    _MAX_SYNONYMS,
    _MAX_VALUES_IN_TOTAL,
    _MAX_VALUES_PER_PROPERTY,
    RegistryDep,
    SessionDep,
    SubmissionPropertyValueRequest,
    SubmittedPropertyValue,
    TerminologyClientDep,
    _Term,
)
from nptc.auth.permissions import Permission
from nptc.catalogue.property_values import PropertyValueInput
from nptc.db.models.submission_working_copy import SubmissionWorkingCopy
from nptc.settings import ApiSettings
from nptc.submissions.working_copy import WorkingContent, WorkingCopyChanges, edit_working_copy

__all__ = [
    "WorkingCopyContent",
    "WorkingCopyRequest",
    "WorkingCopyResponse",
    "WorkingCopyView",
    "label_provenance",
    "router",
    "working_copy_view",
]

router = APIRouter(prefix="/submissions", tags=["submissions"])

_EDIT = Depends(permission_dep(Permission.SUBMISSION_TRANSITION_REVIEW))

_MAX_REASON_LENGTH: Final = 2_000
_CONTENT_FIELDS: Final = ("preferred_term", "synonyms", "snomed_code", "property_values", "notes")

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
            "The submission is not in `Triage`, `Under review` or `Ready for approval`, so it "
            "cannot be edited. Or `expected_row_version` is not the current one, because someone "
            "else changed the submission after it was read; the body then carries "
            "`current_row_version`. Nothing was changed."
        ),
    },
    422: {
        "model": SubmissionFieldRefusalResponse | ErrorResponse | PropertyValidationResponse,
        "description": (
            "The request is not acceptable. `detail` says why for a blank reason, a term that "
            "cannot be cleaned, a SNOMED CT code that is malformed, unknown to the AU edition, "
            "inactive, has no reported status or has no fully specified name, free text that "
            "carries an invisible character, and an amendment edit that names a field an amendment "
            "does not propose or would leave it with no synonym and no code. `issues[]` names each "
            "property problem. A refusal of one field carries `field`. A field sent as `null`, an "
            "unrecognised field, a request with no content field, or a part of the request over its "
            "size bound fails validation before the route runs."
        ),
    },
    502: {
        "model": SubmissionFieldRefusalResponse | ErrorResponse,
        "description": (
            "The terminology server's response could not be used. `field` is `snomed_code` when "
            "it was checking the `snomed_code`."
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


class WorkingCopyRequest(BaseModel):
    """The body of `PATCH /submissions/{id}/working-copy`.

    Send at least one of `preferred_term`, `synonyms`, `snomed_code`, `property_values` and `notes`.
    A field left out stays as it is. A field sent as `null` is refused. `synonyms` and
    `property_values` replace the whole field, and a blank `notes` clears the note. `reason` says
    why, and `expected_row_version` is the `row_version` the caller read, so an edit never lands on
    a submission that changed after the caller looked at it. An amendment accepts only `synonyms`
    and `snomed_code`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    preferred_term: _Term | None = None
    synonyms: list[_Term] | None = Field(default=None, max_length=_MAX_SYNONYMS)
    snomed_code: str | None = Field(default=None, max_length=_MAX_SCTID_LENGTH)
    property_values: (
        dict[
            Annotated[str, StringConstraints(max_length=_MAX_PROPERTY_KEY_LENGTH)],
            Annotated[
                list[SubmissionPropertyValueRequest], Field(max_length=_MAX_VALUES_PER_PROPERTY)
            ],
        ]
        | None
    ) = Field(default=None, max_length=_MAX_PROPERTIES)
    notes: str | None = Field(default=None, max_length=_MAX_NOTES_LENGTH)
    reason: str = Field(max_length=_MAX_REASON_LENGTH)
    expected_row_version: int = Field(ge=1)

    @model_validator(mode="after")
    def _content_is_present_and_never_null(self) -> WorkingCopyRequest:
        sent = [name for name in _CONTENT_FIELDS if name in self.model_fields_set]
        if not sent:
            raise ValueError(f"send at least one of {', '.join(_CONTENT_FIELDS)}")
        for name in sent:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null: leave it out to keep it as it is")
        total = sum(len(items) for items in (self.property_values or {}).values())
        if total > _MAX_VALUES_IN_TOTAL:
            raise ValueError(f"a submission holds at most {_MAX_VALUES_IN_TOTAL} property values")
        return self


class WorkingCopyContent(BaseModel):
    """The content of a working copy. `snomed_fsn` is the label the terminology server returned for
    `snomed_code`, never anything a caller sent (FR-82). `label_provenance` states which designation
    each label field is (FR-98)."""

    model_config = ConfigDict(frozen=True)

    preferred_term: str
    synonyms: list[str]
    snomed_code: str | None
    snomed_fsn: str | None
    property_values: dict[str, list[SubmittedPropertyValue]]
    notes: str | None
    label_provenance: dict[str, LabelProvenance]


class WorkingCopyView(WorkingCopyContent):
    """A working copy as `GET /submissions/{id}` shows it to a holder of
    `submission.transition.review`. `updated_at` is the latest effective edit."""

    updated_at: datetime


class WorkingCopyResponse(WorkingCopyContent):
    """The working content after an edit, with the `row_version` to send with the next change.
    `changed` is false when the edit left the content as it was and nothing was written, and then
    the content may be the submission's original, because a first no-op creates no working copy."""

    submission_id: uuid.UUID
    row_version: int
    changed: bool


def _property_values(
    stored: dict[str, list[dict[str, object]]],
) -> dict[str, list[SubmittedPropertyValue]]:
    return {
        key: [
            SubmittedPropertyValue(value=item["value"], justification=item["justification"])
            for item in items
        ]
        for key, items in stored.items()
    }


def label_provenance(settings: ApiSettings) -> dict[str, LabelProvenance]:
    return {
        "preferred_term": AU_PREFERRED_TERM_PROVENANCE,
        "synonyms": SYNONYM_PROVENANCE,
        "snomed_fsn": fsn_provenance(settings),
    }


def working_copy_view(copy: SubmissionWorkingCopy, settings: ApiSettings) -> WorkingCopyView:
    return WorkingCopyView(
        preferred_term=copy.preferred_term,
        synonyms=list(copy.synonyms),
        snomed_code=copy.snomed_code,
        snomed_fsn=copy.snomed_fsn,
        property_values=_property_values(copy.property_values),
        notes=copy.notes,
        label_provenance=label_provenance(settings),
        updated_at=copy.updated_at,
    )


@router.patch(
    "/{submission_id}/working-copy",
    summary="Edit a submission's working copy",
    response_model=WorkingCopyResponse,
    responses=_RESPONSES,
    dependencies=[_EDIT],
)
def edit_submission_working_copy(
    submission_id: uuid.UUID,
    body: Annotated[WorkingCopyRequest, Body()],
    session: SessionDep,
    ctx: AuditContextDep,
    registry: RegistryDep,
    terminology_client: TerminologyClientDep,
    settings: ApiSettingsDep,
) -> WorkingCopyResponse:
    """Applies the fields sent to the working copy, creating it on the first effective edit. The
    submitter's own content never changes. An effective edit bumps the submission's `row_version`
    and appends one audit event, in one transaction."""
    edit = edit_working_copy(
        session,
        ctx,
        submission_id=submission_id,
        changes=WorkingCopyChanges(
            preferred_term=body.preferred_term,
            synonyms=body.synonyms,
            snomed_code=body.snomed_code,
            property_values=(
                None
                if body.property_values is None
                else {
                    key: [
                        PropertyValueInput(value=item.value, justification=item.justification)
                        for item in items
                    ]
                    for key, items in body.property_values.items()
                }
            ),
            notes=body.notes,
        ),
        reason=body.reason,
        expected_row_version=body.expected_row_version,
        registry=registry,
        terminology_client=terminology_client,
    )
    return _answer(
        edit.submission.id, edit.submission.row_version, edit.changed, edit.content, settings
    )


def _answer(
    submission_id: uuid.UUID,
    row_version: int,
    changed: bool,
    content: WorkingContent,
    settings: ApiSettings,
) -> WorkingCopyResponse:
    return WorkingCopyResponse(
        submission_id=submission_id,
        row_version=row_version,
        changed=changed,
        preferred_term=content.preferred_term,
        synonyms=list(content.synonyms),
        snomed_code=content.snomed_code,
        snomed_fsn=content.snomed_fsn,
        property_values=_property_values(content.property_values),
        notes=content.notes,
        label_provenance=label_provenance(settings),
    )
