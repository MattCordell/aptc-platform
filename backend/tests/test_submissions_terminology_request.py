"""`nptc.submissions.terminology_request` service-layer tests (FR-30, FR-31, FR-44, NFR-08).

Each refusal asserts that no record, no transition row, no state change and no audit event
followed. Rows are read for the submission this test made, and `backend/tests` shares one Postgres
container (see `CLAUDE.md`), so nothing counts a whole table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import nptc.submissions.terminology_request as service_module
from nptc.audit.writer import AuditContext
from nptc.auth.errors_authorisation import PermissionDeniedError
from nptc.auth.permissions import Role, permissions_for_roles
from nptc.auth.principal import Principal
from nptc.db.models.audit import AuditEvent
from nptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from nptc.db.models.submission_terminology_request import (
    SubmissionTerminologyRequest,
    TargetEdition,
)
from nptc.db.models.submission_transition import SubmissionTransition
from nptc.db.models.user import User
from nptc.submissions.errors import (
    FreeTextField,
    FreeTextRefusedError,
    IllegalSubmissionTransitionError,
    SubmissionNotFoundError,
    SubmissionVersionConflictError,
    TerminologyRequestFieldRequiredError,
    TransitionReasonRequiredError,
)
from nptc.submissions.terminology_request import TerminologyRequestDetails, request_terminology

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
