"""`nptc.submissions.transitions` service-layer tests (FR-28, FR-44, FR-63, NFR-08).

Each refusal asserts that no transition row and no audit event was added. `backend/tests` shares one
Postgres container (see `CLAUDE.md`), so every query is scoped to the rows this test created, and an
audit event is found by the id of the submission it describes. The race tests use real connections,
because one session running the code twice cannot reproduce two transactions.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import nptc.submissions.transitions as transitions_module
from nptc.audit.writer import AuditContext
from nptc.auth.errors_authorisation import PermissionDeniedError
from nptc.auth.permissions import Permission, Role, permissions_for_roles
from nptc.auth.principal import Principal
from nptc.db.models.audit import AuditEvent
from nptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from nptc.db.models.submission_transition import SubmissionTransition
from nptc.db.models.user import User
from nptc.submissions.errors import (
    FreeTextRefusedError,
    IllegalSubmissionTransitionError,
    SubmissionNotFoundError,
    SubmissionVersionConflictError,
    TransitionReasonRequiredError,
)
from nptc.submissions.transitions import PERMITTED_TRANSITIONS, transition_submission

_REASON = "Entered by mistake"


def _context(user_id: uuid.UUID) -> AuditContext:
    return AuditContext(
        actor_user_id=user_id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )


def _principal(user: User, *roles: Role) -> Principal:
    held = frozenset(roles)
    return Principal(
        user_id=user.id,
        user_ref=None,
        status=None,
        roles=held,
        permissions=permissions_for_roles(held),
        mfa_satisfied=True,
        mfa_suppressed_roles=frozenset(),
    )


def _user(session: Session) -> User:
    user = User(username=f"transition-{uuid.uuid4()}", display_name="Transition Tester")
    session.add(user)
    session.flush()
    return user


def _submission(session: Session, submitter: User, *, state: str = "Submitted") -> Submission:
    submission = Submission(
        kind=SubmissionKind.NEW_TEST.value,
        state=state,
        preferred_term=f"Transition submission {uuid.uuid4()}",
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=submitter.id,
        organisation="Submitter Lab",
    )
    session.add(submission)
    session.flush()
    return submission


def _withdraw(
    session: Session,
    submitter: User,
    submission: Submission,
    *,
    actor: User | None = None,
    roles: tuple[Role, ...] = (Role.MEMBER,),
    reason: str = _REASON,
    expected_row_version: int | None = None,
) -> Submission:
    who = actor or submitter
    return transition_submission(
        session,
        _context(who.id),
        _principal(who, *roles),
        submission_id=submission.id,
        to_state=SubmissionState.WITHDRAWN,
        reason=reason,
        expected_row_version=(
            submission.row_version if expected_row_version is None else expected_row_version
        ),
    )


def _transitions(session: Session, submission_id: uuid.UUID) -> list[SubmissionTransition]:
    return list(
        session.execute(
            select(SubmissionTransition).where(SubmissionTransition.submission_id == submission_id)
        ).scalars()
    )


def _events(session: Session, submission_id: uuid.UUID) -> list[AuditEvent]:
    return list(
        session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == "submission", AuditEvent.entity_id == str(submission_id)
            )
            .order_by(AuditEvent.sequence)
        ).scalars()
    )


def _assert_nothing_written(session: Session, submission_id: uuid.UUID, state: str) -> None:
    assert _transitions(session, submission_id) == []
    assert _events(session, submission_id) == []
    session.expire_all()
    assert session.get(Submission, submission_id).state == state  # type: ignore[union-attr]


# --- the move itself (FR-28, NFR-08) -------------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Submitted", "Triage"])
def test_the_submitter_withdraws_from_an_open_state(app_session: Session, state: str) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter, state=state)

    result = _withdraw(app_session, submitter, submission)

    assert result.state == "Withdrawn"
    [row] = _transitions(app_session, submission.id)
    assert (row.from_state, row.to_state) == (state, "Withdrawn")
    assert row.actor_id == submitter.id
    assert row.reason == _REASON


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_withdrawal_appends_one_audit_event_with_the_states_and_the_reason(
    app_session: Session,
) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)

    _withdraw(app_session, submitter, submission)

    [event] = _events(app_session, submission.id)
    assert event.action == "submission.withdrawn"
    assert event.actor_user_id == submitter.id
    assert event.reason == _REASON
    assert event.before == {"state": "Submitted"}
    assert event.after == {"state": "Withdrawn"}


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_withdrawal_moves_the_row_version_on(app_session: Session) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)
    before = submission.row_version

    result = _withdraw(app_session, submitter, submission)

    assert result.row_version == before + 1


@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_the_reason_is_stored_cleaned(app_session: Session) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)

    _withdraw(app_session, submitter, submission, reason="  Entered by mistake \n")

    [row] = _transitions(app_session, submission.id)
    assert row.reason == "Entered by mistake"
    [event] = _events(app_session, submission.id)
    assert event.reason == "Entered by mistake"


# --- a move that is not allowed (FR-28) ----------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Withdrawn", "Rejected", "Published in release"])
def test_a_closed_submission_cannot_be_withdrawn(app_session: Session, state: str) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter, state=state)

    with pytest.raises(IllegalSubmissionTransitionError) as refused:
        _withdraw(app_session, submitter, submission)

    assert (refused.value.from_state, refused.value.to_state) == (state, "Withdrawn")
    _assert_nothing_written(app_session, submission.id, state)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_second_withdrawal_is_refused_not_repeated(app_session: Session) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)
    _withdraw(app_session, submitter, submission)

    with pytest.raises(IllegalSubmissionTransitionError):
        _withdraw(app_session, submitter, submission)

    assert len(_transitions(app_session, submission.id)) == 1
    assert len(_events(app_session, submission.id)) == 1


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_move_with_no_row_in_the_table_is_refused(app_session: Session) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)

    with pytest.raises(IllegalSubmissionTransitionError):
        transition_submission(
            app_session,
            _context(submitter.id),
            _principal(submitter, Role.MEMBER),
            submission_id=submission.id,
            to_state=SubmissionState.REJECTED,
            reason=_REASON,
            expected_row_version=submission.row_version,
        )

    _assert_nothing_written(app_session, submission.id, "Submitted")


@pytest.mark.req("FR-28")
def test_the_table_seeds_only_the_two_withdrawals() -> None:
    assert set(PERMITTED_TRANSITIONS) == {
        (SubmissionState.SUBMITTED, SubmissionState.WITHDRAWN),
        (SubmissionState.TRIAGE, SubmissionState.WITHDRAWN),
    }


# --- who may move it (FR-44) ---------------------------------------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_another_member_cannot_withdraw_someone_elses_submission(app_session: Session) -> None:
    submitter = _user(app_session)
    other = _user(app_session)
    submission = _submission(app_session, submitter)

    with pytest.raises(PermissionDeniedError):
        _withdraw(app_session, submitter, submission, actor=other)

    _assert_nothing_written(app_session, submission.id, "Submitted")


@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", [Role.OBSERVER, Role.ADMINISTRATOR])
def test_a_role_without_the_own_permission_cannot_withdraw_even_its_own(
    app_session: Session, role: Role
) -> None:
    """The Administrator holds `submission.withdraw.any` and not `.own`, and the table has no `.any`
    row yet, so the Administrator cannot withdraw their own submission either."""
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)
    assert Permission.SUBMISSION_WITHDRAW_OWN not in permissions_for_roles(frozenset({role}))

    with pytest.raises(PermissionDeniedError):
        _withdraw(app_session, submitter, submission, roles=(role,))

    _assert_nothing_written(app_session, submission.id, "Submitted")


@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", [Role.PROVISIONAL, Role.MEMBER, Role.REVIEWER])
def test_each_role_with_the_own_permission_may_withdraw_its_own(
    app_session: Session, role: Role
) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)

    result = _withdraw(app_session, submitter, submission, roles=(role,))

    assert result.state == "Withdrawn"


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_unknown_submission_is_not_found(app_session: Session) -> None:
    submitter = _user(app_session)

    with pytest.raises(SubmissionNotFoundError):
        transition_submission(
            app_session,
            _context(submitter.id),
            _principal(submitter, Role.MEMBER),
            submission_id=uuid.uuid4(),
            to_state=SubmissionState.WITHDRAWN,
            reason=_REASON,
            expected_row_version=1,
        )


# --- the reason (FR-28, FR-63) -------------------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
def test_a_blank_reason_is_refused(app_session: Session, reason: str) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)

    with pytest.raises(TransitionReasonRequiredError):
        _withdraw(app_session, submitter, submission, reason=reason)

    _assert_nothing_written(app_session, submission.id, "Submitted")


@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_a_reason_with_an_invisible_character_is_refused(app_session: Session) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)
    zero_width_space = chr(0x200B)

    with pytest.raises(FreeTextRefusedError) as refused:
        _withdraw(app_session, submitter, submission, reason=f"Entered{zero_width_space}in error")

    assert refused.value.field.value == "reason"
    _assert_nothing_written(app_session, submission.id, "Submitted")


# --- optimistic locking (FR-28) ------------------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_stale_row_version_is_refused_and_changes_nothing(app_session: Session) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)

    with pytest.raises(SubmissionVersionConflictError) as refused:
        _withdraw(
            app_session, submitter, submission, expected_row_version=submission.row_version + 1
        )

    assert refused.value.current_row_version == submission.row_version
    assert refused.value.expected_row_version == submission.row_version + 1
    _assert_nothing_written(app_session, submission.id, "Submitted")


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_failure_after_the_state_change_leaves_no_trace(
    app_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit write is the last step. If it fails, the state change and the transition row go
    with it, so the three records agree or none exists."""
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)

    def failing(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(transitions_module, "record_change", failing)

    with pytest.raises(RuntimeError, match="audit write failed"):
        _withdraw(app_session, submitter, submission)

    _assert_nothing_written(app_session, submission.id, "Submitted")


# --- concurrency (FR-28) -------------------------------------------------------------------


@pytest.fixture
def committed_submission(
    pristine_audit_event: None, app_engine: Engine, owner_engine: Engine
) -> Iterator[tuple[uuid.UUID, uuid.UUID, int]]:
    """A committed submission, its submitter and its row version. Declared after
    `pristine_audit_event`, so this teardown runs first and clears the rows that block its wipe of
    the user table, which then removes the user."""
    with Session(app_engine) as setup:
        submitter = _user(setup)
        submission = _submission(setup, submitter)
        submission_id, submitter_id, version = submission.id, submitter.id, submission.row_version
        setup.commit()
    yield submission_id, submitter_id, version
    with owner_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM submission_transition WHERE submission_id = :s"),
            {"s": submission_id},
        )
        connection.execute(text("DELETE FROM submission WHERE id = :s"), {"s": submission_id})


