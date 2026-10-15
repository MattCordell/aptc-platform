"""`nptc.submissions.terminology_request` service-layer tests (FR-30, FR-31, FR-44, NFR-08).

Each refusal asserts that no record, no transition row, no state change and no audit event
followed. Rows are read for the submission this test made, and `backend/tests` shares one Postgres
container (see `CLAUDE.md`), so nothing counts a whole table.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import nptc.submissions.terminology_request as service_module
from nptc.audit.writer import AuditContext
from nptc.auth.errors_authorisation import MfaRequiredError, PermissionDeniedError
from nptc.auth.permissions import Role, permissions_for_roles
from nptc.auth.principal import Principal
from nptc.db.models.audit import AuditEvent
from nptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from nptc.db.models.submission_terminology_request import (
    SubmissionTerminologyRequest,
    TargetEdition,
)
from nptc.db.models.submission_transition import SubmissionTransition
from nptc.db.models.submission_working_copy import SubmissionWorkingCopy
from nptc.db.models.user import User
from nptc.submissions.errors import (
    CodeRefusal,
    FreeTextField,
    FreeTextRefusedError,
    IllegalSubmissionTransitionError,
    SubmissionCodeRefusedError,
    SubmissionNotFoundError,
    SubmissionVersionConflictError,
    TerminologyRequestFieldRequiredError,
    TransitionReasonRequiredError,
)
from nptc.submissions.terminology_request import (
    TerminologyAttachment,
    TerminologyRequestDetails,
    attach_terminology_code,
    request_terminology,
)
from nptc.submissions.terms import SubmissionCodeMalformedError
from nptc_shared.terminology import (
    AU_LANGUAGE_TAG,
    Operation,
    StubConcept,
    StubTerminologyClient,
    TerminologyStatusError,
)

_REASON = "No concept covers this measurement."
ZERO_WIDTH_SPACE = chr(0x200B)


def _user(session: Session) -> User:
    user = User(username=f"user-{uuid.uuid4()}", display_name="Terminology tester")
    session.add(user)
    session.flush()
    return user


def _context(user_id: uuid.UUID | None) -> AuditContext:
    return AuditContext(
        actor_user_id=user_id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )


def _principal(user: User | None, *roles: Role) -> Principal:
    held = frozenset(roles)
    return Principal(
        user_id=None if user is None else user.id,
        user_ref=None,
        status=None,
        roles=held,
        permissions=permissions_for_roles(held),
        mfa_satisfied=True,
        mfa_suppressed_roles=frozenset(),
    )


def _submission(
    session: Session, *, state: SubmissionState = SubmissionState.UNDER_REVIEW
) -> Submission:
    submission = Submission(
        kind=SubmissionKind.NEW_TEST.value,
        state=state.value,
        preferred_term="Serum sodium",
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=_user(session).id,
    )
    session.add(submission)
    session.flush()
    return submission


def _details(**overrides: Any) -> TerminologyRequestDetails:
    values: dict[str, Any] = {
        "proposed_fsn": "Serum sodium level (observable entity)",
        "target_edition": TargetEdition.AU,
        "justification": "No concept covers this measurement.",
        "external_reference_system": "snomed-request-service",
        "external_reference_ref": "REQ-1",
        "raised_date": date(2026, 10, 1),
        "expected_release": date(2027, 3, 31),
    }
    values.update(overrides)
    return TerminologyRequestDetails(**values)


def _request(
    session: Session,
    submission: Submission,
    *,
    actor: User | None = None,
    roles: tuple[Role, ...] = (Role.REVIEWER,),
    details: TerminologyRequestDetails | None = None,
    reason: str = _REASON,
    expected_row_version: int | None = None,
) -> Submission:
    user = _user(session) if actor is None else actor
    return request_terminology(
        session,
        _context(user.id),
        _principal(user, *roles),
        submission_id=submission.id,
        details=_details() if details is None else details,
        reason=reason,
        expected_row_version=(
            submission.row_version if expected_row_version is None else expected_row_version
        ),
    )


def _assert_nothing_written(session: Session, submission_id: uuid.UUID, state: str) -> None:
    session.expire_all()
    current = session.get(Submission, submission_id)
    assert current is not None
    assert current.state == state
    assert current.row_version == 1
    assert session.get(SubmissionTerminologyRequest, submission_id) is None
    assert (
        session.execute(
            select(SubmissionTransition).where(SubmissionTransition.submission_id == submission_id)
        ).first()
        is None
    )
    assert (
        session.execute(
            select(AuditEvent).where(AuditEvent.entity_id == str(submission_id))
        ).first()
        is None
    )


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-31")
@pytest.mark.integration
def test_the_move_writes_the_state_the_record_and_the_transition_together(
    app_session: Session,
) -> None:
    submission = _submission(app_session)

    moved = _request(app_session, submission)

    assert moved.state == "Awaiting terminology"
    assert moved.row_version == 2
    record = app_session.get(SubmissionTerminologyRequest, submission.id)
    assert record is not None
    assert (record.proposed_fsn, record.target_edition) == (
        "Serum sodium level (observable entity)",
        "au",
    )
    assert record.raised_date == date(2026, 10, 1)
    assert record.expected_release == date(2027, 3, 31)
    [transition] = app_session.execute(
        select(SubmissionTransition).where(SubmissionTransition.submission_id == submission.id)
    ).scalars()
    assert (transition.from_state, transition.to_state) == ("Under review", "Awaiting terminology")


@pytest.mark.req("FR-30")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_failure_after_the_state_change_leaves_nothing_behind(
    app_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state, the transition row, the record and both events are one unit: when the last write
    fails, the first three are rolled back with it."""
    submission = _submission(app_session)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the record event could not be written")

    monkeypatch.setattr(service_module, "record_snapshot_change", fail)

    with pytest.raises(RuntimeError, match="record event"):
        _request(app_session, submission)

    _assert_nothing_written(app_session, submission.id, "Under review")


