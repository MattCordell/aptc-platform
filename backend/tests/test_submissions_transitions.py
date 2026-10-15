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
from nptc.auth.errors_authorisation import MfaRequiredError, PermissionDeniedError
from nptc.auth.permissions import ADMINISTRATOR_ONLY, Permission, Role, permissions_for_roles
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
    TransitionNeedsOwnRouteError,
    TransitionReasonRequiredError,
)
from nptc.submissions.transitions import (
    PERMITTED_TRANSITIONS,
    TRANSITION_PERMISSIONS,
    available_transitions,
    transition_submission,
)

_REASON = "Entered by mistake"


def _context(user_id: uuid.UUID) -> AuditContext:
    return AuditContext(
        actor_user_id=user_id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )


def _principal(user: User, *roles: Role, suppressed: frozenset[Role] = frozenset()) -> Principal:
    """`suppressed` is a role the user holds but whose permissions are withheld for want of MFA, as
    `principal_for` does for an Administrator who has not stepped up."""
    held = frozenset(roles)
    return Principal(
        user_id=user.id,
        user_ref=None,
        status=None,
        roles=held,
        permissions=permissions_for_roles(held),
        mfa_satisfied=not suppressed,
        mfa_suppressed_roles=suppressed,
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


# --- the review moves (FR-28, FR-81) -------------------------------------------------------

_REVIEW_MOVES = [
    ("Submitted", "Triage", "submission.triaged"),
    ("Triage", "Under review", "submission.review_started"),
    ("Triage", "Rejected", "submission.rejected"),
    ("Rejected", "Triage", "submission.reopened"),
    ("Under review", "Ready for approval", "submission.readied_for_approval"),
]
_REVIEWING_ROLES = [Role.REVIEWER, Role.ADMINISTRATOR]
_NON_REVIEWING_ROLES = [Role.OBSERVER, Role.PROVISIONAL, Role.MEMBER]


def _move(
    session: Session,
    submission: Submission,
    actor: User,
    role: Role,
    to_state: str,
    *,
    reason: str = _REASON,
) -> Submission:
    return transition_submission(
        session,
        _context(actor.id),
        _principal(actor, role),
        submission_id=submission.id,
        to_state=SubmissionState(to_state),
        reason=reason,
        expected_row_version=submission.row_version,
    )


@pytest.mark.req("FR-28")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REVIEWING_ROLES)
@pytest.mark.parametrize(("from_state", "to_state", "action"), _REVIEW_MOVES)
def test_a_reviewer_and_an_administrator_make_each_review_move(
    app_session: Session, role: Role, from_state: str, to_state: str, action: str
) -> None:
    submitter = _user(app_session)
    reviewer = _user(app_session)
    submission = _submission(app_session, submitter, state=from_state)

    result = _move(app_session, submission, reviewer, role, to_state)

    assert result.state == to_state
    [row] = _transitions(app_session, submission.id)
    assert (row.from_state, row.to_state, row.actor_id, row.reason) == (
        from_state,
        to_state,
        reviewer.id,
        _REASON,
    )
    [event] = _events(app_session, submission.id)
    assert event.action == action
    assert event.actor_user_id == reviewer.id
    assert event.before == {"state": from_state}
    assert event.after == {"state": to_state}


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_reopened_submission_keeps_its_history_and_stays_the_same_record(
    app_session: Session,
) -> None:
    submitter = _user(app_session)
    reviewer = _user(app_session)
    submission = _submission(app_session, submitter)

    _move(app_session, submission, reviewer, Role.REVIEWER, "Triage", reason="Looks complete")
    _move(app_session, submission, reviewer, Role.REVIEWER, "Rejected", reason="Out of scope")
    reopened = _move(app_session, submission, reviewer, Role.REVIEWER, "Triage", reason="Appeal")

    assert reopened.id == submission.id
    assert reopened.state == "Triage"
    history = sorted(_transitions(app_session, submission.id), key=lambda row: row.created_at)
    assert [(row.from_state, row.to_state, row.reason) for row in history] == [
        ("Submitted", "Triage", "Looks complete"),
        ("Triage", "Rejected", "Out of scope"),
        ("Rejected", "Triage", "Appeal"),
    ]
    assert [event.action for event in _events(app_session, submission.id)] == [
        "submission.triaged",
        "submission.rejected",
        "submission.reopened",
    ]
    submissions_of_submitter = app_session.execute(
        select(func.count()).select_from(Submission).where(Submission.submitter_id == submitter.id)
    ).scalar_one()
    assert submissions_of_submitter == 1


