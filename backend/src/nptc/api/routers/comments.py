"""The internal comment routes: staff-only notes on a submission or a catalogue entry (FR-95, NFR-08).

The same three routes exist under each target, `/submissions/{submission_id}/comments` and
`/catalogue/entries/{business_key}/comments`:

- `GET` lists the comments, oldest first.
- `POST` adds one.
- `POST .../{comment_id}/withdraw` marks one withdrawn, with a required reason.

They are the HTTP adapter over `nptc.comments.service`. They re-implement no rule, and a domain
exception carries `http_status`, which `nptc.api.errors` maps.

**Authorisation:** `Permission.COMMENT_INTERNAL_READ` gates the `GET` routes and
`Permission.COMMENT_INTERNAL_WRITE` the others (FR-44). Both are route dependencies, so a caller
without the permission gets a 403 before the target is looked up. The refusal is the same for a
target with comments, one without and one that does not exist, so a submitter cannot tell whether a
submission has comments. Any holder of the write permission may withdraw any comment.

**Nothing here appears on any other route.** No submission, entry, list, export or public route
mentions comments, and a comment write changes neither the submission nor the entry it is on.

**A withdrawn comment keeps its text,** and every caller who may read comments sees it, beside who
withdrew it, when and why. A comment withdrawn twice is a 409, and a comment named under a target it
is not on is a 404, the same answer as an unknown comment.

**`body` is returned as literal text** and never interpreted (NFR-23). A blank `body` or `reason`, or
one with an invisible character, is a 422 that names the field. The terms gate applies to every
non-GET route (NFR-45).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from nptc.api.dependencies import AuditContextDep, permission_dep
from nptc.api.errors import SubmissionFieldRefusalResponse
from nptc.api.routers.auth import ErrorResponse
from nptc.api.routers.catalogue_shared import BusinessKeyPath
from nptc.api.routers.submissions import SessionDep
from nptc.api.routers.submissions_read import CLOSED_ACCOUNT_PLACEHOLDER
from nptc.auth.identity import UserRef
from nptc.auth.permissions import Permission
from nptc.comments.service import (
    CommentRecord,
    CommentTarget,
    EntryTarget,
    SubmissionTarget,
    list_comments,
    post_comment,
    withdraw_comment,
)

router = APIRouter(tags=["comments"])

_READ = Depends(permission_dep(Permission.COMMENT_INTERNAL_READ))
_WRITE = Depends(permission_dep(Permission.COMMENT_INTERNAL_WRITE))

_MAX_TEXT_LENGTH: Final = 2_000

_SUBMISSION_COMMENTS = "/submissions/{submission_id}/comments"
_ENTRY_COMMENTS = "/catalogue/entries/{business_key}/comments"
_WITHDRAW = "/{comment_id}/withdraw"


def _responses(
    permission: Permission, *, target: str, withdraw: bool = False
) -> dict[int | str, dict[str, Any]]:
    responses: dict[int | str, dict[str, Any]] = {
        401: {
            "model": ErrorResponse,
            "description": "No credential, or one that could not be verified.",
        },
        403: {
            "model": ErrorResponse,
            "description": (
                f"The caller does not hold `{permission.value}`, or has not accepted the current "
                "terms of use (the body then carries a `code` that says so). The refusal is the "
                "same whether or not the target exists or has comments."
            ),
        },
        404: {
            "model": ErrorResponse,
            "description": (
                f"No {target} has the identifier"
                + (", or no comment with that identifier is on it." if withdraw else ".")
            ),
        },
        422: {
            "model": SubmissionFieldRefusalResponse,
            "description": (
                "A blank `body` or `reason`, or one with an invisible character such as a "
                "zero-width space (`field` is `body` or `reason`). A malformed identifier or a "
                "text over the size bound fails validation before the route runs."
            ),
        },
    }
    if withdraw:
        responses[409] = {
            "model": ErrorResponse,
            "description": "The comment was withdrawn before. Nothing was changed.",
        }
    return responses


class CommentPerson(BaseModel):
    """A person named on a comment. Both names are `Closed account` for a closed account."""

    model_config = ConfigDict(frozen=True)

    username: str
    display_name: str


class CommentResponse(BaseModel):
    """One comment. `body` is plain text. The three `withdrawn_*` values are all null for a comment
    that stands and all set for one that was withdrawn, which keeps its `body`."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    body: str
    created_at: datetime
    author: CommentPerson
    withdrawn_at: datetime | None
    withdrawn_by: CommentPerson | None
    withdrawn_reason: str | None


class CommentListResponse(BaseModel):
    """The comments on one target, oldest first, withdrawn ones included."""

    model_config = ConfigDict(frozen=True)

    comments: list[CommentResponse]


class PostCommentRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    body: str = Field(max_length=_MAX_TEXT_LENGTH)