@pytest.mark.req("FR-30")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"proposed_fsn": "  "}, FreeTextField.PROPOSED_FSN),
        ({"justification": ""}, FreeTextField.JUSTIFICATION),
        ({"external_reference_system": " "}, FreeTextField.EXTERNAL_REFERENCE_SYSTEM),
        ({"external_reference_ref": ""}, FreeTextField.EXTERNAL_REFERENCE_REF),
    ],
    ids=["fsn", "justification", "system", "reference"],
)
def test_a_blank_text_field_is_refused_before_the_append_lock(
    app_session: Session,
    capture_statements: Any,
    overrides: dict[str, str],
    field: FreeTextField,
) -> None:
    submission = _submission(app_session)

    def is_lock(statement: str, _parameters: object) -> bool:
        return "pg_advisory_xact_lock" in statement

    with (
        capture_statements(app_session.get_bind(), keep=is_lock) as locks,
        pytest.raises(TerminologyRequestFieldRequiredError) as refused,
    ):
        _request(app_session, submission, details=_details(**overrides))

    assert refused.value.field is field
    assert locks == []
    _assert_nothing_written(app_session, submission.id, "Under review")


@pytest.mark.req("FR-63")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize(
    "overrides",
    [
        {"proposed_fsn": "Se" + ZERO_WIDTH_SPACE + "rum"},
        {"justification": "No" + ZERO_WIDTH_SPACE + "te"},
        {"external_reference_system": "s" + ZERO_WIDTH_SPACE},
        {"external_reference_ref": "R" + ZERO_WIDTH_SPACE},
    ],
    ids=["fsn", "justification", "system", "reference"],
)
def test_an_invisible_character_is_refused_before_the_append_lock(
    app_session: Session, capture_statements: Any, overrides: dict[str, str]
) -> None:
    submission = _submission(app_session)

    def is_lock(statement: str, _parameters: object) -> bool:
        return "pg_advisory_xact_lock" in statement

    with (
        capture_statements(app_session.get_bind(), keep=is_lock) as locks,
        pytest.raises(FreeTextRefusedError),
    ):
        _request(app_session, submission, details=_details(**overrides))

    assert locks == []
    _assert_nothing_written(app_session, submission.id, "Under review")


@pytest.mark.req("FR-28")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_blank_reason_is_refused_before_the_append_lock(
    app_session: Session, capture_statements: Any
) -> None:
    submission = _submission(app_session)

    def is_lock(statement: str, _parameters: object) -> bool:
        return "pg_advisory_xact_lock" in statement

    with (
        capture_statements(app_session.get_bind(), keep=is_lock) as locks,
        pytest.raises(TransitionReasonRequiredError),
    ):
        _request(app_session, submission, reason="   ")

    assert locks == []
    _assert_nothing_written(app_session, submission.id, "Under review")


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize("role", [Role.MEMBER, Role.OBSERVER, Role.PROVISIONAL])
def test_a_role_without_the_review_permission_is_refused(app_session: Session, role: Role) -> None:
    submission = _submission(app_session)

    with pytest.raises(PermissionDeniedError):
        _request(app_session, submission, roles=(role,))

    _assert_nothing_written(app_session, submission.id, "Under review")


