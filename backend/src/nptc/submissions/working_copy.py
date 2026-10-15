"""Editing a submission's working copy (FR-23, NFR-08).

A reviewer edits a copy of what the submitter sent. The submission's own content never changes
(`nptc.db.models.submission`), so the edits live in `submission_working_copy`, one row per submission,
created by the first effective edit. A submission with no row has not been edited, and its working
content is its original.

**One lock.** The submission's `row_version` is the only lock. Every effective edit bumps it, so an
edit and a change of state carry the same `expected_row_version` and cannot cross. The submitter can
see `row_version` rise, which is accepted: one lock was the requirement.

**The request is partial.** A field left out stays as it is. `synonyms` and `property_values` replace
the whole field. A blank `notes` clears the note. Nothing else can clear a field: a code is replaced,
never removed.

**An amendment proposes only `synonyms` and a code (FR-35).** Its `preferred_term` is a copy of the
entry's term, kept as context, so an edit of the term, the notes or the property values is refused,
and so is an edit that would leave the amendment with neither a synonym nor a code.

**Everything that can refuse runs before the lock where it can.** The reason and free text are
cleaned, the property values are checked against the registry, and the code is resolved through the
terminology server. Only then does the function take the audit append lock, so a refused request
takes no lock and no request holds the lock across a call to the terminology server. The lock is one
global advisory lock, so holding it across a slow server would stall every audit write.
`test_lock_ordering.py` therefore names this function as exempt from its lock-first rule, and
`test_submissions_working_copy.py` pins the ordering instead. Whatever depends on the committed row
(the state, the version and the content the edit is measured against) is read after the lock, with
`populate_existing`.

**An edit that changes nothing writes nothing.** No row is created, `row_version` is not bumped and
no audit event is appended. It still takes the append lock, because the state, the version and the
content it is measured against are read under it, so a stale caller learns that it is stale.

**No duplicate check.** A reviewer may give a term the catalogue already holds. Duplicates are
checked when a submission is created, and a working copy has no confirmation column to record one.

**One audit event, about the submission.** An effective edit appends one `UPDATED` event with
`entity_type` `submission`, whose `before` and `after` hold only the content fields that changed. The
first edit is `UPDATED` too: its `before` is the original.

**The FSN is the server's, never the caller's (FR-82).** A code and its label change together.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from nptc.audit.diffing import ChangeKind
from nptc.audit.policy import policy_for
from nptc.audit.recording import record_snapshot_change
from nptc.audit.writer import AuditContext, acquire_append_lock
from nptc.catalogue.property_values import PropertyValueInput
from nptc.db.models.submission import WORKING_STATES, Submission, SubmissionKind
from nptc.db.models.submission_working_copy import SubmissionWorkingCopy
from nptc.registry.handlers import DatatypeRegistry
from nptc.submissions.errors import (
    AmendmentRefusal,
    AmendmentRefusedError,
    EditReasonRequiredError,
    FreeTextField,
    SubmissionNotEditableError,
    SubmissionNotFoundError,
    SubmissionVersionConflictError,
)
from nptc.submissions.shared import (
    check_submission_property_values,
    clean_free_text,
    resolve_code,
)
from nptc.submissions.terms import TermField, clean_submission_term, distinct_synonyms
from nptc_shared.terminology import TerminologyClient

__all__ = ["WorkingContent", "WorkingCopyChanges", "WorkingCopyEdit", "edit_working_copy"]

#: The content fields a working copy holds, in the order the audit diff names them.
_CONTENT_FIELDS: Final = (
    "preferred_term",
    "synonyms",
    "snomed_code",
    "snomed_fsn",
    "property_values",
    "notes",
)


@dataclass(frozen=True)
class WorkingCopyChanges:
    """What a reviewer sends. `None` means "leave as it is", so there is no way to send a null.
    A blank `notes` clears the note. There is no FSN field: it is served by the terminology server,
    never accepted (FR-82)."""

    preferred_term: str | None = None
    synonyms: Sequence[str] | None = None
    snomed_code: str | None = None
    property_values: Mapping[str, Sequence[PropertyValueInput]] | None = None
    notes: str | None = None


@dataclass(frozen=True)
class WorkingContent:
    """The content a reviewer sees as the working version of a submission."""

    preferred_term: str
    synonyms: list[str]
    snomed_code: str | None
    snomed_fsn: str | None
    property_values: dict[str, list[dict[str, object]]]
    notes: str | None


@dataclass(frozen=True)
class WorkingCopyEdit:
    """The outcome of an edit. `changed` is false when the edit left the content as it was, and
    then nothing was written. `submission` carries the `row_version` to send next."""

    submission: Submission
    content: WorkingContent
    changed: bool


@dataclass(frozen=True)
class _Prepared:
    """The parts of the request that need no read of the committed row: cleaned, checked and, for
    a code, resolved."""

    preferred_term: str | None
    synonyms: list[str] | None
    notes_given: bool
    notes: str | None
    snomed: tuple[str, str] | None
    property_values: dict[str, list[dict[str, object]]] | None


def edit_working_copy(
    session: Session,
    ctx: AuditContext,
    *,
    submission_id: uuid.UUID,
    changes: WorkingCopyChanges,
    reason: str,
    expected_row_version: int,
    registry: DatatypeRegistry,
    terminology_client: TerminologyClient,
) -> WorkingCopyEdit:
    """Applies `changes` to the working copy of the submission, creating it on the first effective
    edit, bumps the submission's `row_version` and appends the audit event. Returns the outcome.

    Raises `EditReasonRequiredError` for a blank reason, `SubmissionNotFoundError` for an unknown
    submission, `SubmissionNotEditableError` for a state outside `WORKING_STATES`,
    `AmendmentRefusedError` for an amendment edit that names a field an amendment does not propose,
    `nptc.catalogue.term_hygiene.TermCleaningError` for a term that is empty or carries an
    invisible character, `FreeTextRefusedError` for the same in the reason or notes,
    `PropertyValidationError` carrying every property problem at once, `nptc.terminology.errors`
    and `nptc_shared.sctid.InvalidSCTIDError` for a code that is malformed or cannot be looked up,
    `SubmissionCodeRefusedError` for a code the server knows but rules out, and
    `SubmissionVersionConflictError` for a stale `expected_row_version`. A refusal writes nothing.
    Blocks on the terminology server when a code or a coded property value is given.
    """
    if ctx.actor_user_id is None:
        raise ValueError("an edit belongs to a person, but the audit context has none")

    cleaned_reason = clean_free_text(reason, field=FreeTextField.REASON, multiline=True)
    if cleaned_reason is None:
        raise EditReasonRequiredError("an edit of a working copy needs a reason")

    submission = _submission(session, submission_id, populate_existing=False)
    _require_editable(submission)
    kind = SubmissionKind(submission.kind)
    if kind is SubmissionKind.AMENDMENT and (
        changes.preferred_term is not None
        or changes.property_values is not None
        or changes.notes is not None
    ):
        raise AmendmentRefusedError(AmendmentRefusal.FIELD_NOT_AMENDABLE)
    prepared = _prepare(session, changes, registry, terminology_client)

    acquire_append_lock(session)
    submission = _submission(session, submission_id, populate_existing=True)
    _require_editable(submission)
    if submission.row_version != expected_row_version:
        raise SubmissionVersionConflictError(
            expected_row_version=expected_row_version, current_row_version=submission.row_version
        )
    copy = session.get(SubmissionWorkingCopy, submission.id, populate_existing=True)
    base = _content_of(submission if copy is None else copy)
    new = _merge(base, prepared)
    if kind is SubmissionKind.AMENDMENT and not new.synonyms and new.snomed_code is None:
        raise AmendmentRefusedError(AmendmentRefusal.NOTHING_PROPOSED)

    changed = [name for name in _CONTENT_FIELDS if getattr(new, name) != getattr(base, name)]
    if not changed:
        return WorkingCopyEdit(submission=submission, content=base, changed=False)

    savepoint = session.begin_nested()
    try:
        if copy is None:
            session.add(
                SubmissionWorkingCopy(
                    submission_id=submission.id,
                    **{name: getattr(new, name) for name in _CONTENT_FIELDS},
                )
            )
        else:
            for name in changed:
                setattr(copy, name, getattr(new, name))
        # The manual bump marks the submission dirty, so the flush issues the UPDATE that
        # `version_id_col` guards: a writer that skipped the lock still fails on a stale version.
        submission.row_version += 1
        session.flush()
        record_snapshot_change(
            session,
            ctx,
            action="submission.working_copy_edited",
            entity_type="submission",
            entity_id=str(submission.id),
            policy=policy_for(Submission),
            before={name: getattr(base, name) for name in changed},
            after={name: getattr(new, name) for name in changed},
            kind=ChangeKind.UPDATED,
            reason=cleaned_reason,
        )
    except StaleDataError, ObjectDeletedError:
        savepoint.rollback()
        session.expire(submission)
        current = _submission(session, submission_id, populate_existing=True)
        raise SubmissionVersionConflictError(
            expected_row_version=expected_row_version, current_row_version=current.row_version
        ) from None
    except BaseException:
        savepoint.rollback()
        raise
    savepoint.commit()
    return WorkingCopyEdit(submission=submission, content=new, changed=True)


def _submission(
    session: Session, submission_id: uuid.UUID, *, populate_existing: bool
) -> Submission:
    # `populate_existing` after the lock: the row read then is the committed one, and a copy already
    # in the session's identity map would otherwise be returned without a query.
    submission = session.get(Submission, submission_id, populate_existing=populate_existing)
    if submission is None:
        raise SubmissionNotFoundError("no submission has that identifier")
    return submission


def _require_editable(submission: Submission) -> None:
    if submission.state not in WORKING_STATES:
        raise SubmissionNotEditableError(submission.state)


def _prepare(
    session: Session,
    changes: WorkingCopyChanges,
    registry: DatatypeRegistry,
    terminology_client: TerminologyClient,
) -> _Prepared:
    preferred_term = (
        None
        if changes.preferred_term is None
        else clean_submission_term(changes.preferred_term, TermField.PREFERRED_TERM)
    )
    synonyms = (
        None
        if changes.synonyms is None
        else [clean_submission_term(term, TermField.SYNONYMS) for term in changes.synonyms]
    )
    notes = (
        clean_free_text(changes.notes, field=FreeTextField.NOTES, multiline=True)
        if changes.notes is not None
        else None
    )

    property_values: dict[str, list[dict[str, object]]] | None = None
    if changes.property_values is not None:
        given = {key: items for key, items in changes.property_values.items() if items}
        check_submission_property_values(session, registry, given)
        property_values = {
            key: [{"value": item.value, "justification": item.justification} for item in items]
            for key, items in given.items()
        }

    snomed = (
        None
        if changes.snomed_code is None
        else resolve_code(terminology_client, changes.snomed_code)
    )
    return _Prepared(
        preferred_term=preferred_term,
        synonyms=synonyms,
        notes_given=changes.notes is not None,
        notes=notes,
        snomed=snomed,
        property_values=property_values,
    )


def _content_of(source: Submission | SubmissionWorkingCopy) -> WorkingContent:
    return WorkingContent(
        preferred_term=source.preferred_term,
        synonyms=list(source.synonyms),
        snomed_code=source.snomed_code,
        snomed_fsn=source.snomed_fsn,
        property_values=dict(source.property_values),
        notes=source.notes,
    )


def _merge(base: WorkingContent, prepared: _Prepared) -> WorkingContent:
    """`base` with the request applied. Synonyms are re-checked against the preferred term when
    either changes, so a term that becomes the same as a synonym drops that synonym."""
    preferred_term = (
        base.preferred_term if prepared.preferred_term is None else prepared.preferred_term
    )
    synonyms = base.synonyms
    if prepared.synonyms is not None or preferred_term != base.preferred_term:
        synonyms = distinct_synonyms(
            preferred_term, base.synonyms if prepared.synonyms is None else prepared.synonyms
        )
    snomed_code, snomed_fsn = (base.snomed_code, base.snomed_fsn)
    if prepared.snomed is not None:
        snomed_code, snomed_fsn = prepared.snomed
    return WorkingContent(
        preferred_term=preferred_term,
        synonyms=synonyms,
        snomed_code=snomed_code,
        snomed_fsn=snomed_fsn,
        property_values=(
            base.property_values if prepared.property_values is None else prepared.property_values
        ),
        notes=prepared.notes if prepared.notes_given else base.notes,
    )
