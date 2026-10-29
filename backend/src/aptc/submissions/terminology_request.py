"""Waiting for a code that does not exist yet (FR-30, FR-31, NFR-08).

A reviewer who cannot find a SNOMED CT code for a proposed test moves the submission from
`Under review` to `Awaiting terminology`, and records what was asked for and where
(`aptc.db.models.submission_terminology_request`). The move and the record are written together:
a submission never waits without a complete record, and a record never exists for a submission
that did not move.

**The move is a row of `PERMITTED_TRANSITIONS`,** so the permission, the order of the checks and
the optimistic lock are those of every other move (`aptc.submissions.transitions`). This module adds
only what the move carries: the record, and the second audit event that says what it holds.

**Every refusal that needs no read runs before the lock.** The reason and the four text fields are
cleaned first, so a refused request takes no lock. The submission is read only after the lock.

**Attaching the code is the one move out, and only an Administrator makes it.** The code is looked up
on the terminology server in the AU edition, whatever `target_edition` says, because `target_edition`
records where the concept was requested and the platform's own edition is the one it serves (FR-82).
The code and its FSN are written to the working copy, the state moves to `Ready for approval` and one
audit event records all three, in one savepoint. The record stays as the history of what was asked.

**The lookup runs before the lock.** The append lock is one global advisory lock, so holding it across
a slow server would stall every audit write. `attach_terminology_code` therefore reads the submission
and judges the move first, so a caller who may not make it never causes a terminology call, then calls
the server, then takes the lock and judges the move again against the committed row.
`test_lock_ordering.py` names this function as exempt from its lock-first rule, and
`test_submissions_terminology_request.py` pins the ordering instead.

**While the submission waits, the tracking fields can change** (FR-31): the external reference and
the expected release, because a request with a third party moves on without the submission's content
changing. What was asked for (`proposed_fsn`, `target_edition`, `justification`, `raised_date`) never
changes, and the `UPDATE` grant says so. A change bumps `submission.row_version`, the one lock, so an
edit and a move cannot cross, appends one audit event naming only the fields that changed, and writes
nothing when the request changes nothing.

**Two audit events when the request is made, and each says one thing.** `submission.terminology_requested` is the change of
state, on the submission, with the reason, as for any move. `submission.terminology_request_created`
is the record, with every field it holds. The record is written through a snapshot because the
first event flushes the session, and an ORM diff of a row already flushed is empty.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import date

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from aptc.audit.diffing import ChangeKind
from aptc.audit.policy import policy_for
from aptc.audit.recording import record_change, record_snapshot_change
from aptc.audit.writer import AuditContext, acquire_append_lock
from aptc.auth.principal import Principal
from aptc.db.models.submission import Submission, SubmissionState
from aptc.db.models.submission_terminology_request import (
    SubmissionTerminologyRequest,
    TargetEdition,
)
from aptc.db.models.submission_working_copy import SubmissionWorkingCopy
from aptc.submissions.errors import (
    ExpectedReleaseBeforeRaisedError,
    FreeTextField,
    SubmissionNotFoundError,
    SubmissionVersionConflictError,
    TerminologyRequestFieldRequiredError,
    TerminologyRequestNotOpenError,
)
from aptc.submissions.queries import get_terminology_request
from aptc.submissions.shared import clean_free_text, resolve_code
from aptc.submissions.transitions import (
    PermittedTransition,
    check_move,
    check_row_version,
    clean_transition_reason,
    load_submission_locked,
    perform_move,
)
from aptc.submissions.working_copy import (
    WorkingContent,
    store_working_content,
    working_content_of,
)
from aptc_shared.terminology import TerminologyClient

__all__ = [
    "TerminologyAttachment",
    "TerminologyRequestDetails",
    "TerminologyRequestUpdate",
    "TerminologyTrackingChanges",
    "attach_terminology_code",
    "clean_terminology_request",
    "clean_terminology_tracking",
    "request_terminology",
    "update_terminology_request",
]

_RECORD_CREATED = "submission.terminology_request_created"
_RECORD_UPDATED = "submission.terminology_request_updated"


@dataclass(frozen=True)
class TerminologyRequestDetails:
    """What a reviewer says was asked for. Every field but `expected_release` is required, because
    a request nobody can follow up is not a request (FR-31)."""

    proposed_fsn: str
    target_edition: TargetEdition
    justification: str
    external_reference_system: str
    external_reference_ref: str
    raised_date: date
    expected_release: date | None = None


@dataclass(frozen=True)
class TerminologyTrackingChanges:
    """What a reviewer changes while the submission waits. A field left out stays as it is. The
    external reference is replaced as a pair, because a system without its reference cannot be
    followed. `expected_release_given` is true when `expected_release` was sent, so a sent `None`
    clears the date and a date left out keeps it."""

    external_reference_system: str | None = None
    external_reference_ref: str | None = None
    expected_release: date | None = None
    expected_release_given: bool = False


@dataclass(frozen=True)
class TerminologyRequestUpdate:
    """The outcome of a change to the tracking fields. `changed` is false when the request left the
    record as it was, and then nothing was written. `submission` carries the `row_version` to send
    next."""

    submission: Submission
    record: SubmissionTerminologyRequest
    changed: bool


@dataclass(frozen=True)
class TerminologyAttachment:
    """The outcome of attaching a code: the submission as it now stands and the working content that
    carries the code and its FSN."""

    submission: Submission
    content: WorkingContent


def request_terminology(
    session: Session,
    ctx: AuditContext,
    principal: Principal,
    *,
    submission_id: uuid.UUID,
    details: TerminologyRequestDetails,
    reason: str,
    expected_row_version: int,
) -> Submission:
    """Moves an `Under review` submission to `Awaiting terminology`, creates its terminology request
    record, writes the transition row and appends both audit events. Returns the submission as it
    now stands.

    Raises `TransitionReasonRequiredError` for a blank reason, `TerminologyRequestFieldRequiredError`
    for a text field that is empty once cleaned, `FreeTextRefusedError` for one with an invisible
    character, `SubmissionNotFoundError` for an unknown submission,
    `ExpectedReleaseBeforeRaisedError` for a release before the day the request was raised,
    `IllegalSubmissionTransitionError` for a submission that is not `Under review`,
    `PermissionDeniedError` for a caller without `submission.transition.review`
    (`MfaRequiredError` when a role suppressed for want of MFA would grant it) and
    `SubmissionVersionConflictError` for a stale `expected_row_version`. A refusal writes nothing.
    """
    cleaned_reason = clean_transition_reason(reason)
    details = clean_terminology_request(details)

    acquire_append_lock(session)
    submission = load_submission_locked(session, submission_id)
    permitted = check_move(
        principal,
        submission,
        SubmissionState.AWAITING_TERMINOLOGY,
        required_from=SubmissionState.UNDER_REVIEW,
    )
    check_row_version(submission, expected_row_version)

    def write() -> None:
        record_change(
            session,
            ctx,
            action=permitted.action,
            instance=submission,
            kind=ChangeKind.UPDATED,
            reason=cleaned_reason,
        )
        record = SubmissionTerminologyRequest(
            id=uuid.uuid4(),
            submission_id=submission.id,
            proposed_fsn=details.proposed_fsn,
            target_edition=details.target_edition.value,
            justification=details.justification,
            external_reference_system=details.external_reference_system,
            external_reference_ref=details.external_reference_ref,
            raised_date=details.raised_date,
            expected_release=details.expected_release,
        )
        session.add(record)
        record_snapshot_change(
            session,
            ctx,
            action=_RECORD_CREATED,
            entity_type=SubmissionTerminologyRequest.__tablename__,
            entity_id=str(record.id),
            policy=policy_for(SubmissionTerminologyRequest),
            before=None,
            after={
                "proposed_fsn": details.proposed_fsn,
                "target_edition": details.target_edition.value,
                "justification": details.justification,
                "external_reference_system": details.external_reference_system,
                "external_reference_ref": details.external_reference_ref,
                "raised_date": details.raised_date,
                "expected_release": details.expected_release,
            },
            kind=ChangeKind.CREATED,
            reason=cleaned_reason,
        )

    return perform_move(
        session,
        principal,
        submission,
        to_state=SubmissionState.AWAITING_TERMINOLOGY,
        reason=cleaned_reason,
        expected_row_version=expected_row_version,
        write=write,
    )


def clean_terminology_request(details: TerminologyRequestDetails) -> TerminologyRequestDetails:
    """`details` with its four text fields cleaned as free text is (FR-63), or a refusal. It touches
    no session, so a writer runs it ahead of the append lock and a refused request takes none. An
    expected release before the day the request was raised is refused too."""
    if details.expected_release is not None and details.expected_release < details.raised_date:
        raise ExpectedReleaseBeforeRaisedError
    return replace(
        details,
        proposed_fsn=_required(details.proposed_fsn, FreeTextField.PROPOSED_FSN, multiline=False),
        justification=_required(details.justification, FreeTextField.JUSTIFICATION, multiline=True),
        external_reference_system=_required(
            details.external_reference_system,
            FreeTextField.EXTERNAL_REFERENCE_SYSTEM,
            multiline=False,
        ),
        external_reference_ref=_required(
            details.external_reference_ref, FreeTextField.EXTERNAL_REFERENCE_REF, multiline=False
        ),
    )


def _required(text: str, field: FreeTextField, *, multiline: bool) -> str:
    cleaned = clean_free_text(text, field=field, multiline=multiline)
    if cleaned is None:
        raise TerminologyRequestFieldRequiredError(field)
    return cleaned


def attach_terminology_code(
    session: Session,
    ctx: AuditContext,
    principal: Principal,
    *,
    submission_id: uuid.UUID,
    snomed_code: str,
    reason: str,
    expected_row_version: int,
    terminology_client: TerminologyClient,
) -> TerminologyAttachment:
    """Looks the code up on the terminology server, writes the code and its FSN to the working copy
    of an `Awaiting terminology` submission, moves it to `Ready for approval`, writes the transition
    row and appends one audit event. Returns the outcome.

    Raises `TransitionReasonRequiredError` for a blank reason, `SubmissionNotFoundError` for an
    unknown submission, `IllegalSubmissionTransitionError` for a submission that is not
    `Awaiting terminology`, `PermissionDeniedError` for a caller without
    `submission.transition.approve` (`MfaRequiredError` when a role suppressed for want of MFA would
    grant it), `aptc.submissions.terms.SubmissionCodeMalformedError` for a code that is not a SCTID,
    `SubmissionCodeRefusedError` for a code the server does not know or rules out,
    `SubmissionCodeUnavailableError` and `SubmissionCodeUpstreamError` for a server that cannot be
    reached or answers with something unusable, and `SubmissionVersionConflictError` for a stale
    `expected_row_version`. A refusal writes nothing. Blocks on the terminology server.
    """
    cleaned_reason = clean_transition_reason(reason)

    submission = session.get(Submission, submission_id)
    if submission is None:
        raise SubmissionNotFoundError("no submission has that identifier")
    _judge_attach(principal, submission)
    code, fsn = resolve_code(terminology_client, snomed_code)

    acquire_append_lock(session)
    submission = load_submission_locked(session, submission_id)
    permitted = _judge_attach(principal, submission)
    check_row_version(submission, expected_row_version)

    copy = session.get(SubmissionWorkingCopy, submission.id, populate_existing=True)
    base = working_content_of(submission if copy is None else copy)
    new = replace(base, snomed_code=code, snomed_fsn=fsn)
    changed = [
        name for name in ("snomed_code", "snomed_fsn") if getattr(new, name) != getattr(base, name)
    ]

    def write() -> None:
        if changed:
            store_working_content(session, submission, copy, new, changed)
        record_snapshot_change(
            session,
            ctx,
            action=permitted.action,
            entity_type=Submission.__tablename__,
            entity_id=str(submission.id),
            policy=policy_for(Submission),
            before={
                "state": SubmissionState.AWAITING_TERMINOLOGY.value,
                "snomed_code": base.snomed_code,
                "snomed_fsn": base.snomed_fsn,
            },
            after={
                "state": SubmissionState.READY_FOR_APPROVAL.value,
                "snomed_code": code,
                "snomed_fsn": fsn,
            },
            kind=ChangeKind.UPDATED,
            reason=cleaned_reason,
        )

    moved = perform_move(
        session,
        principal,
        submission,
        to_state=SubmissionState.READY_FOR_APPROVAL,
        reason=cleaned_reason,
        expected_row_version=expected_row_version,
        write=write,
    )
    return TerminologyAttachment(submission=moved, content=new)


def _judge_attach(principal: Principal, submission: Submission) -> PermittedTransition:
    return check_move(
        principal,
        submission,
        SubmissionState.READY_FOR_APPROVAL,
        required_from=SubmissionState.AWAITING_TERMINOLOGY,
    )


def clean_terminology_tracking(changes: TerminologyTrackingChanges) -> TerminologyTrackingChanges:
    """`changes` with the external reference cleaned as free text is (FR-63), or a refusal. It
    touches no session, so a writer runs it ahead of the append lock and a refused request takes
    none."""
    if changes.external_reference_system is None and changes.external_reference_ref is None:
        return changes
    if changes.external_reference_system is None or changes.external_reference_ref is None:
        raise ValueError("the external reference is replaced as a pair: send both or neither")
    return replace(
        changes,
        external_reference_system=_required(
            changes.external_reference_system,
            FreeTextField.EXTERNAL_REFERENCE_SYSTEM,
            multiline=False,
        ),
        external_reference_ref=_required(
            changes.external_reference_ref, FreeTextField.EXTERNAL_REFERENCE_REF, multiline=False
        ),
    )


def update_terminology_request(
    session: Session,
    ctx: AuditContext,
    *,
    submission_id: uuid.UUID,
    changes: TerminologyTrackingChanges,
    reason: str,
    expected_row_version: int,
) -> TerminologyRequestUpdate:
    """Applies `changes` to the tracking fields of the submission's terminology request, bumps the
    submission's `row_version` and appends one audit event. Returns the outcome. A request that
    changes nothing writes nothing, but is still judged against the committed row under the lock.

    Raises `TransitionReasonRequiredError` for a blank reason,
    `TerminologyRequestFieldRequiredError` for a reference field that is empty once cleaned,
    `FreeTextRefusedError` for one with an invisible character, `SubmissionNotFoundError` for an
    unknown submission, `TerminologyRequestNotOpenError` for a submission that is not
    `Awaiting terminology`, `SubmissionVersionConflictError` for a stale `expected_row_version`
    and `ExpectedReleaseBeforeRaisedError` for a release before the day the request was raised. It
    changes the newest request of the submission. The caller's permission is judged by the route,
    as for the working copy.
    A refusal writes nothing.
    """
    cleaned_reason = clean_transition_reason(reason)
    changes = clean_terminology_tracking(changes)

    acquire_append_lock(session)
    submission = load_submission_locked(session, submission_id)
    record = get_terminology_request(session, submission.id, populate_existing=True)
    if submission.state != SubmissionState.AWAITING_TERMINOLOGY.value or record is None:
        raise TerminologyRequestNotOpenError(submission.state)
    check_row_version(submission, expected_row_version)
    if (
        changes.expected_release_given
        and changes.expected_release is not None
        and changes.expected_release < record.raised_date
    ):
        raise ExpectedReleaseBeforeRaisedError

    new_values: dict[str, object] = {}
    if changes.external_reference_system is not None:
        new_values["external_reference_system"] = changes.external_reference_system
        new_values["external_reference_ref"] = changes.external_reference_ref
    if changes.expected_release_given:
        new_values["expected_release"] = changes.expected_release
    changed = [name for name, value in new_values.items() if getattr(record, name) != value]
    if not changed:
        return TerminologyRequestUpdate(submission=submission, record=record, changed=False)

    savepoint = session.begin_nested()
    try:
        for name in changed:
            setattr(record, name, new_values[name])
        # The manual bump marks the submission dirty, so the flush issues the UPDATE that
        # `version_id_col` guards: a writer that skipped the lock still fails on a stale version.
        submission.row_version += 1
        record_change(
            session,
            ctx,
            action=_RECORD_UPDATED,
            instance=record,
            kind=ChangeKind.UPDATED,
            reason=cleaned_reason,
        )
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
    return TerminologyRequestUpdate(submission=submission, record=record, changed=True)