@pytest.mark.req("FR-81")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", _NON_REVIEWING_ROLES)
@pytest.mark.parametrize(("from_state", "to_state", "action"), _REVIEW_MOVES)
def test_a_role_without_the_review_permission_is_refused_every_review_move(
    app_session: Session, role: Role, from_state: str, to_state: str, action: str
) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter, state=from_state)
    assert Permission.SUBMISSION_TRANSITION_REVIEW not in permissions_for_roles(frozenset({role}))
    assert action

    with pytest.raises(PermissionDeniedError, match=r"submission\.transition\.review"):
        _move(app_session, submission, submitter, role, to_state)

    _assert_nothing_written(app_session, submission.id, from_state)


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REVIEWING_ROLES)
@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        ("Submitted", "Ready for approval"),
        ("Submitted", "Under review"),
        ("Submitted", "Rejected"),
        ("Triage", "Ready for approval"),
        ("Under review", "Rejected"),
        ("Under review", "Triage"),
        ("Ready for approval", "Under review"),
        ("Ready for approval", "Triage"),
        ("Rejected", "Under review"),
        ("Triage", "Approved"),
        ("Published in release", "Triage"),
        ("Withdrawn", "Triage"),
    ],
)
def test_a_move_the_table_has_no_row_for_is_refused_for_a_reviewer(
    app_session: Session, role: Role, from_state: str, to_state: str
) -> None:
    submitter = _user(app_session)
    reviewer = _user(app_session)
    submission = _submission(app_session, submitter, state=from_state)

    with pytest.raises(IllegalSubmissionTransitionError):
        _move(app_session, submission, reviewer, role, to_state)

    _assert_nothing_written(app_session, submission.id, from_state)


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-29")
@pytest.mark.req("FR-44")
def test_only_an_administrator_move_from_ready_for_approval_enters_approved() -> None:
    """Approval also writes the catalogue entry, so it is a move of its own: one row, one source
    state, an Administrator-only permission and a service of its own."""
    entering = {
        move: row
        for move, row in PERMITTED_TRANSITIONS.items()
        if move[1] is SubmissionState.APPROVED
    }

    assert set(entering) == {(SubmissionState.READY_FOR_APPROVAL, SubmissionState.APPROVED)}
    row = entering[(SubmissionState.READY_FOR_APPROVAL, SubmissionState.APPROVED)]
    assert [requirement.permission for requirement in row.requirements] == [
        Permission.SUBMISSION_TRANSITION_APPROVE
    ]
    assert Permission.SUBMISSION_TRANSITION_APPROVE in ADMINISTRATOR_ONLY
    assert row.needs_payload


# --- a suppressed Administrator who holds another role (FR-44) -----------------------------


