"""Approving a submission writes the catalogue entry (FR-28, FR-29, FR-35, NFR-08).

An Administrator moves a submission from `Ready for approval` to `Approved`. A new test becomes an
`active` entry, and an amendment is applied to the entry it names. The entry writes, the new `state`,
the transition row and every audit event succeed or fail together: a refusal at any step leaves the
submission in `Ready for approval` and the catalogue as it was.

**The move is a row of `PERMITTED_TRANSITIONS`,** so the permission, the order of the checks and the
optimistic lock are those of every other move (`nptc.submissions.transitions`). This module adds
only what the move carries: the catalogue writes. The row is `needs_payload`, so the general route
refuses it and this service is the one way in.

**The content is the working copy when there is one,** otherwise the original, as a reviewer saw it.

**The reason is the changelog note (FR-37).** It becomes the published History text of each catalogue
write, so it is validated against the catalogue's rules, which are stricter than a transition's,
before the lock is taken. A reason that passes `clean_transition_reason` but not
`validate_changelog_note` is refused with nothing written.

**One outer savepoint holds the catalogue writes and the move.** The catalogue writers each flush,
and `perform_move` opens its own savepoint inside this one, so a stale `row_version` or any later
refusal rolls the entry back with it.

**`created_entry_id` is set inside the move's `write`,** after the state and before the audit diff.
A flush of the submission between the two would clear the attribute history the diff reads, and the
event would omit the link.

**A new test is created `active` with its synonyms, its code and its properties.** A preferred-term
collision is the catalogue's own refusal (FR-05). **An amendment re-reads the entry under the lock,**
because it may have changed since the proposal: an entry that is no longer active, a synonym the entry
has since gained, a code that is already its active code and a code bound after the proposal, which a
replacement would retire unseen, each refuse the whole approval, and none is applied in part. The
submission records no code of the entry's, so "bound after the proposal" is a binding created after
the submission was. A different active code is retired, the proposed one is bound, and the two are
linked (FR-08), in that order, which the unique index on active bindings fixes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.audit.diffing import ChangeKind
from nptc.audit.recording import record_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.auth.principal import Principal
from nptc.catalogue.bindings import create_binding, link_replacement, retire_binding
from nptc.catalogue.changelog import validate_changelog_note
from nptc.catalogue.designations import add_synonyms, find_active_designation
from nptc.catalogue.entries import create_entry, entry_child_write
from nptc.catalogue.property_values import PropertyValueInput, save_property_values
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.code_binding import SNOMED_CT_SYSTEM, CodeBinding, CodeBindingStatus
from nptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from nptc.db.models.submission_working_copy import SubmissionWorkingCopy
from nptc.registry.handlers import DatatypeRegistry
from nptc.submissions.errors import (
    AmendmentEntryNotActiveError,
    AmendmentNoLongerApplicableCause,
    AmendmentNoLongerApplicableError,
)
from nptc.submissions.transitions import (
    check_move,
    check_row_version,
    clean_transition_reason,
    load_submission_locked,
    perform_move,
)
from nptc.submissions.working_copy import WorkingContent, working_content_of

__all__ = ["Approval", "approve_submission"]


@dataclass(frozen=True)
class Approval:
    """The outcome: the submission as it now stands and the entry the approval created or amended."""

    submission: Submission
    entry: CatalogueEntry


def approve_submission(
    session: Session,
    ctx: AuditContext,
    principal: Principal,
    *,
    submission_id: uuid.UUID,
    reason: str,
    expected_row_version: int,
    registry: DatatypeRegistry,
) -> Approval:
    """Moves a `Ready for approval` submission to `Approved`, writes or amends the catalogue entry,
    writes the transition row and appends the audit events. Returns the outcome.

    Raises `TransitionReasonRequiredError` for a blank reason, `FreeTextRefusedError` for one with an
    invisible character, `nptc.catalogue.changelog.ChangelogNoteError` for one that is not a
    meaningful changelog note, `SubmissionNotFoundError` for an unknown submission,
    `IllegalSubmissionTransitionError` for a submission that is not `Ready for approval`,
    `PermissionDeniedError` for a caller without `submission.transition.approve`
    (`MfaRequiredError` when a role suppressed for want of MFA would grant it),
    `SubmissionVersionConflictError` for a stale `expected_row_version`, and, from the catalogue,
    `AmendmentEntryNotActiveError` and `AmendmentNoLongerApplicableError` for an amendment that no
    longer applies, `nptc.catalogue.collisions` refusals for a term that collides,
    `EntryVersionConflictError` for an entry that changed under the write, `PropertyValidationError`
    for a property value the registry refuses, and `nptc.catalogue.bindings` refusals for a code that
    is already bound elsewhere. A refusal writes nothing.
    """
    note = validate_changelog_note(clean_transition_reason(reason))
    acquire_append_lock(session)
    submission = load_submission_locked(session, submission_id)

    permitted = check_move(
        principal,
        submission,
        SubmissionState.APPROVED,
        required_from=SubmissionState.READY_FOR_APPROVAL,
    )
    check_row_version(submission, expected_row_version)

    copy = session.get(SubmissionWorkingCopy, submission.id, populate_existing=True)
    content = working_content_of(submission if copy is None else copy)
    is_new_test = submission.kind == SubmissionKind.NEW_TEST.value

    outer = session.begin_nested()
    try:
        if is_new_test:
            entry = _create_entry(session, ctx, content, note, registry)
        else:
            entry = _amend_entry(session, ctx, submission, content, note)

        def write() -> None:
            if is_new_test:
                submission.created_entry_id = entry.id
            record_change(
                session,
                ctx,
                action=permitted.action,
                instance=submission,
                kind=ChangeKind.UPDATED,
                reason=note,
            )

        moved = perform_move(
            session,
            principal,
            submission,
            to_state=SubmissionState.APPROVED,
            reason=note,
            expected_row_version=expected_row_version,
            write=write,
        )
    except BaseException:
        outer.rollback()
        raise
    outer.commit()
    return Approval(submission=moved, entry=entry)


def _create_entry(
    session: Session,
    ctx: AuditContext,
    content: WorkingContent,
    note: str,
    registry: DatatypeRegistry,
) -> CatalogueEntry:
    acquire_append_lock(session)
    entry = create_entry(
        session,
        ctx,
        preferred_term=content.preferred_term,
        reason=note,
        status=CatalogueEntryStatus.ACTIVE,
    )
    if content.synonyms:
        add_synonyms(session, ctx, entry=entry, terms=content.synonyms, reason=note)
    if content.snomed_code is not None and content.snomed_fsn is not None:
        create_binding(
            session, ctx, entry=entry, code=content.snomed_code, fsn=content.snomed_fsn, reason=note
        )
    for property_key, items in content.property_values.items():
        if not items:
            continue
        save_property_values(
            session,
            ctx,
            entry=entry,
            property_key=property_key,
            values=[
                PropertyValueInput(value=item["value"], justification=_justification(item))
                for item in items
            ],
            reason=note,
            registry=registry,
            expected_row_version=entry.row_version,
        )
    return entry


def _justification(item: dict[str, object]) -> str | None:
    justification = item.get("justification")
    return justification if isinstance(justification, str) else None


def _amend_entry(
    session: Session,
    ctx: AuditContext,
    submission: Submission,
    content: WorkingContent,
    note: str,
) -> CatalogueEntry:
    acquire_append_lock(session)
    entry = (
        None
        if submission.entry_id is None
        else session.get(CatalogueEntry, submission.entry_id, populate_existing=True)
    )
    if entry is None:
        raise ValueError("an amendment names an entry that does not exist")
    if entry.status != CatalogueEntryStatus.ACTIVE:
        raise AmendmentEntryNotActiveError(CatalogueEntryStatus(entry.status))

    for term in content.synonyms:
        if find_active_designation(session, entry_id=entry.id, term=term) is not None:
            raise AmendmentNoLongerApplicableError(AmendmentNoLongerApplicableCause.SYNONYM_EXISTS)
    current = _active_binding(session, entry.id)
    if current is not None and current.code == content.snomed_code:
        raise AmendmentNoLongerApplicableError(AmendmentNoLongerApplicableCause.CODE_ALREADY_ACTIVE)
    if (
        current is not None
        and content.snomed_code is not None
        and current.created_at > submission.created_at
    ):
        raise AmendmentNoLongerApplicableError(AmendmentNoLongerApplicableCause.CODE_CHANGED)

    with entry_child_write(session, entry, entry.row_version, reason=note):
        if content.synonyms:
            add_synonyms(session, ctx, entry=entry, terms=content.synonyms, reason=note)
        if content.snomed_code is not None and content.snomed_fsn is not None:
            if current is not None:
                retire_binding(session, ctx, binding=current, reason=note)
            successor = create_binding(
                session,
                ctx,
                entry=entry,
                code=content.snomed_code,
                fsn=content.snomed_fsn,
                reason=note,
            )
            if current is not None:
                link_replacement(session, ctx, superseded=current, successor=successor, reason=note)
    return entry


def _active_binding(session: Session, entry_id: uuid.UUID) -> CodeBinding | None:
    """The entry's one active SNOMED CT binding, which `ix_code_binding_one_active_per_entry`
    allows at most one of."""
    return session.execute(
        select(CodeBinding).where(
            CodeBinding.entry_id == entry_id,
            CodeBinding.system == SNOMED_CT_SYSTEM,
            CodeBinding.status == CodeBindingStatus.ACTIVE,
        )
    ).scalar_one_or_none()
