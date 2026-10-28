"""`GET /api/v1/submissions/{id}/transitions` (FR-28, FR-42, FR-44, NFR-04, NFR-08, NFR-17).

The real app over a stub identity provider (`api_app_support`). Only the withdraw route writes a
transition today, so every real actor is the submitter. Reviewer-made rows are inserted directly, as
the submissions themselves are. `backend/tests` shares one Postgres container, so every assertion is
scoped to the submission this test created.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from aptc.audit.writer import AuditContext
from aptc.auth.identity import close_account
from aptc.auth.permissions import Role
from aptc.db.models.audit import AuditEvent
from aptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from aptc.db.models.submission_transition import SubmissionTransition
from aptc.db.models.user import User, UserStatus
from aptc.db.models.user_identity import UserIdentity


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_api_support = _load("api_app_support")
build_api_test_app = _api_support.build_api_test_app
ApiTestApp = _api_support.ApiTestApp

_PLACEHOLDER = "Closed account"
_ACTOR_DISPLAY_NAME = "Rita Reviewer"
_ACTOR_ORGANISATION = "Profile Pathology"
_IDENTITY_KEYS = {"display_name", "organisation"}
_IDENTITY_ROLES = [Role.REVIEWER, Role.ADMINISTRATOR]
_PLAIN_ROLES = [Role.OBSERVER, Role.PROVISIONAL, Role.MEMBER]
_ITEM_KEYS = {"from_state", "to_state", "reason", "created_at", "actor"}


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _sign_in(api: ApiTestApp, role: Role) -> tuple[str, User]:
    """A token holding exactly `role`, and the user it belongs to. The user has a display name and a
    profile organisation, so a response that leaked either to the wrong caller would show it."""
    subject = f"sub-transitions-{uuid.uuid4().hex[:10]}"
    token = api.token_for_role(
        subject=subject,
        role=role,
        with_mfa=role is Role.ADMINISTRATOR,
        replace_roles=True,
        email=f"{subject}@example.test",
    )
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    user.display_name = _ACTOR_DISPLAY_NAME
    user.organisation = _ACTOR_ORGANISATION
    api.session.flush()
    return token, user


def _submission(api: ApiTestApp, submitter: User, *, state: str = "Triage") -> Submission:
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
    api.session.add(submission)
    api.session.flush()
    return submission


def _transition(
    api: ApiTestApp,
    submission: Submission,
    actor: User,
    *,
    from_state: str,
    to_state: str,
    reason: str = "Moved by the working group",
    created_at: datetime | None = None,
) -> SubmissionTransition:
    row = SubmissionTransition(
        submission_id=submission.id,
        from_state=from_state,
        to_state=to_state,
        actor_id=actor.id,
        reason=reason,
        created_at=created_at or datetime.now(UTC),
    )
    api.session.add(row)
    api.session.flush()
    return row


def _history(api: ApiTestApp, token: str | None, submission_id: object) -> Any:
    return api.get(f"/submissions/{submission_id}/transitions", token=token)


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _all_keys(item)}
    return set()


def _events(api: ApiTestApp, submission: Submission) -> int:
    return int(
        api.session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.entity_type == "submission", AuditEvent.entity_id == str(submission.id)
            )
        )
        or 0
    )


# --- what the history holds (FR-28) --------------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_history_is_oldest_first_and_each_item_has_the_five_keys(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)
    _, submitter = _sign_in(api, Role.MEMBER)
    _, reviewer = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, submitter, state=SubmissionState.SUBMITTED.value)
    now = datetime.now(UTC).replace(microsecond=0)
    # Inserted out of order, so only the read's ordering can put them right.
    _transition(
        api,
        submission,
        reviewer,
        from_state="Triage",
        to_state="Rejected",
        reason="Second move",
        created_at=now,
    )
    _transition(
        api,
        submission,
        reviewer,
        from_state="Submitted",
        to_state="Triage",
        reason="First move",
        created_at=now - timedelta(hours=2),
    )

    response = _history(api, token, submission.id)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [(i["from_state"], i["to_state"], i["reason"]) for i in items] == [
        ("Submitted", "Triage", "First move"),
        ("Triage", "Rejected", "Second move"),
    ]
    assert all(set(item) == _ITEM_KEYS for item in items)
    assert set(response.json()) == {"items"}


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_two_rows_written_in_one_instant_keep_a_stable_order(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)
    _, submitter = _sign_in(api, Role.MEMBER)
    _, reviewer = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, submitter, state=SubmissionState.SUBMITTED.value)
    tied = datetime.now(UTC).replace(microsecond=0)
    rows = [
        _transition(
            api, submission, reviewer, from_state="Submitted", to_state="Triage", created_at=tied
        ),
        _transition(
            api, submission, reviewer, from_state="Triage", to_state="Rejected", created_at=tied
        ),
    ]
    expected = [row.to_state for row in sorted(rows, key=lambda row: row.id)]

    first = [i["to_state"] for i in _history(api, token, submission.id).json()["items"]]
    second = [i["to_state"] for i in _history(api, token, submission.id).json()["items"]]

    assert first == second == expected


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_submission_with_no_transitions_is_a_200_with_an_empty_list(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter, state=SubmissionState.SUBMITTED.value)

    response = _history(api, token, submission.id)

    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_row_for_another_submission_is_not_in_the_history(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)
    _, submitter = _sign_in(api, Role.MEMBER)
    _, reviewer = _sign_in(api, Role.REVIEWER)
    mine = _submission(api, submitter)
    other = _submission(api, submitter)
    _transition(api, other, reviewer, from_state="Submitted", to_state="Triage", reason="Not mine")

    assert _history(api, token, mine.id).json() == {"items": []}


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_withdraw_route_writes_a_row_the_history_then_shows(api: ApiTestApp) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter, state=SubmissionState.SUBMITTED.value)
    withdrawn = api.post(
        f"/submissions/{submission.id}/withdraw",
        token=token,
        json={"reason": "Entered by mistake", "expected_row_version": 1},
    )
    assert withdrawn.status_code == 200, withdrawn.text

    response = _history(api, token, submission.id)

    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert (item["from_state"], item["to_state"]) == ("Submitted", "Withdrawn")
    assert item["reason"] == "Entered by mistake"
    assert item["actor"] == {"username": submitter.username}


# --- the identity rule (FR-42) -------------------------------------------------------------


@pytest.mark.req("FR-42")
@pytest.mark.integration
@pytest.mark.parametrize("role", _IDENTITY_ROLES)
def test_a_caller_with_the_identity_permission_sees_the_actors_name_and_organisation(
    api: ApiTestApp, role: Role
) -> None:
    token, _ = _sign_in(api, role)
    _, submitter = _sign_in(api, Role.MEMBER)
    _, reviewer = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, submitter, state=SubmissionState.SUBMITTED.value)
    _transition(api, submission, reviewer, from_state="Submitted", to_state="Triage")

    response = _history(api, token, submission.id)

    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert item["actor"] == {
        "username": reviewer.username,
        "display_name": _ACTOR_DISPLAY_NAME,
        "organisation": _ACTOR_ORGANISATION,
    }


@pytest.mark.req("FR-42")
@pytest.mark.integration
@pytest.mark.parametrize("role", _PLAIN_ROLES)
def test_a_caller_without_the_identity_permission_sees_the_username_and_no_other_key(
    api: ApiTestApp, role: Role
) -> None:
    token, _ = _sign_in(api, role)
    _, submitter = _sign_in(api, Role.MEMBER)
    _, reviewer = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, submitter, state=SubmissionState.SUBMITTED.value)
    _transition(
        api, submission, reviewer, from_state="Submitted", to_state="Triage", reason="Looks valid"
    )

    response = _history(api, token, submission.id)

    assert response.status_code == 200, response.text
    [item] = response.json()["items"]
    assert item["actor"] == {"username": reviewer.username}
    assert not _all_keys(response.json()) & _IDENTITY_KEYS
    assert _ACTOR_DISPLAY_NAME not in response.text
    assert _ACTOR_ORGANISATION not in response.text
    assert item["reason"] == "Looks valid"


@pytest.mark.req("FR-42")
@pytest.mark.integration
def test_a_submitter_reads_the_reasons_on_their_own_submission(api: ApiTestApp) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    _, reviewer = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, submitter, state=SubmissionState.SUBMITTED.value)
    _transition(
        api,
        submission,
        reviewer,
        from_state="Submitted",
        to_state="Rejected",
        reason="Already in the catalogue",
    )

    [item] = _history(api, token, submission.id).json()["items"]

    assert item["reason"] == "Already in the catalogue"
    assert item["actor"] == {"username": reviewer.username}


@pytest.mark.req("NFR-04")
@pytest.mark.integration
@pytest.mark.parametrize("role", [Role.MEMBER, Role.REVIEWER])
def test_no_internal_id_is_in_the_response(api: ApiTestApp, role: Role) -> None:
    token, _ = _sign_in(api, role)
    _, submitter = _sign_in(api, Role.MEMBER)
    _, reviewer = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, submitter, state=SubmissionState.SUBMITTED.value)
    _transition(api, submission, reviewer, from_state="Submitted", to_state="Triage")

    response = _history(api, token, submission.id)

    assert str(reviewer.id) not in response.text
    assert str(submission.id) not in response.text
    assert "id" not in _all_keys(response.json())


# --- a closed account (NFR-17) -------------------------------------------------------------


@pytest.mark.req("NFR-17")
@pytest.mark.integration
def test_a_closed_account_actor_shows_the_placeholder_and_keeps_the_row(
    api: ApiTestApp,
) -> None:
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    member_token, _ = _sign_in(api, Role.MEMBER)
    _, submitter = _sign_in(api, Role.MEMBER)
    _, actor = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, submitter, state=SubmissionState.SUBMITTED.value)
    _transition(api, submission, actor, from_state="Submitted", to_state="Triage", reason="Kept")
    close_account(api.session, actor.id, AuditContext.system())
    api.session.flush()

    for_reviewer = _history(api, reviewer_token, submission.id)
    for_member = _history(api, member_token, submission.id)

    assert for_reviewer.status_code == 200, for_reviewer.text
    assert for_reviewer.json()["items"][0]["actor"] == {
        "username": _PLACEHOLDER,
        "display_name": _PLACEHOLDER,
        "organisation": None,
    }
    assert for_reviewer.json()["items"][0]["reason"] == "Kept"
    assert for_member.json()["items"][0]["actor"] == {"username": _PLACEHOLDER}


# --- no write, no extra reads (NFR-08) -----------------------------------------------------


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_read_writes_no_transition_and_no_audit_event(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)
    events_before = _events(api, submission)

    assert _history(api, token, submission.id).status_code == 200

    assert _events(api, submission) == events_before
    assert (
        api.session.scalar(
            select(func.count())
            .select_from(SubmissionTransition)
            .where(SubmissionTransition.submission_id == submission.id)
        )
        == 0
    )


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_history_costs_the_same_statements_however_many_rows_it_has(
    api: ApiTestApp, app_db: Connection, capture_statements: Any
) -> None:
    """Every statement the request runs is counted, not only those that name `submission_transition`:
    a per-row lookup of the actor would query `app_user` alone and slip past that filter. Each row
    has its own actor, so such a lookup would add a statement for every row."""
    token, _ = _sign_in(api, Role.REVIEWER)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter, state=SubmissionState.SUBMITTED.value)
    assert _history(api, token, submission.id).status_code == 200

    def statements() -> tuple[int, int]:
        """The statements the whole request runs, and those that read the transition table."""
        with capture_statements(app_db) as seen:
            assert _history(api, token, submission.id).status_code == 200
        return len(seen), sum("FROM submission_transition" in statement for statement in seen)

    base = datetime.now(UTC).replace(microsecond=0)
    counts: list[tuple[int, int]] = []
    for step, (before, after) in enumerate(
        [("Submitted", "Triage"), ("Triage", "Submitted"), ("Submitted", "Triage")]
    ):
        _, actor = _sign_in(api, Role.REVIEWER)
        _transition(
            api,
            submission,
            actor,
            from_state=before,
            to_state=after,
            created_at=base + timedelta(minutes=step),
        )
        counts.append(statements())

    assert counts[0] == counts[1] == counts[2]
    assert counts[0][1] == 1


# --- who may read (FR-44) ------------------------------------------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_unknown_submission_is_a_404_that_does_not_echo_the_id(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)
    unknown = uuid.uuid4()

    response = _history(api, token, unknown)

    assert response.status_code == 404, response.text
    assert str(unknown) not in response.text


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_identifier_that_is_not_a_uuid_is_a_422(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)

    assert _history(api, token, "not-a-uuid").status_code == 422


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_anonymous_caller_is_refused_with_401(api: ApiTestApp) -> None:
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    response = _history(api, None, submission.id)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_caller_without_the_view_permission_is_refused_with_403(api: ApiTestApp) -> None:
    """A suspended account keeps only the anonymous read surface, which has no `submission.view`.
    The refusal comes before the submission is looked up, so an unknown id is a 403 as well."""
    token, user = _sign_in(api, Role.MEMBER)
    submission = _submission(api, user)
    user.status = UserStatus.SUSPENDED.value
    api.session.flush()

    for target in (submission.id, uuid.uuid4()):
        response = _history(api, token, target)
        assert response.status_code == 403, response.text