@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("state", "to_state", "own"),
    [
        ("Approved", "Withdrawn", True),
        ("Published in release", "Withdrawn", True),
        ("Triage", "Withdrawn", False),
        ("Submitted", "Triage", False),
    ],
    ids=["approved", "published", "someone-elses-triage", "review-move"],
)
def test_a_caller_whose_administrator_role_awaits_mfa_is_told_to_step_up(
    app_session: Session, state: str, to_state: str, own: bool
) -> None:
    """The user also holds Member, so the route gate lets them in. The move needs a permission only
    the suppressed Administrator role grants, so the refusal says stepping up would help."""
    submitter = _user(app_session)
    actor = submitter if own else _user(app_session)
    submission = _submission(app_session, submitter, state=state)
    principal = _principal(actor, Role.MEMBER, suppressed=frozenset({Role.ADMINISTRATOR}))

    with pytest.raises(MfaRequiredError):
        transition_submission(
            app_session,
            _context(actor.id),
            principal,
            submission_id=submission.id,
            to_state=SubmissionState(to_state),
            reason=_REASON,
            expected_row_version=submission.row_version,
        )

    _assert_nothing_written(app_session, submission.id, state)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_caller_with_no_suppressed_role_gets_the_plain_refusal(app_session: Session) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter, state="Approved")

    with pytest.raises(PermissionDeniedError) as refused:
        _withdraw(app_session, submitter, submission, roles=(Role.MEMBER,))

    assert not isinstance(refused.value, MfaRequiredError)
    _assert_nothing_written(app_session, submission.id, "Approved")


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_suppressed_administrator_still_withdraws_their_own_open_submission(
    app_session: Session,
) -> None:
    """The Member role grants `.own`, so the move needs no step-up and the suppressed role is not
    what decides it."""
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)
    principal = _principal(submitter, Role.MEMBER, suppressed=frozenset({Role.ADMINISTRATOR}))

    result = transition_submission(
        app_session,
        _context(submitter.id),
        principal,
        submission_id=submission.id,
        to_state=SubmissionState.WITHDRAWN,
        reason=_REASON,
        expected_row_version=submission.row_version,
    )

    assert result.state == "Withdrawn"


# --- what a caller may do now (FR-28) ------------------------------------------------------


def _available(user: User, role: Role, state: str, *, submitter: User) -> list[str]:
    return [
        target.value
        for target in available_transitions(
            _principal(user, role), from_state=SubmissionState(state), submitter_id=submitter.id
        )
    ]


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_member_may_withdraw_their_own_open_submission_and_nothing_else(
    app_session: Session,
) -> None:
    member = _user(app_session)

    assert _available(member, Role.MEMBER, "Submitted", submitter=member) == ["Withdrawn"]
    assert _available(member, Role.MEMBER, "Triage", submitter=member) == ["Withdrawn"]
    assert _available(member, Role.MEMBER, "Under review", submitter=member) == []
    assert _available(member, Role.MEMBER, "Approved", submitter=member) == []


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Submitted", "Triage", "Under review", "Ready for approval"])
def test_a_member_may_make_no_move_on_someone_elses_submission(
    app_session: Session, state: str
) -> None:
    member = _user(app_session)
    other = _user(app_session)

    assert _available(member, Role.MEMBER, state, submitter=other) == []


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Withdrawn", "Rejected", "Published in release"])
def test_a_member_has_no_move_on_a_closed_submission_of_their_own(
    app_session: Session, state: str
) -> None:
    member = _user(app_session)

    assert _available(member, Role.MEMBER, state, submitter=member) == []


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_reviewer_is_offered_the_review_moves_and_withdrawal_only_of_their_own(
    app_session: Session,
) -> None:
    reviewer = _user(app_session)
    other = _user(app_session)

    assert _available(reviewer, Role.REVIEWER, "Submitted", submitter=other) == ["Triage"]
    assert _available(reviewer, Role.REVIEWER, "Submitted", submitter=reviewer) == [
        "Triage",
        "Withdrawn",
    ]
    assert _available(reviewer, Role.REVIEWER, "Triage", submitter=other) == [
        "Under review",
        "Rejected",
    ]
    assert _available(reviewer, Role.REVIEWER, "Rejected", submitter=other) == ["Triage"]
    assert _available(reviewer, Role.REVIEWER, "Under review", submitter=other) == [
        "Ready for approval",
        "Awaiting terminology",
    ]
    assert _available(reviewer, Role.REVIEWER, "Awaiting terminology", submitter=other) == [
        "Under review"
    ]
    assert _available(reviewer, Role.REVIEWER, "Approved", submitter=other) == []
    assert _available(reviewer, Role.REVIEWER, "Published in release", submitter=other) == []


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_administrator_is_offered_withdrawal_from_every_open_and_approved_state(
    app_session: Session,
) -> None:
    admin = _user(app_session)
    other = _user(app_session)

    assert _available(admin, Role.ADMINISTRATOR, "Submitted", submitter=other) == [
        "Triage",
        "Withdrawn",
    ]
    assert _available(admin, Role.ADMINISTRATOR, "Triage", submitter=admin) == [
        "Under review",
        "Rejected",
        "Withdrawn",
    ]
    assert _available(admin, Role.ADMINISTRATOR, "Awaiting terminology", submitter=other) == [
        "Ready for approval",
        "Under review",
    ]
    assert _available(admin, Role.ADMINISTRATOR, "Approved", submitter=other) == ["Withdrawn"]
    assert _available(admin, Role.ADMINISTRATOR, "Published in release", submitter=other) == [
        "Withdrawn"
    ]
    assert _available(admin, Role.ADMINISTRATOR, "Withdrawn", submitter=other) == []