def _committed_withdrawal(
    engine: Engine, submitter_id: uuid.UUID, submission_id: uuid.UUID, version: int
) -> None:
    with Session(engine) as session:
        submitter = session.get(User, submitter_id)
        assert submitter is not None
        try:
            transition_submission(
                session,
                _context(submitter_id),
                _principal(submitter, Role.MEMBER),
                submission_id=submission_id,
                to_state=SubmissionState.WITHDRAWN,
                reason=_REASON,
                expected_row_version=version,
            )
            session.commit()
        except BaseException:
            session.rollback()
            raise


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_two_simultaneous_withdrawals_leave_one_transition(
    committed_submission: tuple[uuid.UUID, uuid.UUID, int], app_engine: Engine
) -> None:
    """Both callers read version 1. The append lock makes the second wait, then see `Withdrawn`
    and refuse, so exactly one wins and one transition row exists."""
    submission_id, submitter_id, version = committed_submission
    barrier = threading.Barrier(2)
    outcomes: dict[str, BaseException | None] = {}

    def attempt(name: str) -> None:
        barrier.wait(timeout=5)
        try:
            _committed_withdrawal(app_engine, submitter_id, submission_id, version)
            outcomes[name] = None
        except BaseException as exc:  # recorded and asserted below, not swallowed
            outcomes[name] = exc

    threads = [threading.Thread(target=attempt, args=(name,)) for name in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not any(thread.is_alive() for thread in threads), "a request hung: looks like a deadlock"
    refusals = [outcome for outcome in outcomes.values() if outcome is not None]
    assert len(outcomes) == 2
    assert len(refusals) == 1
    assert isinstance(
        refusals[0], IllegalSubmissionTransitionError | SubmissionVersionConflictError
    )
    with Session(app_engine) as check:
        assert len(_transitions(check, submission_id)) == 1
        assert len(_events(check, submission_id)) == 1


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_row_version_that_moves_after_the_check_is_still_a_conflict(
    committed_submission: tuple[uuid.UUID, uuid.UUID, int],
    app_engine: Engine,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A writer that skipped the append lock can move the row between the version check and the
    flush. `StaleDataError` from that flush becomes the same refusal as a stale `row_version`."""
    submission_id, submitter_id, version = committed_submission
    real_record_change = transitions_module.record_change

    def moves_the_row_first(*args: Any, **kwargs: Any) -> Any:
        with owner_engine.begin() as connection:
            connection.execute(
                text("UPDATE submission SET row_version = row_version + 1 WHERE id = :s"),
                {"s": submission_id},
            )
        return real_record_change(*args, **kwargs)

    monkeypatch.setattr(transitions_module, "record_change", moves_the_row_first)

    with pytest.raises(SubmissionVersionConflictError) as refused:
        _committed_withdrawal(app_engine, submitter_id, submission_id, version)

    assert refused.value.current_row_version == version + 1
    with Session(app_engine) as check:
        assert _transitions(check, submission_id) == []
        assert (
            check.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.entity_id == str(submission_id))
            ).scalar_one()
            == 0
        )
        assert check.get(Submission, submission_id).state == "Submitted"  # type: ignore[union-attr]
