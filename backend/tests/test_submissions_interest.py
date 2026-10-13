"""`nptc.submissions.interest` service-layer tests (FR-32, FR-34, NFR-08, NFR-17).

Each refusal asserts that no interest row and no audit event was added. `backend/tests` shares one
Postgres container (see `CLAUDE.md`), so every query is scoped to the rows this test created, and an
audit event is found by the id of the interest record it describes. The race test uses two real
connections, because one session running the code twice cannot reproduce two transactions.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.db.models.audit import AuditEvent
from nptc.db.models.submission import CLOSED_SUBMISSION_STATES, Submission, SubmissionKind
from nptc.db.models.submission_interest import SubmissionInterest
from nptc.db.models.user import User
from nptc.submissions.errors import (
    ClosedSubmissionInterestError,
    OwnSubmissionInterestError,
    SubmissionNotFoundError,
)
from nptc.submissions.interest import (
    InterestSummary,
    interest_summaries,
    register_interest,
    revoke_interest,
)

_PROFILE_ORGANISATION = "Profile Pathology"


def _context(user_id: uuid.UUID) -> AuditContext:
    return AuditContext(
        actor_user_id=user_id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )


def _user(session: Session, *, organisation: str | None = _PROFILE_ORGANISATION) -> User:
    user = User(
        username=f"interest-{uuid.uuid4()}",
        display_name="Interest Tester",
        organisation=organisation,
    )
    session.add(user)
    session.flush()
    return user


def _submission(session: Session, submitter: User, *, state: str = "Submitted") -> Submission:
    submission = Submission(
        kind=SubmissionKind.NEW_TEST.value,
        state=state,
        preferred_term=f"Interest submission {uuid.uuid4()}",
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=submitter.id,
        organisation="Submitter Lab",
    )
    session.add(submission)
    session.flush()
    return submission


def _register(
    session: Session, user: User, submission: Submission, *, organisation: str | None = None
) -> bool:
    return register_interest(
        session,
        _context(user.id),
        submission_id=submission.id,
        profile_organisation=organisation if organisation is not None else user.organisation,
    )


def _revoke(session: Session, user: User, submission: Submission) -> bool:
    return revoke_interest(session, _context(user.id), submission_id=submission.id)


def _rows(session: Session, submission: Submission, user: User) -> list[SubmissionInterest]:
    """Revoked records first and the live one last. `created_at` cannot order them, because a
    test's whole transaction shares one `now()`."""
    return list(
        session.execute(
            select(SubmissionInterest)
            .where(
                SubmissionInterest.submission_id == submission.id,
                SubmissionInterest.user_id == user.id,
            )
            .order_by(SubmissionInterest.revoked_at.asc().nulls_last())
        ).scalars()
    )


def _live(session: Session, submission: Submission, user: User) -> list[SubmissionInterest]:
    return [row for row in _rows(session, submission, user) if row.revoked_at is None]


def _events(session: Session, user: User) -> list[AuditEvent]:
    """Every interest event the user caused, oldest first. The user is this test's own, so another
    test's events never appear."""
    return list(
        session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.actor_user_id == user.id,
                AuditEvent.entity_type == "submission_interest",
            )
            .order_by(AuditEvent.sequence)
        ).scalars()
    )