@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("state", [s.value for s in SubmissionState])
def test_every_offered_move_is_one_the_service_accepts_and_no_other_is(
    app_session: Session, role: Role, state: str
) -> None:
    """The list and the endpoint apply one test, so each state a caller is offered succeeds and each
    state they are not offered is refused, whichever reason the refusal has."""
    submitter = _user(app_session)
    actor = _user(app_session)
    offered = set(
        available_transitions(
            _principal(actor, role), from_state=SubmissionState(state), submitter_id=submitter.id
        )
    )

    for target in SubmissionState:
        if target.value == state:
            continue
        submission = _submission(app_session, submitter, state=state)
        savepoint = app_session.begin_nested()
        try:
            _move(app_session, submission, actor, role, target.value)
        except IllegalSubmissionTransitionError, PermissionDeniedError:
            assert target not in offered
        except TransitionNeedsOwnRouteError:
            # Offered, and made through its own route: the general one refuses it once the caller
            # has been judged.
            assert target in offered
            assert PERMITTED_TRANSITIONS[(SubmissionState(state), target)].needs_payload
        else:
            assert target in offered
        finally:
            savepoint.rollback()


@pytest.mark.req("FR-44")
def test_the_route_gate_is_every_permission_the_table_names() -> None:
    named = {
        Permission.SUBMISSION_TRANSITION_REVIEW,
        Permission.SUBMISSION_TRANSITION_APPROVE,
        Permission.SUBMISSION_WITHDRAW_OWN,
        Permission.SUBMISSION_WITHDRAW_ANY,
    }
    assert named == TRANSITION_PERMISSIONS


# --- a move that is not allowed (FR-28) ----------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Withdrawn", "Rejected"])
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
def test_the_table_holds_exactly_the_moves_of_the_review_graph() -> None:
    S = SubmissionState
    assert set(PERMITTED_TRANSITIONS) == {
        (S.SUBMITTED, S.TRIAGE),
        (S.TRIAGE, S.UNDER_REVIEW),
        (S.TRIAGE, S.REJECTED),
        (S.REJECTED, S.TRIAGE),
        (S.UNDER_REVIEW, S.READY_FOR_APPROVAL),
        (S.UNDER_REVIEW, S.AWAITING_TERMINOLOGY),
        (S.AWAITING_TERMINOLOGY, S.READY_FOR_APPROVAL),
        (S.AWAITING_TERMINOLOGY, S.UNDER_REVIEW),
        (S.READY_FOR_APPROVAL, S.APPROVED),
        (S.SUBMITTED, S.WITHDRAWN),
        (S.TRIAGE, S.WITHDRAWN),
        (S.APPROVED, S.WITHDRAWN),
        (S.PUBLISHED_IN_RELEASE, S.WITHDRAWN),
    }


@pytest.mark.req("FR-30")
def test_only_the_moves_of_awaiting_terminology_and_approval_need_their_own_route() -> None:
    S = SubmissionState
    assert {move for move, row in PERMITTED_TRANSITIONS.items() if row.needs_payload} == {
        (S.UNDER_REVIEW, S.AWAITING_TERMINOLOGY),
        (S.AWAITING_TERMINOLOGY, S.READY_FOR_APPROVAL),
        (S.READY_FOR_APPROVAL, S.APPROVED),
    }


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-44")
def test_only_an_administrator_permission_takes_a_submission_out_of_awaiting_terminology() -> None:
    row = PERMITTED_TRANSITIONS[
        (SubmissionState.AWAITING_TERMINOLOGY, SubmissionState.READY_FOR_APPROVAL)
    ]

    assert [requirement.permission for requirement in row.requirements] == [
        Permission.SUBMISSION_TRANSITION_APPROVE
    ]
    assert Permission.SUBMISSION_TRANSITION_APPROVE in ADMINISTRATOR_ONLY