@pytest.mark.req("FR-30")
@pytest.mark.integration
@pytest.mark.parametrize(
    "state",
    [s for s in SubmissionState if s is not SubmissionState.UNDER_REVIEW],
    ids=lambda s: s.value,
)
def test_a_submission_that_is_not_under_review_is_refused(
    app_session: Session, state: SubmissionState
) -> None:
    submission = _submission(app_session, state=state)

    with pytest.raises(IllegalSubmissionTransitionError):
        _request(app_session, submission)

    _assert_nothing_written(app_session, submission.id, state.value)


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_a_stale_row_version_is_refused_with_the_current_one(app_session: Session) -> None:
    submission = _submission(app_session)

    with pytest.raises(SubmissionVersionConflictError) as refused:
        _request(app_session, submission, expected_row_version=5)

    assert (refused.value.expected_row_version, refused.value.current_row_version) == (5, 1)
    _assert_nothing_written(app_session, submission.id, "Under review")


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_an_unknown_submission_is_refused(app_session: Session) -> None:
    user = _user(app_session)

    with pytest.raises(SubmissionNotFoundError):
        request_terminology(
            app_session,
            _context(user.id),
            _principal(user, Role.REVIEWER),
            submission_id=uuid.uuid4(),
            details=_details(),
            reason=_REASON,
            expected_row_version=1,
        )


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_a_move_needs_a_human_actor(app_session: Session) -> None:
    submission = _submission(app_session)

    with pytest.raises(ValueError, match="belongs to a person"):
        request_terminology(
            app_session,
            _context(None),
            _principal(None, Role.REVIEWER),
            submission_id=submission.id,
            details=_details(),
            reason=_REASON,
            expected_row_version=1,
        )

    _assert_nothing_written(app_session, submission.id, "Under review")


# --- attaching the code (FR-30, FR-06, FR-82, NFR-08) --------------------------------------------

_CODE = "391483001"
_FSN = "Microscopy (acid fast bacilli) (procedure)"
_INACTIVE_CODE = "119364003"
_ATTACH_REASON = "The authority issued the code."


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


def _awaiting(session: Session) -> Submission:
    """An `Awaiting terminology` submission at `row_version` 1, with no record: the attach reads
    only the state, so the record the move in writes is not needed here."""
    return _submission(session, state=SubmissionState.AWAITING_TERMINOLOGY)


def _attach(
    session: Session,
    submission: Submission,
    *,
    roles: tuple[Role, ...] = (Role.ADMINISTRATOR,),
    code: str = _CODE,
    reason: str = _ATTACH_REASON,
    expected_row_version: int | None = None,
    client: StubTerminologyClient | None = None,
    actor: User | None = None,
) -> TerminologyAttachment:
    user = _user(session) if actor is None else actor
    return attach_terminology_code(
        session,
        _context(user.id),
        _principal(user, *roles),
        submission_id=submission.id,
        snomed_code=code,
        reason=reason,
        expected_row_version=(
            submission.row_version if expected_row_version is None else expected_row_version
        ),
        terminology_client=_client() if client is None else client,
    )


def _assert_attach_wrote_nothing(session: Session, submission_id: uuid.UUID) -> None:
    session.expire_all()
    current = session.get(Submission, submission_id)
    assert current is not None
    assert current.state == "Awaiting terminology"
    assert current.row_version == 1
    assert session.get(SubmissionWorkingCopy, submission_id) is None
    assert (
        session.execute(
            select(SubmissionTransition).where(SubmissionTransition.submission_id == submission_id)
        ).first()
        is None
    )
    assert (
        session.execute(
            select(AuditEvent).where(AuditEvent.entity_id == str(submission_id))
        ).first()
        is None
    )


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_the_attach_writes_the_code_the_state_and_the_transition_together(
    app_session: Session,
) -> None:
    submission = _awaiting(app_session)

    attached = _attach(app_session, submission)

    assert attached.submission.state == "Ready for approval"
    assert attached.submission.row_version == 2
    assert (attached.content.snomed_code, attached.content.snomed_fsn) == (_CODE, _FSN)
    copy = app_session.get(SubmissionWorkingCopy, submission.id)
    assert copy is not None
    assert (copy.snomed_code, copy.snomed_fsn) == (_CODE, _FSN)
    [transition] = app_session.execute(
        select(SubmissionTransition).where(SubmissionTransition.submission_id == submission.id)
    ).scalars()
    assert (transition.from_state, transition.to_state) == (
        "Awaiting terminology",
        "Ready for approval",
    )