# --- registering (FR-32) -------------------------------------------------------------------


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_registering_stores_a_live_record_with_a_copy_of_the_profile_organisation(
    app_session: Session,
) -> None:
    member = _user(app_session)
    submission = _submission(app_session, _user(app_session))

    changed = _register(app_session, member, submission)

    assert changed is True
    [row] = _live(app_session, submission, member)
    assert row.organisation == _PROFILE_ORGANISATION
    assert row.revoked_at is None


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_registering_twice_leaves_one_live_record_and_one_audit_event(app_session: Session) -> None:
    member = _user(app_session)
    submission = _submission(app_session, _user(app_session))
    _register(app_session, member, submission)

    again = _register(app_session, member, submission)

    assert again is False
    assert len(_rows(app_session, submission, member)) == 1
    assert [event.action for event in _events(app_session, member)] == [
        "submission_interest.registered"
    ]


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_registering_after_a_revocation_adds_a_new_record_with_a_fresh_organisation_copy(
    app_session: Session,
) -> None:
    member = _user(app_session, organisation="First Lab")
    submission = _submission(app_session, _user(app_session))
    _register(app_session, member, submission)
    _revoke(app_session, member, submission)
    member.organisation = "Second Lab"
    app_session.flush()

    changed = _register(app_session, member, submission)

    assert changed is True
    first, second = _rows(app_session, submission, member)
    assert (first.organisation, first.revoked_at is not None) == ("First Lab", True)
    assert (second.organisation, second.revoked_at) == ("Second Lab", None)


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_a_submitter_cannot_register_interest_in_their_own_submission(
    app_session: Session,
) -> None:
    submitter = _user(app_session)
    submission = _submission(app_session, submitter)

    with pytest.raises(OwnSubmissionInterestError):
        _register(app_session, submitter, submission)

    assert _rows(app_session, submission, submitter) == []
    assert _events(app_session, submitter) == []


@pytest.mark.req("FR-32")
@pytest.mark.integration
@pytest.mark.parametrize("state", CLOSED_SUBMISSION_STATES)
def test_registering_in_a_closed_submission_is_refused(app_session: Session, state: str) -> None:
    member = _user(app_session)
    submission = _submission(app_session, _user(app_session), state=state)

    with pytest.raises(ClosedSubmissionInterestError) as raised:
        _register(app_session, member, submission)

    assert raised.value.state == state
    assert _rows(app_session, submission, member) == []
    assert _events(app_session, member) == []


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_registering_in_an_unknown_submission_is_refused(app_session: Session) -> None:
    member = _user(app_session)

    with pytest.raises(SubmissionNotFoundError):
        register_interest(
            app_session,
            _context(member.id),
            submission_id=uuid.uuid4(),
            profile_organisation=None,
        )

    assert _events(app_session, member) == []


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_a_context_with_no_actor_is_refused(app_session: Session) -> None:
    submission = _submission(app_session, _user(app_session))

    with pytest.raises(ValueError, match="audit context has none"):
        register_interest(
            app_session,
            AuditContext.system(),
            submission_id=submission.id,
            profile_organisation=None,
        )


# --- revoking (FR-32) ----------------------------------------------------------------------


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_revoking_ends_the_live_record_and_keeps_the_row(app_session: Session) -> None:
    member = _user(app_session)
    submission = _submission(app_session, _user(app_session))
    _register(app_session, member, submission)

    changed = _revoke(app_session, member, submission)

    assert changed is True
    [row] = _rows(app_session, submission, member)
    assert row.revoked_at is not None
    assert row.organisation == _PROFILE_ORGANISATION
    assert _live(app_session, submission, member) == []


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_revoking_twice_changes_nothing_and_adds_no_audit_event(app_session: Session) -> None:
    member = _user(app_session)
    submission = _submission(app_session, _user(app_session))
    _register(app_session, member, submission)
    _revoke(app_session, member, submission)
    [row] = _rows(app_session, submission, member)
    revoked_at = row.revoked_at

    again = _revoke(app_session, member, submission)

    assert again is False
    assert [row.revoked_at for row in _rows(app_session, submission, member)] == [revoked_at]
    assert len(_events(app_session, member)) == 2


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_revoking_without_ever_registering_is_a_no_op(app_session: Session) -> None:
    member = _user(app_session)
    submission = _submission(app_session, _user(app_session))

    changed = _revoke(app_session, member, submission)

    assert changed is False
    assert _rows(app_session, submission, member) == []
    assert _events(app_session, member) == []