@pytest.mark.req("FR-28")
def test_a_withdrawn_submission_has_no_way_out() -> None:
    assert SubmissionState.WITHDRAWN not in {source for source, _ in PERMITTED_TRANSITIONS}


# --- who may move it (FR-44) ---------------------------------------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_another_member_cannot_withdraw_someone_elses_submission(app_session: Session) -> None:
    submitter = _user(app_session)
    other = _user(app_session)
    submission = _submission(app_session, submitter)

    with pytest.raises(PermissionDeniedError, match="only the submitter may use it"):
        _withdraw(app_session, submitter, submission, actor=other)

    _assert_nothing_written(app_session, submission.id, "Submitted")


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_role_with_neither_withdraw_permission_cannot_withdraw_even_its_own(
    app_session: Session,
) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)
    held = permissions_for_roles(frozenset({Role.OBSERVER}))
    assert Permission.SUBMISSION_WITHDRAW_OWN not in held
    assert Permission.SUBMISSION_WITHDRAW_ANY not in held

    with pytest.raises(PermissionDeniedError, match="is required for this move"):
        _withdraw(app_session, submitter, submission, roles=(Role.OBSERVER,))

    _assert_nothing_written(app_session, submission.id, "Submitted")


@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Submitted", "Triage"])
def test_an_administrator_withdraws_their_own_submission(app_session: Session, state: str) -> None:
    """The Administrator holds `submission.withdraw.any` and not `.own`, so the `.any` requirement on
    the open-state rows is what lets them withdraw a submission of their own."""
    admin = _user(app_session)
    submission = _submission(app_session, admin, state=state)
    assert Permission.SUBMISSION_WITHDRAW_OWN not in permissions_for_roles(
        frozenset({Role.ADMINISTRATOR})
    )

    result = _withdraw(app_session, admin, submission, roles=(Role.ADMINISTRATOR,))

    assert result.state == "Withdrawn"
    [row] = _transitions(app_session, submission.id)
    assert (row.from_state, row.to_state, row.actor_id) == (state, "Withdrawn", admin.id)


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Submitted", "Triage", "Approved", "Published in release"])
def test_an_administrator_withdraws_someone_elses_submission(
    app_session: Session, state: str
) -> None:
    submitter = _user(app_session)
    admin = _user(app_session)
    submission = _submission(app_session, submitter, state=state)

    result = _withdraw(app_session, submitter, submission, actor=admin, roles=(Role.ADMINISTRATOR,))

    assert result.state == "Withdrawn"
    [row] = _transitions(app_session, submission.id)
    assert (row.from_state, row.to_state, row.actor_id) == (state, "Withdrawn", admin.id)
    [event] = _events(app_session, submission.id)
    assert event.action == "submission.withdrawn"
    assert event.actor_user_id == admin.id


@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", [Role.PROVISIONAL, Role.MEMBER, Role.REVIEWER])
@pytest.mark.parametrize("state", ["Approved", "Published in release"])
def test_the_submitter_cannot_withdraw_once_the_submission_is_approved(
    app_session: Session, role: Role, state: str
) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter, state=state)

    with pytest.raises(PermissionDeniedError, match=r"submission\.withdraw\.any"):
        _withdraw(app_session, submitter, submission, roles=(role,))

    _assert_nothing_written(app_session, submission.id, state)


@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Approved", "Published in release"])
def test_a_reviewer_cannot_withdraw_someone_elses_approved_submission(
    app_session: Session, state: str
) -> None:
    submitter = _user(app_session)
    reviewer = _user(app_session)
    submission = _submission(app_session, submitter, state=state)

    with pytest.raises(PermissionDeniedError):
        _withdraw(app_session, submitter, submission, actor=reviewer, roles=(Role.REVIEWER,))

    _assert_nothing_written(app_session, submission.id, state)


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
