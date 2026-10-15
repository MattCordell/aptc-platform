"""Waiting for a code that does not exist yet (FR-30, FR-31, NFR-08).

A reviewer who cannot find a SNOMED CT code for a proposed test moves the submission from
`Under review` to `Awaiting terminology`, and records what was asked for and where
(`nptc.db.models.submission_terminology_request`). The move and the record are written together:
a submission never waits without a complete record, and a record never exists for a submission
that did not move.

**The move is a row of `PERMITTED_TRANSITIONS`,** so the permission, the order of the checks and
the optimistic lock are those of every other move (`nptc.submissions.transitions`). This module adds
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

from nptc.audit.diffing import ChangeKind
from nptc.audit.policy import policy_for
from nptc.audit.recording import record_change, record_snapshot_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.auth.principal import Principal
from nptc.db.models.submission import Submission, SubmissionState
from nptc.db.models.submission_terminology_request import (
    SubmissionTerminologyRequest,
    TargetEdition,
)
from nptc.db.models.submission_working_copy import SubmissionWorkingCopy
from nptc.submissions.errors import (
    FreeTextField,
    SubmissionNotFoundError,
    TerminologyRequestFieldRequiredError,
)
from nptc.submissions.shared import clean_free_text, resolve_code
from nptc.submissions.transitions import (
    PermittedTransition,
    check_move,
    check_row_version,
    clean_transition_reason,
    load_submission_locked,
    perform_move,
)
from nptc.submissions.working_copy import (
    WorkingContent,
    store_working_content,
    working_content_of,
)
from nptc_shared.terminology import TerminologyClient

__all__ = [
    "TerminologyAttachment",
    "TerminologyRequestDetails",
    "attach_terminology_code",
    "clean_terminology_request",
    "request_terminology",
]

_RECORD_CREATED = "submission.terminology_request_created"


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
            entity_id=str(submission.id),
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
    no session, so a writer runs it ahead of the append lock and a refused request takes none."""
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
    grant it), `nptc.submissions.terms.SubmissionCodeMalformedError` for a code that is not a SCTID,
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