@pytest.mark.req("FR-32")
@pytest.mark.integration
@pytest.mark.parametrize("state", CLOSED_SUBMISSION_STATES)
def test_revoking_in_a_closed_submission_is_refused_and_the_count_stays(
    app_session: Session, state: str
) -> None:
    """The record is made while the submission is open, then the submission closes. The count that
    stood at closure must not move."""
    member = _user(app_session)
    submission = _submission(app_session, _user(app_session))
    _register(app_session, member, submission)
    submission.state = state
    app_session.flush()

    with pytest.raises(ClosedSubmissionInterestError):
        _revoke(app_session, member, submission)

    assert len(_live(app_session, submission, member)) == 1
    assert len(_events(app_session, member)) == 1


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_revoking_in_an_unknown_submission_is_refused(app_session: Session) -> None:
    member = _user(app_session)

    with pytest.raises(SubmissionNotFoundError):
        revoke_interest(app_session, _context(member.id), submission_id=uuid.uuid4())


# --- audit (NFR-08) ------------------------------------------------------------------------


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_each_change_is_audited_and_the_organisation_never_appears(app_session: Session) -> None:
    member = _user(app_session, organisation="Distinctive Pathology Name")
    submission = _submission(app_session, _user(app_session))

    _register(app_session, member, submission)
    _revoke(app_session, member, submission)
    _register(app_session, member, submission)

    events = _events(app_session, member)
    assert [event.action for event in events] == [
        "submission_interest.registered",
        "submission_interest.revoked",
        "submission_interest.registered",
    ]
    registered, revoked, _ = events
    assert registered.after is not None
    assert registered.after["submission_id"] == str(submission.id)
    assert registered.after["user_id"] == str(member.id)
    assert revoked.before is not None and revoked.after is not None
    assert revoked.before["revoked_at"] is None
    assert revoked.after["revoked_at"] is not None
    assert "Distinctive Pathology Name" not in str([dict(vars(event)) for event in events])


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_an_event_names_the_user_who_acted(app_session: Session) -> None:
    member = _user(app_session)
    submission = _submission(app_session, _user(app_session))

    _register(app_session, member, submission)

    [event] = _events(app_session, member)
    assert event.actor_user_id == member.id


# --- counts (FR-34) ------------------------------------------------------------------------


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_the_count_excludes_revoked_records_and_includes_live_ones_on_a_closed_submission(
    app_session: Session,
) -> None:
    submitter = _user(app_session)
    open_submission = _submission(app_session, submitter)
    later_closed = _submission(app_session, submitter)
    members = [_user(app_session) for _ in range(3)]
    for member in members:
        _register(app_session, member, open_submission)
    _revoke(app_session, members[0], open_submission)
    _register(app_session, members[1], later_closed)
    later_closed.state = CLOSED_SUBMISSION_STATES[0]
    app_session.flush()

    summaries = interest_summaries(
        app_session, [open_submission.id, later_closed.id], viewer_id=None
    )

    assert summaries[open_submission.id].count == 2
    assert summaries[later_closed.id].count == 1


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_a_summary_says_whether_the_viewer_holds_live_interest(app_session: Session) -> None:
    submitter = _user(app_session)
    held, revoked, never = (_submission(app_session, submitter) for _ in range(3))
    viewer, other = _user(app_session), _user(app_session)
    _register(app_session, viewer, held)
    _register(app_session, viewer, revoked)
    _revoke(app_session, viewer, revoked)
    _register(app_session, other, never)

    summaries = interest_summaries(
        app_session, [held.id, revoked.id, never.id], viewer_id=viewer.id
    )

    assert summaries[held.id] == InterestSummary(count=1, registered=True)
    assert summaries[revoked.id] == InterestSummary(count=0, registered=False)
    assert summaries[never.id] == InterestSummary(count=1, registered=False)


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_a_submission_with_no_interest_has_a_zero_summary_and_none_is_none_at_all(
    app_session: Session,
) -> None:
    submission = _submission(app_session, _user(app_session))

    assert interest_summaries(app_session, [submission.id], viewer_id=None) == {
        submission.id: InterestSummary(count=0, registered=False)
    }
    assert interest_summaries(app_session, [], viewer_id=None) == {}


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_summaries_for_many_submissions_cost_one_statement(
    app_session: Session, app_db: Connection, capture_statements: Any
) -> None:
    submitter = _user(app_session)
    submissions = [_submission(app_session, submitter) for _ in range(5)]
    _register(app_session, _user(app_session), submissions[0])
    ids = [submission.id for submission in submissions]

    with capture_statements(
        app_db, lambda statement, _: "submission_interest" in statement
    ) as seen:
        interest_summaries(app_session, ids, viewer_id=submitter.id)

    assert len(seen) == 1


