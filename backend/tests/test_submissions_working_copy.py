"""`nptc.submissions.working_copy.edit_working_copy` service-layer tests (FR-23, FR-06, FR-82, NFR-08).

Each refusal asserts that no working copy, no `row_version` bump and no audit event followed, because
a refused request must leave nothing behind. Audit events are read for the submission this test made,
and `backend/tests` shares one Postgres container (see `CLAUDE.md`), so nothing counts a whole table.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.catalogue.local_codes import DatabaseLocalCodeLookup
from nptc.catalogue.property_values import PropertyValidationError, PropertyValueInput
from nptc.catalogue.term_hygiene import TermCleaningError
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.property_definition import PropertyDefinition, PropertyOrigin, PropertyScope
from nptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from nptc.db.models.submission_working_copy import SubmissionWorkingCopy
from nptc.db.models.user import User
from nptc.registry.datatypes import build_builtin_handlers
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps
from nptc.submissions.errors import (
    AmendmentRefusal,
    AmendmentRefusedError,
    CodeRefusal,
    EditReasonRequiredError,
    FreeTextRefusedError,
    SubmissionCodeRefusedError,
    SubmissionNotEditableError,
    SubmissionNotFoundError,
    SubmissionVersionConflictError,
)
from nptc.submissions.terms import SubmissionCodeMalformedError
from nptc.submissions.working_copy import WorkingCopyChanges, WorkingCopyEdit, edit_working_copy
from nptc_shared.terminology import (
    AU_LANGUAGE_TAG,
    Operation,
    StubConcept,
    StubTerminologyClient,
    TerminologyStatusError,
)

_CODE = "391483001"
_FSN = "Microscopy (acid fast bacilli) (procedure)"
_INACTIVE_CODE = "119364003"
_REASON = "Corrected against the lab's own sheet."
_ORIGINAL_NOTES = "Seen on the new analyser panel"
ZERO_WIDTH_SPACE = chr(0x200B)


def _client() -> StubTerminologyClient:
    client = StubTerminologyClient()
    client.add_concept(
        StubConcept(
            code=_CODE,
            fsn=_FSN,
            preferred_terms={AU_LANGUAGE_TAG: "Acid fast bacilli microscopy"},
        )
    )
    client.add_concept(
        StubConcept(
            code=_INACTIVE_CODE,
            fsn="Serum specimen (specimen)",
            preferred_terms={AU_LANGUAGE_TAG: "Serum specimen"},
            active=False,
        )
    )
    return client


def _registry(session: Session, client: StubTerminologyClient) -> DatatypeRegistry:
    return DatatypeRegistry(
        build_builtin_handlers(
            HandlerDeps(
                terminology_client=client, local_code_lookup=DatabaseLocalCodeLookup(session)
            )
        )
    )


def _user(session: Session) -> User:
    user = User(username=f"user-{uuid.uuid4()}", display_name="Working copy tester")
    session.add(user)
    session.flush()
    return user


def _ctx(user: User) -> AuditContext:
    return AuditContext(
        actor_user_id=user.id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )


def _new_test(
    session: Session,
    *,
    state: SubmissionState = SubmissionState.TRIAGE,
    synonyms: tuple[str, ...] = ("Sodium, serum",),
    notes: str | None = _ORIGINAL_NOTES,
) -> Submission:
    submission = Submission(
        kind=SubmissionKind.NEW_TEST.value,
        state=state.value,
        preferred_term="Serum sodium",
        synonyms=list(synonyms),
        property_values={},
        notes=notes,
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=_user(session).id,
    )
    session.add(submission)
    session.flush()
    return submission


def _amendment(session: Session) -> Submission:
    entry = CatalogueEntry(
        business_key=f"NPTC-{random.randrange(100_000_000, 999_999_999)}",
        preferred_term="Serum sodium",
        status=CatalogueEntryStatus.ACTIVE.value,
    )
    session.add(entry)
    session.flush()
    submission = Submission(
        kind=SubmissionKind.AMENDMENT.value,
        state=SubmissionState.TRIAGE.value,
        entry_id=entry.id,
        preferred_term="Serum sodium",
        synonyms=["Sodium, serum"],
        property_values={},
        submitter_id=_user(session).id,
    )
    session.add(submission)
    session.flush()
    return submission


def _property(session: Session, key: str, *, required: bool = False) -> None:
    session.add(
        PropertyDefinition(
            key=key,
            label=key.replace("_", " ").title(),
            datatype="string",
            cardinality="0..1",
            scope=PropertyScope.SUBMISSION,
            required_for_submission=required,
            required_for_publication=False,
            filterable=False,
            origin=PropertyOrigin.ADMIN,
            display_order=0,
            constraints={},
        )
    )
    session.flush()


def _edit(
    session: Session,
    submission: Submission,
    changes: WorkingCopyChanges,
    *,
    ctx: AuditContext | None = None,
    reason: str = _REASON,
    expected_row_version: int | None = None,
    client: StubTerminologyClient | None = None,
) -> WorkingCopyEdit:
    terminology = client if client is not None else _client()
    return edit_working_copy(
        session,
        ctx if ctx is not None else _ctx(_user(session)),
        submission_id=submission.id,
        changes=changes,
        reason=reason,
        expected_row_version=(
            submission.row_version if expected_row_version is None else expected_row_version
        ),
        registry=_registry(session, terminology),
        terminology_client=terminology,
    )


def _copy(session: Session, submission: Submission) -> SubmissionWorkingCopy | None:
    return session.get(SubmissionWorkingCopy, submission.id, populate_existing=True)


def _events(session: Session, submission: Submission) -> list[AuditEvent]:
    return list(
        session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == "submission", AuditEvent.entity_id == str(submission.id)
            )
            .order_by(AuditEvent.sequence)
        ).scalars()
    )


def _fresh_version(session: Session, submission: Submission) -> int:
    session.refresh(submission)
    return submission.row_version


def _assert_nothing_written(session: Session, submission: Submission, version: int) -> None:
    assert _copy(session, submission) is None
    assert _fresh_version(session, submission) == version
    assert _events(session, submission) == []


# --- the edit itself ---------------------------------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_the_first_edit_creates_the_working_copy_and_leaves_the_original(
    app_session: Session,
) -> None:
    _property(app_session, "wc_note")
    submission = _new_test(app_session)
    version = submission.row_version

    result = _edit(
        app_session,
        submission,
        WorkingCopyChanges(
            preferred_term="  Sodium, serum level  ",
            synonyms=["Na (serum)", "Sodium, serum"],
            snomed_code=_CODE,
            property_values={"wc_note": [PropertyValueInput(value="checked")]},
            notes="Confirmed with the lab",
        ),
    )

    assert result.changed is True
    copy = _copy(app_session, submission)
    assert copy is not None
    assert copy.preferred_term == "Sodium, serum level"
    assert copy.synonyms == ["Na (serum)", "Sodium, serum"]
    assert (copy.snomed_code, copy.snomed_fsn) == (_CODE, _FSN)
    assert copy.property_values == {"wc_note": [{"value": "checked", "justification": None}]}
    assert copy.notes == "Confirmed with the lab"

    app_session.refresh(submission)
    assert submission.preferred_term == "Serum sodium"
    assert submission.synonyms == ["Sodium, serum"]
    assert submission.snomed_code is None
    assert submission.snomed_fsn is None
    assert submission.property_values == {}
    assert submission.notes == "Seen on the new analyser panel"
    assert submission.row_version == version + 1
    assert result.content.preferred_term == "Sodium, serum level"


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_an_effective_edit_appends_one_event_naming_only_the_changed_fields(
    app_session: Session,
) -> None:
    submission = _new_test(app_session)
    ctx = _ctx(_user(app_session))

    _edit(
        app_session,
        submission,
        WorkingCopyChanges(preferred_term="Sodium, serum level", snomed_code=_CODE),
        ctx=ctx,
    )

    (event,) = _events(app_session, submission)
    assert event.action == "submission.working_copy_edited"
    assert event.actor_user_id == ctx.actor_user_id
    assert event.reason == _REASON
    assert event.before == {
        "preferred_term": "Serum sodium",
        "snomed_code": None,
        "snomed_fsn": None,
    }
    assert event.after == {
        "preferred_term": "Sodium, serum level",
        "snomed_code": _CODE,
        "snomed_fsn": _FSN,
    }


@pytest.mark.req("FR-23")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_second_edit_updates_the_same_row_and_records_the_previous_working_value(
    app_session: Session,
) -> None:
    submission = _new_test(app_session)
    _edit(app_session, submission, WorkingCopyChanges(preferred_term="First wording"))
    version = _fresh_version(app_session, submission)

    _edit(app_session, submission, WorkingCopyChanges(preferred_term="Second wording"))

    copy = _copy(app_session, submission)
    assert copy is not None
    assert copy.preferred_term == "Second wording"
    assert _fresh_version(app_session, submission) == version + 1
    first, second = _events(app_session, submission)
    assert first.before == {"preferred_term": "Serum sodium"}
    assert second.before == {"preferred_term": "First wording"}
    assert second.after == {"preferred_term": "Second wording"}


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_field_left_out_keeps_its_working_value(app_session: Session) -> None:
    submission = _new_test(app_session)
    _edit(
        app_session,
        submission,
        WorkingCopyChanges(preferred_term="Sodium, serum level", synonyms=["Na (serum)"]),
    )

    _edit(app_session, submission, WorkingCopyChanges(notes="Only the note changed"))

    copy = _copy(app_session, submission)
    assert copy is not None
    assert copy.preferred_term == "Sodium, serum level"
    assert copy.synonyms == ["Na (serum)"]
    assert copy.notes == "Only the note changed"


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_list_replaces_the_whole_field(app_session: Session) -> None:
    submission = _new_test(app_session, synonyms=("One", "Two"))

    _edit(app_session, submission, WorkingCopyChanges(synonyms=["Three"]))

    copy = _copy(app_session, submission)
    assert copy is not None
    assert copy.synonyms == ["Three"]


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_blank_note_clears_the_note(app_session: Session) -> None:
    submission = _new_test(app_session)

    _edit(app_session, submission, WorkingCopyChanges(notes="   "))

    copy = _copy(app_session, submission)
    assert copy is not None
    assert copy.notes is None


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_preferred_term_that_matches_a_synonym_drops_that_synonym(app_session: Session) -> None:
    submission = _new_test(app_session, synonyms=("Sodium, serum", "Na (serum)"))

    _edit(app_session, submission, WorkingCopyChanges(preferred_term="Sodium, serum"))

    copy = _copy(app_session, submission)
    assert copy is not None
    assert copy.synonyms == ["Na (serum)"]


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize(
    "state",
    [SubmissionState.TRIAGE, SubmissionState.UNDER_REVIEW, SubmissionState.READY_FOR_APPROVAL],
)
def test_an_edit_is_accepted_in_every_working_state(
    app_session: Session, state: SubmissionState
) -> None:
    submission = _new_test(app_session, state=state)

    result = _edit(app_session, submission, WorkingCopyChanges(notes="Edited"))

    assert result.changed is True


# --- an edit that changes nothing --------------------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_an_edit_that_changes_nothing_writes_nothing(app_session: Session) -> None:
    submission = _new_test(app_session)
    version = submission.row_version

    result = _edit(
        app_session,
        submission,
        WorkingCopyChanges(
            preferred_term="Serum sodium",
            synonyms=["Sodium, serum"],
            notes="Seen on the new analyser panel",
        ),
    )

    assert result.changed is False
    assert result.content.preferred_term == "Serum sodium"
    _assert_nothing_written(app_session, submission, version)


@pytest.mark.req("FR-23")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_repeating_an_edit_changes_nothing_the_second_time(app_session: Session) -> None:
    submission = _new_test(app_session)
    changes = WorkingCopyChanges(preferred_term="Sodium, serum level", snomed_code=_CODE)
    _edit(app_session, submission, changes)
    version = _fresh_version(app_session, submission)

    result = _edit(app_session, submission, changes)

    assert result.changed is False
    assert _fresh_version(app_session, submission) == version
    assert len(_events(app_session, submission)) == 1


# --- state and version -------------------------------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize(
    "state",
    [
        SubmissionState.SUBMITTED,
        SubmissionState.APPROVED,
        SubmissionState.PUBLISHED_IN_RELEASE,
        SubmissionState.REJECTED,
        SubmissionState.WITHDRAWN,
    ],
)
def test_an_edit_outside_the_working_states_is_refused(
    app_session: Session, state: SubmissionState
) -> None:
    submission = _new_test(app_session, state=state)
    version = submission.row_version

    with pytest.raises(SubmissionNotEditableError) as excinfo:
        _edit(app_session, submission, WorkingCopyChanges(notes="Edited"))

    assert excinfo.value.state == state.value
    _assert_nothing_written(app_session, submission, version)


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_stale_row_version_is_refused_with_the_current_one(app_session: Session) -> None:
    submission = _new_test(app_session)
    version = submission.row_version

    with pytest.raises(SubmissionVersionConflictError) as excinfo:
        _edit(
            app_session,
            submission,
            WorkingCopyChanges(notes="Edited"),
            expected_row_version=version + 5,
        )

    assert excinfo.value.expected_row_version == version + 5
    assert excinfo.value.current_row_version == version
    _assert_nothing_written(app_session, submission, version)


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_stale_row_version_is_refused_even_when_the_edit_would_change_nothing(
    app_session: Session,
) -> None:
    submission = _new_test(app_session)
    version = submission.row_version

    with pytest.raises(SubmissionVersionConflictError):
        _edit(
            app_session,
            submission,
            WorkingCopyChanges(notes="Seen on the new analyser panel"),
            expected_row_version=version + 1,
        )


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_an_edit_makes_the_old_row_version_stale_for_the_next_writer(
    app_session: Session,
) -> None:
    submission = _new_test(app_session)
    old_version = submission.row_version
    _edit(app_session, submission, WorkingCopyChanges(notes="Edited"))

    with pytest.raises(SubmissionVersionConflictError):
        _edit(
            app_session,
            submission,
            WorkingCopyChanges(notes="Edited again"),
            expected_row_version=old_version,
        )


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_an_unknown_submission_is_refused(app_session: Session) -> None:
    with pytest.raises(SubmissionNotFoundError):
        edit_working_copy(
            app_session,
            _ctx(_user(app_session)),
            submission_id=uuid.uuid4(),
            changes=WorkingCopyChanges(notes="Edited"),
            reason=_REASON,
            expected_row_version=1,
            registry=_registry(app_session, _client()),
            terminology_client=_client(),
        )


# --- the reason and the free text --------------------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize("reason", ["", "   ", "\n\t"], ids=["empty", "spaces", "whitespace"])
def test_an_edit_needs_a_reason(app_session: Session, reason: str) -> None:
    submission = _new_test(app_session)
    version = submission.row_version

    with pytest.raises(EditReasonRequiredError):
        _edit(app_session, submission, WorkingCopyChanges(notes="Edited"), reason=reason)

    _assert_nothing_written(app_session, submission, version)


@pytest.mark.req("FR-63")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (WorkingCopyChanges(notes="Edited"), "Why" + ZERO_WIDTH_SPACE),
        (WorkingCopyChanges(notes="No" + ZERO_WIDTH_SPACE + "te"), _REASON),
    ],
    ids=["reason", "notes"],
)
def test_an_invisible_character_in_free_text_is_refused(
    app_session: Session, changes: WorkingCopyChanges, reason: str
) -> None:
    submission = _new_test(app_session)
    version = submission.row_version

    with pytest.raises(FreeTextRefusedError):
        _edit(app_session, submission, changes, reason=reason)

    _assert_nothing_written(app_session, submission, version)


@pytest.mark.req("FR-63")
@pytest.mark.integration
@pytest.mark.parametrize(
    "changes",
    [
        WorkingCopyChanges(preferred_term="   "),
        WorkingCopyChanges(preferred_term="Se" + ZERO_WIDTH_SPACE + "rum"),
        WorkingCopyChanges(synonyms=["   "]),
    ],
    ids=["blank_term", "invisible_term", "blank_synonym"],
)
def test_a_term_that_cannot_be_cleaned_is_refused(
    app_session: Session, changes: WorkingCopyChanges
) -> None:
    submission = _new_test(app_session)
    version = submission.row_version

    with pytest.raises(TermCleaningError):
        _edit(app_session, submission, changes)

    _assert_nothing_written(app_session, submission, version)


# --- the code ----------------------------------------------------------------------------------


@pytest.mark.req("FR-06")
@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_the_fsn_is_the_one_the_server_returned(app_session: Session) -> None:
    submission = _new_test(app_session)

    _edit(app_session, submission, WorkingCopyChanges(snomed_code=_CODE))

    copy = _copy(app_session, submission)
    assert copy is not None
    assert (copy.snomed_code, copy.snomed_fsn) == (_CODE, _FSN)
    assert isinstance(copy.snomed_code, str)


@pytest.mark.req("FR-06")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("code", "error"),
    [
        (_INACTIVE_CODE, SubmissionCodeRefusedError),
        ("123", SubmissionCodeMalformedError),
        ("not-a-code", SubmissionCodeMalformedError),
    ],
    ids=["inactive", "bad_check_digit", "not_digits"],
)
def test_a_code_the_server_rules_out_is_refused_and_writes_nothing(
    app_session: Session, code: str, error: type[Exception]
) -> None:
    submission = _new_test(app_session)
    version = submission.row_version

    with pytest.raises(error):
        _edit(app_session, submission, WorkingCopyChanges(snomed_code=code))

    _assert_nothing_written(app_session, submission, version)


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_a_code_the_server_does_not_know_is_refused_and_writes_nothing(
    app_session: Session,
) -> None:
    submission = _new_test(app_session)
    version = submission.row_version
    client = StubTerminologyClient()
    client.seed_error(Operation.LOOKUP, TerminologyStatusError("not found", status_code=404))

    with pytest.raises(SubmissionCodeRefusedError) as excinfo:
        _edit(app_session, submission, WorkingCopyChanges(snomed_code="26604007"), client=client)

    assert excinfo.value.reason is CodeRefusal.NOT_FOUND
    _assert_nothing_written(app_session, submission, version)


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_an_inactive_code_is_named_as_inactive(app_session: Session) -> None:
    submission = _new_test(app_session)

    with pytest.raises(SubmissionCodeRefusedError) as excinfo:
        _edit(app_session, submission, WorkingCopyChanges(snomed_code=_INACTIVE_CODE))

    assert excinfo.value.reason is CodeRefusal.INACTIVE


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_code_cannot_be_removed(app_session: Session) -> None:
    submission = _new_test(app_session)
    _edit(app_session, submission, WorkingCopyChanges(snomed_code=_CODE))

    _edit(app_session, submission, WorkingCopyChanges(notes="Edited"))

    copy = _copy(app_session, submission)
    assert copy is not None
    assert copy.snomed_code == _CODE


# --- property values ---------------------------------------------------------------------------


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_an_unknown_property_is_refused_and_writes_nothing(app_session: Session) -> None:
    submission = _new_test(app_session)
    version = submission.row_version

    with pytest.raises(PropertyValidationError) as excinfo:
        _edit(
            app_session,
            submission,
            WorkingCopyChanges(
                property_values={"no_such_property": [PropertyValueInput(value="x")]}
            ),
        )

    assert ("no_such_property", "unknown-property") in {
        (issue.property_key, issue.code) for issue in excinfo.value.issues
    }
    _assert_nothing_written(app_session, submission, version)


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_property_set_that_leaves_out_a_required_property_is_refused(
    app_session: Session,
) -> None:
    _property(app_session, "wc_required", required=True)
    _property(app_session, "wc_other")
    submission = _new_test(app_session)
    version = submission.row_version

    with pytest.raises(PropertyValidationError) as excinfo:
        _edit(
            app_session,
            submission,
            WorkingCopyChanges(property_values={"wc_other": [PropertyValueInput(value="x")]}),
        )

    assert ("wc_required", "required-property-missing") in {
        (issue.property_key, issue.code) for issue in excinfo.value.issues
    }
    _assert_nothing_written(app_session, submission, version)


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_a_property_set_that_carries_every_required_property_is_accepted(
    app_session: Session,
) -> None:
    _property(app_session, "wc_required", required=True)
    submission = _new_test(app_session)

    _edit(
        app_session,
        submission,
        WorkingCopyChanges(property_values={"wc_required": [PropertyValueInput(value="given")]}),
    )

    copy = _copy(app_session, submission)
    assert copy is not None
    assert copy.property_values == {"wc_required": [{"value": "given", "justification": None}]}


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_an_edit_that_leaves_the_properties_alone_is_not_blocked_by_a_new_required_one(
    app_session: Session,
) -> None:
    submission = _new_test(app_session)
    _property(app_session, "wc_added_later", required=True)

    result = _edit(app_session, submission, WorkingCopyChanges(notes="Edited"))

    assert result.changed is True


# --- an amendment ------------------------------------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_working_copy_changes_the_synonyms_and_the_code(
    app_session: Session,
) -> None:
    submission = _amendment(app_session)

    _edit(
        app_session,
        submission,
        WorkingCopyChanges(synonyms=["Na (serum)"], snomed_code=_CODE),
    )

    copy = _copy(app_session, submission)
    assert copy is not None
    assert copy.preferred_term == "Serum sodium"
    assert copy.synonyms == ["Na (serum)"]
    assert (copy.snomed_code, copy.snomed_fsn) == (_CODE, _FSN)


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-35")
@pytest.mark.integration
@pytest.mark.parametrize(
    "changes",
    [
        WorkingCopyChanges(preferred_term="Renamed"),
        WorkingCopyChanges(notes="A note"),
        WorkingCopyChanges(property_values={}),
    ],
    ids=["preferred_term", "notes", "property_values"],
)
def test_an_amendment_refuses_a_field_it_does_not_propose(
    app_session: Session, changes: WorkingCopyChanges
) -> None:
    submission = _amendment(app_session)
    version = submission.row_version

    with pytest.raises(AmendmentRefusedError) as excinfo:
        _edit(app_session, submission, changes)

    assert excinfo.value.reason is AmendmentRefusal.FIELD_NOT_AMENDABLE
    _assert_nothing_written(app_session, submission, version)


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_cannot_be_left_with_no_synonym_and_no_code(app_session: Session) -> None:
    submission = _amendment(app_session)
    version = submission.row_version

    with pytest.raises(AmendmentRefusedError) as excinfo:
        _edit(app_session, submission, WorkingCopyChanges(synonyms=[]))

    assert excinfo.value.reason is AmendmentRefusal.NOTHING_PROPOSED
    _assert_nothing_written(app_session, submission, version)


# --- the audit context -------------------------------------------------------------------------


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_an_edit_needs_a_human_actor(app_session: Session) -> None:
    submission = _new_test(app_session)
    ctx = AuditContext(
        actor_user_id=None, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )

    with pytest.raises(ValueError, match="person"):
        _edit(app_session, submission, WorkingCopyChanges(notes="Edited"), ctx=ctx)


# --- the append lock is never held across the terminology server -------------------------------


class _CallRecordingClient(StubTerminologyClient):
    """Records, for every terminology call, whether the append lock was already held."""

    def __init__(self, lock_held: Callable[[], bool], calls: list[bool]) -> None:
        super().__init__()
        self._lock_held = lock_held
        self._calls = calls

    def lookup(self, *args: Any, **kwargs: Any) -> Any:
        self._calls.append(self._lock_held())
        return super().lookup(*args, **kwargs)


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_no_terminology_call_is_made_while_the_append_lock_is_held(
    app_session: Session, capture_statements: Any
) -> None:
    """The lock is one global advisory lock, so a call to the terminology server made while holding
    it would stall every audit write. The lock-ordering guard exempts this writer because it must
    not take the lock first, so this test is what pins the order."""
    submission = _new_test(app_session)
    calls: list[bool] = []

    def is_lock(statement: str, _parameters: object) -> bool:
        return "pg_advisory_xact_lock" in statement

    with capture_statements(app_session.get_bind(), keep=is_lock) as locks:
        client = _CallRecordingClient(lambda: bool(locks), calls)
        client.add_concept(
            StubConcept(
                code=_CODE,
                fsn=_FSN,
                preferred_terms={AU_LANGUAGE_TAG: "Acid fast bacilli microscopy"},
            )
        )
        _edit(app_session, submission, WorkingCopyChanges(snomed_code=_CODE), client=client)

    assert calls == [False]
    # The probe does see the lock the write takes, so the assertion above cannot pass vacuously.
    assert locks


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_refused_edit_takes_no_append_lock(app_session: Session, capture_statements: Any) -> None:
    submission = _new_test(app_session)

    def is_lock(statement: str, _parameters: object) -> bool:
        return "pg_advisory_xact_lock" in statement

    with (
        capture_statements(app_session.get_bind(), keep=is_lock) as locks,
        pytest.raises(SubmissionCodeRefusedError),
    ):
        _edit(app_session, submission, WorkingCopyChanges(snomed_code=_INACTIVE_CODE))

    assert locks == []


@pytest.mark.req("FR-63")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize(
    "changes",
    [
        WorkingCopyChanges(synonyms=["   "]),
        WorkingCopyChanges(synonyms=["Na" + ZERO_WIDTH_SPACE]),
        WorkingCopyChanges(preferred_term="Se" + ZERO_WIDTH_SPACE + "rum"),
        WorkingCopyChanges(notes="No" + ZERO_WIDTH_SPACE + "te"),
    ],
    ids=["blank_synonym", "invisible_synonym", "invisible_term", "invisible_notes"],
)
def test_text_that_cannot_be_cleaned_is_refused_before_the_append_lock(
    app_session: Session, capture_statements: Any, changes: WorkingCopyChanges
) -> None:
    submission = _new_test(app_session)

    def is_lock(statement: str, _parameters: object) -> bool:
        return "pg_advisory_xact_lock" in statement

    with (
        capture_statements(app_session.get_bind(), keep=is_lock) as locks,
        pytest.raises((TermCleaningError, FreeTextRefusedError)),
    ):
        _edit(app_session, submission, changes)

    assert locks == []


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_an_edit_that_changes_nothing_still_takes_the_append_lock(
    app_session: Session, capture_statements: Any
) -> None:
    """The state and version are checked under the lock, so a no-op is judged against the committed
    row. This pins that the docstring's statement is true."""
    submission = _new_test(app_session)

    def is_lock(statement: str, _parameters: object) -> bool:
        return "pg_advisory_xact_lock" in statement

    with capture_statements(app_session.get_bind(), keep=is_lock) as locks:
        result = _edit(app_session, submission, WorkingCopyChanges(notes=_ORIGINAL_NOTES))

    assert result.changed is False
    assert locks