class WithdrawCommentRequest(BaseModel):
    """`reason` is required and is kept with the withdrawal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(max_length=_MAX_TEXT_LENGTH)


def _person(user: UserRef) -> CommentPerson:
    return CommentPerson(
        username=user.username or CLOSED_ACCOUNT_PLACEHOLDER,
        display_name=user.display_name or CLOSED_ACCOUNT_PLACEHOLDER,
    )


def _comment(record: CommentRecord) -> CommentResponse:
    return CommentResponse(
        id=record.id,
        body=record.body,
        created_at=record.created_at,
        author=_person(record.author),
        withdrawn_at=record.withdrawn_at,
        withdrawn_by=_person(record.withdrawn_by) if record.withdrawn_by else None,
        withdrawn_reason=record.withdrawn_reason,
    )


def _listing(session: SessionDep, target: CommentTarget) -> CommentListResponse:
    return CommentListResponse(
        comments=[_comment(record) for record in list_comments(session, target)]
    )


@router.get(
    _SUBMISSION_COMMENTS,
    summary="List the internal comments on a submission",
    response_model=CommentListResponse,
    responses=_responses(Permission.COMMENT_INTERNAL_READ, target="submission"),
    dependencies=[_READ],
)
def list_submission_comments(submission_id: uuid.UUID, session: SessionDep) -> CommentListResponse:
    """Every comment, oldest first, withdrawn ones included with their text. A submission with no
    comments gives an empty list."""
    return _listing(session, SubmissionTarget(submission_id))


@router.post(
    _SUBMISSION_COMMENTS,
    summary="Add an internal comment to a submission",
    status_code=201,
    response_model=CommentResponse,
    responses=_responses(Permission.COMMENT_INTERNAL_WRITE, target="submission"),
    dependencies=[_WRITE],
)
def add_submission_comment(
    submission_id: uuid.UUID, body: PostCommentRequest, session: SessionDep, ctx: AuditContextDep
) -> CommentResponse:
    """Adds the comment and appends its audit event. The submission itself is not changed."""
    return _comment(post_comment(session, ctx, SubmissionTarget(submission_id), body=body.body))


@router.post(
    _SUBMISSION_COMMENTS + _WITHDRAW,
    summary="Withdraw an internal comment on a submission",
    response_model=CommentResponse,
    responses=_responses(Permission.COMMENT_INTERNAL_WRITE, target="submission", withdraw=True),
    dependencies=[_WRITE],
)
def withdraw_submission_comment(
    submission_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: WithdrawCommentRequest,
    session: SessionDep,
    ctx: AuditContextDep,
) -> CommentResponse:
    """Marks the comment withdrawn, with the reason. Its text stays."""
    return _comment(
        withdraw_comment(
            session, ctx, SubmissionTarget(submission_id), comment_id, reason=body.reason
        )
    )


@router.get(
    _ENTRY_COMMENTS,
    summary="List the internal comments on a catalogue entry",
    response_model=CommentListResponse,
    responses=_responses(Permission.COMMENT_INTERNAL_READ, target="catalogue entry"),
    dependencies=[_READ],
)
def list_entry_comments(business_key: BusinessKeyPath, session: SessionDep) -> CommentListResponse:
    """Every comment, oldest first, withdrawn ones included with their text. An entry of any
    status, a draft included, can be read here. An entry with no comments gives an empty list."""
    return _listing(session, EntryTarget(business_key))


@router.post(
    _ENTRY_COMMENTS,
    summary="Add an internal comment to a catalogue entry",
    status_code=201,
    response_model=CommentResponse,
    responses=_responses(Permission.COMMENT_INTERNAL_WRITE, target="catalogue entry"),
    dependencies=[_WRITE],
)
def add_entry_comment(
    business_key: BusinessKeyPath,
    body: PostCommentRequest,
    session: SessionDep,
    ctx: AuditContextDep,
) -> CommentResponse:
    """Adds the comment and appends its audit event. The entry itself is not changed, and its
    public history does not show the comment."""
    return _comment(post_comment(session, ctx, EntryTarget(business_key), body=body.body))


@router.post(
    _ENTRY_COMMENTS + _WITHDRAW,
    summary="Withdraw an internal comment on a catalogue entry",
    response_model=CommentResponse,
    responses=_responses(
        Permission.COMMENT_INTERNAL_WRITE, target="catalogue entry", withdraw=True
    ),
    dependencies=[_WRITE],
)
def withdraw_entry_comment(
    business_key: BusinessKeyPath,
    comment_id: uuid.UUID,
    body: WithdrawCommentRequest,
    session: SessionDep,
    ctx: AuditContextDep,
) -> CommentResponse:
    """Marks the comment withdrawn, with the reason. Its text stays."""
    return _comment(
        withdraw_comment(session, ctx, EntryTarget(business_key), comment_id, reason=body.reason)
    )