@pytest.mark.req("FR-30")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_code_the_working_copy_already_carries_still_moves_the_submission(
    app_session: Session,
) -> None:
    """The reviewer may have set the same code while the submission waited. The move still happens,
    and the event still records the state, so the audit diff is never empty."""
    submission = _awaiting(app_session)
    app_session.add(
        SubmissionWorkingCopy(
            submission_id=submission.id,
            preferred_term=submission.preferred_term,
            snomed_code=_CODE,
            snomed_fsn=_FSN,
        )
    )
    app_session.flush()

    attached = _attach(app_session, submission)

    assert attached.submission.state == "Ready for approval"
    [event] = app_session.execute(
        select(AuditEvent).where(
            AuditEvent.entity_id == str(submission.id),
            AuditEvent.action == "submission.terminology_attached",
        )
    ).scalars()
    # The diff keeps only what changed, so the unchanged code is not repeated.
    assert event.before == {"state": "Awaiting terminology"}
    assert event.after == {"state": "Ready for approval"}


@pytest.mark.req("FR-30")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_failure_after_the_state_change_leaves_nothing_behind_on_the_attach(
    app_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state, the transition row, the working copy and the event are one unit: when the event
    cannot be written, the other three are rolled back with it."""
    submission = _awaiting(app_session)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("the event could not be written")

    monkeypatch.setattr(service_module, "record_snapshot_change", fail)

    with pytest.raises(RuntimeError, match="event could not"):
        _attach(app_session, submission)

    _assert_attach_wrote_nothing(app_session, submission.id)


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize(
    "role", [Role.REVIEWER, Role.MEMBER, Role.OBSERVER, Role.PROVISIONAL], ids=lambda r: r.value
)
def test_only_an_administrator_may_attach_and_no_terminology_call_precedes_the_refusal(
    app_session: Session, role: Role
) -> None:
    submission = _awaiting(app_session)
    client = _client()

    with pytest.raises(PermissionDeniedError):
        _attach(app_session, submission, roles=(role,), client=client)

    assert client.requests == ()
    _assert_attach_wrote_nothing(app_session, submission.id)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_administrator_without_mfa_is_told_to_step_up(app_session: Session) -> None:
    submission = _awaiting(app_session)
    user = _user(app_session)
    held = frozenset({Role.ADMINISTRATOR})
    principal = Principal(
        user_id=user.id,
        user_ref=None,
        status=None,
        roles=held,
        permissions=frozenset(),
        mfa_satisfied=False,
        mfa_suppressed_roles=held,
    )

    with pytest.raises(MfaRequiredError):
        attach_terminology_code(
            app_session,
            _context(user.id),
            principal,
            submission_id=submission.id,
            snomed_code=_CODE,
            reason=_ATTACH_REASON,
            expected_row_version=1,
            terminology_client=_client(),
        )

    _assert_attach_wrote_nothing(app_session, submission.id)


@pytest.mark.req("FR-30")
@pytest.mark.integration
@pytest.mark.parametrize(
    "state",
    [s for s in SubmissionState if s is not SubmissionState.AWAITING_TERMINOLOGY],
    ids=lambda s: s.value,
)
def test_a_submission_that_is_not_awaiting_terminology_is_refused_without_a_lookup(
    app_session: Session, state: SubmissionState
) -> None:
    submission = _submission(app_session, state=state)
    client = _client()

    with pytest.raises(IllegalSubmissionTransitionError):
        _attach(app_session, submission, client=client)

    assert client.requests == ()
    assert app_session.get(SubmissionWorkingCopy, submission.id) is None


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_an_unknown_submission_is_refused_on_the_attach(app_session: Session) -> None:
    user = _user(app_session)

    with pytest.raises(SubmissionNotFoundError):
        attach_terminology_code(
            app_session,
            _context(user.id),
            _principal(user, Role.ADMINISTRATOR),
            submission_id=uuid.uuid4(),
            snomed_code=_CODE,
            reason=_ATTACH_REASON,
            expected_row_version=1,
            terminology_client=_client(),
        )


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_a_stale_row_version_is_refused_on_the_attach_with_the_current_one(
    app_session: Session,
) -> None:
    submission = _awaiting(app_session)

    with pytest.raises(SubmissionVersionConflictError) as refused:
        _attach(app_session, submission, expected_row_version=4)

    assert (refused.value.expected_row_version, refused.value.current_row_version) == (4, 1)
    _assert_attach_wrote_nothing(app_session, submission.id)


@pytest.mark.req("FR-06")
@pytest.mark.req("FR-82")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("code", "error"),
    [
        (_INACTIVE_CODE, SubmissionCodeRefusedError),
        ("26604008", SubmissionCodeMalformedError),
        ("not-a-code", SubmissionCodeMalformedError),
    ],
    ids=["inactive", "bad_check_digit", "not_digits"],
)
def test_a_code_the_server_rules_out_is_refused_and_writes_nothing(
    app_session: Session, code: str, error: type[Exception]
) -> None:
    submission = _awaiting(app_session)

    with pytest.raises(error):
        _attach(app_session, submission, code=code)

    _assert_attach_wrote_nothing(app_session, submission.id)


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_a_code_the_server_does_not_know_is_refused_and_writes_nothing(
    app_session: Session,
) -> None:
    submission = _awaiting(app_session)
    client = _client()
    client.seed_error(Operation.LOOKUP, TerminologyStatusError("not found", status_code=404))

    with pytest.raises(SubmissionCodeRefusedError) as refused:
        _attach(app_session, submission, code="26604007", client=client)

    assert refused.value.reason is CodeRefusal.NOT_FOUND
    _assert_attach_wrote_nothing(app_session, submission.id)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_blank_reason_is_refused_on_the_attach_before_any_read_or_lock(
    app_session: Session, capture_statements: Any
) -> None:
    submission = _awaiting(app_session)
    client = _client()
    actor = _user(app_session)

    with (
        capture_statements(app_session.get_bind()) as statements,
        pytest.raises(TransitionReasonRequiredError),
    ):
        _attach(app_session, submission, reason="   ", client=client, actor=actor)

    assert statements == []
    assert client.requests == ()


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


def _is_lock(statement: str, _parameters: object) -> bool:
    return "pg_advisory_xact_lock" in statement


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_no_terminology_call_is_made_while_the_append_lock_is_held(
    app_session: Session, capture_statements: Any
) -> None:
    """The lock is one global advisory lock, so a call to the terminology server made while holding
    it would stall every audit write. The lock-ordering guard exempts this writer because it must
    not take the lock first, so this test is what pins the order."""
    submission = _awaiting(app_session)
    calls: list[bool] = []

    with capture_statements(app_session.get_bind(), keep=_is_lock) as locks:
        client = _CallRecordingClient(lambda: bool(locks), calls)
        client.add_concept(
            StubConcept(
                code=_CODE,
                fsn=_FSN,
                preferred_terms={AU_LANGUAGE_TAG: "Acid fast bacilli microscopy"},
            )
        )
        _attach(app_session, submission, client=client)

    assert calls == [False]
    # The probe does see the lock the write takes, so the assertion above cannot pass vacuously.
    assert locks


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_refused_attach_takes_no_append_lock(
    app_session: Session, capture_statements: Any
) -> None:
    submission = _awaiting(app_session)

    with (
        capture_statements(app_session.get_bind(), keep=_is_lock) as locks,
        pytest.raises(SubmissionCodeRefusedError),
    ):
        _attach(app_session, submission, code=_INACTIVE_CODE)

    assert locks == []


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_caller_who_may_not_attach_takes_no_append_lock(
    app_session: Session, capture_statements: Any
) -> None:
    submission = _awaiting(app_session)

    with (
        capture_statements(app_session.get_bind(), keep=_is_lock) as locks,
        pytest.raises(PermissionDeniedError),
    ):
        _attach(app_session, submission, roles=(Role.REVIEWER,))

    assert locks == []


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_an_attach_needs_a_human_actor(app_session: Session) -> None:
    submission = _awaiting(app_session)

    with pytest.raises(ValueError, match="belongs to a person"):
        attach_terminology_code(
            app_session,
            _context(None),
            _principal(None, Role.ADMINISTRATOR),
            submission_id=submission.id,
            snomed_code=_CODE,
            reason=_ATTACH_REASON,
            expected_row_version=1,
            terminology_client=_client(),
        )

    _assert_attach_wrote_nothing(app_session, submission.id)