# --- concurrency ---------------------------------------------------------------------------


@pytest.fixture
def racing_pair(
    pristine_audit_event: None, app_engine: Engine, owner_engine: Engine
) -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    """A committed submission and a committed member who may register interest in it. Declared
    after `pristine_audit_event`, so this teardown runs first and clears the rows that block its
    wipe of the user table, which then removes the two users."""
    with Session(app_engine) as setup:
        submitter, member = _user(setup), _user(setup)
        submission = _submission(setup, submitter)
        submission_id, member_id = submission.id, member.id
        setup.commit()
    yield submission_id, member_id
    with owner_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM submission_interest WHERE submission_id = :s"), {"s": submission_id}
        )
        connection.execute(text("DELETE FROM submission WHERE id = :s"), {"s": submission_id})


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_two_concurrent_registrations_by_one_user_leave_one_live_record(
    racing_pair: tuple[uuid.UUID, uuid.UUID], app_engine: Engine
) -> None:
    """Both transactions would find no live record and insert. The append lock makes the second
    wait, then see the first's commit and return without writing, so neither meets the unique
    index and exactly one reports a change."""
    submission_id, member_id = racing_pair
    barrier = threading.Barrier(2)
    results: dict[str, bool] = {}
    errors: dict[str, BaseException] = {}

    def attempt(name: str) -> None:
        session = Session(app_engine)
        try:
            barrier.wait(timeout=5)
            results[name] = register_interest(
                session,
                _context(member_id),
                submission_id=submission_id,
                profile_organisation=_PROFILE_ORGANISATION,
            )
            session.commit()
        except BaseException as exc:  # surfaced below, not swallowed
            session.rollback()
            errors[name] = exc
        finally:
            session.close()

    threads = [threading.Thread(target=attempt, args=(name,)) for name in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert not any(thread.is_alive() for thread in threads), "a request hung: looks like a deadlock"
    assert errors == {}
    assert sorted(results.values()) == [False, True]
    with Session(app_engine) as check:
        live = check.execute(
            select(func.count())
            .select_from(SubmissionInterest)
            .where(
                SubmissionInterest.submission_id == submission_id,
                SubmissionInterest.user_id == member_id,
                SubmissionInterest.revoked_at.is_(None),
            )
        ).scalar_one()
    assert live == 1


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_a_revocation_that_started_before_a_registration_committed_still_succeeds(
    racing_pair: tuple[uuid.UUID, uuid.UUID], app_engine: Engine
) -> None:
    """A transaction's `now()` is the time of its first statement, which in a request is the
    principal lookup, before the lock is taken. A revocation that began earlier than a registration
    that then committed first must still be stamped later than that registration's `created_at`,
    or the `revoked_after_created` check fails and the caller gets a 500."""
    submission_id, member_id = racing_pair
    revoking = Session(app_engine)
    try:
        revoking.execute(select(1))
        with Session(app_engine) as registering:
            assert register_interest(
                registering,
                _context(member_id),
                submission_id=submission_id,
                profile_organisation=_PROFILE_ORGANISATION,
            )
            registering.commit()

        assert revoke_interest(revoking, _context(member_id), submission_id=submission_id)
        revoking.commit()
    finally:
        revoking.close()

    with Session(app_engine) as check:
        revoked_at = check.execute(
            select(SubmissionInterest.revoked_at).where(
                SubmissionInterest.submission_id == submission_id
            )
        ).scalar_one()
    assert revoked_at is not None
