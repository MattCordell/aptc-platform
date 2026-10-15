"""`POST /api/v1/submissions/{id}/transitions` (FR-28, FR-44, FR-63, FR-81, NFR-08, NFR-45).

The real app over a stub identity provider (`api_app_support`). Submissions are inserted directly,
so a test can start a submission in any state. Approval has its own file. `backend/tests` shares
one Postgres container, so every assertion is scoped to rows this test created: transition rows are found by submission, and audit
events by the submission they describe. Every refusal asserts that nothing was written.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection

from nptc.api.terms_gate import TERMS_ACCEPTANCE_REQUIRED_CODE
from nptc.auth.permissions import Role
from nptc.db.models.audit import AuditEvent
from nptc.db.models.submission import Submission, SubmissionKind
from nptc.db.models.submission_transition import SubmissionTransition
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity


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

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_REASON = "Checked against the evidence"

_REVIEW_MOVES = [
    ("Submitted", "Triage", "submission.triaged"),
    ("Triage", "Under review", "submission.review_started"),
    ("Triage", "Rejected", "submission.rejected"),
    ("Rejected", "Triage", "submission.reopened"),
    ("Under review", "Ready for approval", "submission.readied_for_approval"),
]
_REVIEWING_ROLES = [Role.REVIEWER, Role.ADMINISTRATOR]
#: Roles that may sign in and ask, and hold no review permission.
_NON_REVIEWING_ROLES = [Role.PROVISIONAL, Role.MEMBER]


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _sign_in(api: ApiTestApp, role: Role, *, accept_terms: bool = True) -> tuple[str, User]:
    """A token holding exactly `role`, and the user it belongs to. An administrator gets the MFA
    claim the realm demands for that role. Each call gives its own email, so each is a distinct user
    (NFR-05)."""
    subject = f"sub-transition-{uuid.uuid4().hex[:10]}"
    token = api.token_for_role(
        subject=subject,
        role=role,
        with_mfa=role is Role.ADMINISTRATOR,
        replace_roles=True,
        accept_terms=accept_terms,
        email=f"{subject}@example.test",
    )
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    return token, user


def _submission(api: ApiTestApp, submitter: User, *, state: str = "Submitted") -> Submission:
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


def _move(
    api: ApiTestApp,
    token: str | None,
    submission_id: object,
    to_state: object,
    *,
    reason: object = _REASON,
    expected_row_version: object = 1,
    **extra: object,
) -> Any:
    return api.post(
        f"/submissions/{submission_id}/transitions",
        token=token,
        json={
            "to_state": to_state,
            "reason": reason,
            "expected_row_version": expected_row_version,
            **extra,
        },
    )


def _transitions(api: ApiTestApp, submission: Submission) -> list[SubmissionTransition]:
    return list(
        api.session.execute(
            select(SubmissionTransition)
            .where(SubmissionTransition.submission_id == submission.id)
            .order_by(SubmissionTransition.created_at)
        ).scalars()
    )


def _events(api: ApiTestApp, submission: Submission) -> list[AuditEvent]:
    return list(
        api.session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == "submission", AuditEvent.entity_id == str(submission.id)
            )
            .order_by(AuditEvent.sequence)
        ).scalars()
    )


def _state(api: ApiTestApp, submission: Submission) -> str:
    api.session.expire(submission)
    return submission.state


def _assert_unchanged(api: ApiTestApp, submission: Submission, state: str) -> None:
    assert _state(api, submission) == state
    assert _transitions(api, submission) == []
    assert _events(api, submission) == []


# --- the review moves (FR-28, NFR-08) ------------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REVIEWING_ROLES)
@pytest.mark.parametrize(("from_state", "to_state", "action"), _REVIEW_MOVES)
def test_a_reviewer_and_an_administrator_make_each_review_move(
    api: ApiTestApp, role: Role, from_state: str, to_state: str, action: str
) -> None:
    _, submitter = _sign_in(api, Role.MEMBER)
    token, reviewer = _sign_in(api, role)
    submission = _submission(api, submitter, state=from_state)

    response = _move(api, token, submission.id, to_state)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["id"], body["state"], body["row_version"]) == (str(submission.id), to_state, 2)
    assert _state(api, submission) == to_state
    [transition] = _transitions(api, submission)
    assert (transition.from_state, transition.to_state) == (from_state, to_state)
    assert transition.actor_id == reviewer.id
    assert transition.reason == _REASON
    [event] = _events(api, submission)
    assert event.action == action
    assert event.actor_user_id == reviewer.id
    assert event.reason == _REASON
    assert event.before == {"state": from_state}
    assert event.after == {"state": to_state}


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_reopened_submission_keeps_its_history_and_is_not_a_new_record(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    for version, to_state in enumerate(["Triage", "Rejected", "Triage"], start=1):
        response = _move(api, token, submission.id, to_state, expected_row_version=version)
        assert response.status_code == 200, response.text

    assert response.json()["state"] == "Triage"
    assert response.json()["row_version"] == 4
    assert [(row.from_state, row.to_state) for row in _transitions(api, submission)] == [
        ("Submitted", "Triage"),
        ("Triage", "Rejected"),
        ("Rejected", "Triage"),
    ]
    assert len(_events(api, submission)) == 3
    owned = api.session.execute(
        select(Submission.id).where(Submission.submitter_id == submitter.id)
    ).all()
    assert len(owned) == 1


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_rejected_submission_leaves_the_default_list_and_returns_when_reopened(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter, state="Triage")

    def listed() -> bool:
        response = api.get("/submissions", token=token, params={"limit": 200})
        assert response.status_code == 200, response.text
        return str(submission.id) in {item["id"] for item in response.json()["items"]}

    assert listed()
    assert _move(api, token, submission.id, "Rejected").status_code == 200
    assert not listed()
    assert _move(api, token, submission.id, "Triage", expected_row_version=2).status_code == 200
    assert listed()


# --- withdrawal through the general route (FR-28, FR-44) -----------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Submitted", "Triage"])
def test_an_administrator_withdraws_their_own_open_submission(api: ApiTestApp, state: str) -> None:
    token, administrator = _sign_in(api, Role.ADMINISTRATOR)
    submission = _submission(api, administrator, state=state)

    response = _move(api, token, submission.id, "Withdrawn")

    assert response.status_code == 200, response.text
    assert _state(api, submission) == "Withdrawn"
    [transition] = _transitions(api, submission)
    assert (transition.from_state, transition.to_state) == (state, "Withdrawn")


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Approved", "Published in release"])
def test_an_administrator_withdraws_an_approved_or_published_submission(
    api: ApiTestApp, state: str
) -> None:
    token, administrator = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter, state=state)

    response = _move(api, token, submission.id, "Withdrawn")

    assert response.status_code == 200, response.text
    assert _state(api, submission) == "Withdrawn"
    [transition] = _transitions(api, submission)
    assert (transition.from_state, transition.to_state) == (state, "Withdrawn")
    assert transition.actor_id == administrator.id
    [event] = _events(api, submission)
    assert event.action == "submission.withdrawn"


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_member_withdraws_their_own_submission_through_the_general_route(
    api: ApiTestApp,
) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submission = _submission(api, member)

    response = _move(api, token, submission.id, "Withdrawn")

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "Withdrawn"


# --- who may move it (FR-44, FR-81) --------------------------------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize("role", _NON_REVIEWING_ROLES)
@pytest.mark.parametrize(("from_state", "to_state", "action"), _REVIEW_MOVES)
def test_a_role_without_the_review_permission_is_refused_every_review_move(
    api: ApiTestApp, role: Role, from_state: str, to_state: str, action: str
) -> None:
    token, user = _sign_in(api, role)
    submission = _submission(api, user, state=from_state)
    assert action

    response = _move(api, token, submission.id, to_state)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers
    assert not _UUID.search(response.text)
    _assert_unchanged(api, submission, from_state)


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
def test_an_observer_is_refused_with_403_before_the_submission_is_looked_up(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.OBSERVER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    for target in (submission.id, uuid.uuid4()):
        response = _move(api, token, target, "Triage")
        assert response.status_code == 403, response.text
        for name in Role:
            assert name.value not in response.text
    _assert_unchanged(api, submission, "Submitted")


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Approved", "Published in release"])
def test_a_reviewer_is_refused_withdrawal_from_an_approved_or_published_submission(
    api: ApiTestApp, state: str
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter, state=state)

    response = _move(api, token, submission.id, "Withdrawn")

    assert response.status_code == 403, response.text
    _assert_unchanged(api, submission, state)


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Approved", "Published in release"])
def test_the_submitter_is_refused_withdrawal_once_the_submission_is_approved(
    api: ApiTestApp, state: str
) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter, state=state)

    response = _move(api, token, submission.id, "Withdrawn")

    assert response.status_code == 403, response.text
    _assert_unchanged(api, submission, state)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_member_is_refused_withdrawal_of_someone_elses_submission(api: ApiTestApp) -> None:
    _, submitter = _sign_in(api, Role.MEMBER)
    other_token, _ = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    response = _move(api, other_token, submission.id, "Withdrawn")

    assert response.status_code == 403, response.text
    _assert_unchanged(api, submission, "Submitted")


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_anonymous_caller_is_refused_with_401(api: ApiTestApp) -> None:
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _move(api, None, submission.id, "Triage")

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
    _assert_unchanged(api, submission, "Submitted")


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_reviewer_who_has_not_accepted_the_current_terms_is_refused(api: ApiTestApp) -> None:
    token, reviewer = _sign_in(api, Role.REVIEWER, accept_terms=False)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    refused = _move(api, token, submission.id, "Triage")

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == TERMS_ACCEPTANCE_REQUIRED_CODE
    _assert_unchanged(api, submission, "Submitted")

    api.accept_current_terms(reviewer.id)
    assert _move(api, token, submission.id, "Triage").status_code == 200


# --- a move that is not allowed (FR-28) ----------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REVIEWING_ROLES)
@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        ("Submitted", "Ready for approval"),
        ("Submitted", "Rejected"),
        ("Triage", "Ready for approval"),
        ("Under review", "Rejected"),
        ("Ready for approval", "Under review"),
        ("Triage", "Approved"),
        ("Published in release", "Triage"),
        ("Withdrawn", "Triage"),
    ],
)
def test_a_move_with_no_row_is_a_409_and_nothing_is_written(
    api: ApiTestApp, role: Role, from_state: str, to_state: str
) -> None:
    token, _ = _sign_in(api, role)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter, state=from_state)

    response = _move(api, token, submission.id, to_state)

    assert response.status_code == 409, response.text
    assert "not allowed" in response.json()["detail"]
    _assert_unchanged(api, submission, from_state)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_second_identical_move_is_a_409_and_adds_nothing(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)
    assert _move(api, token, submission.id, "Triage").status_code == 200

    again = _move(api, token, submission.id, "Triage", expected_row_version=2)

    assert again.status_code == 409, again.text
    assert len(_transitions(api, submission)) == 1
    assert len(_events(api, submission)) == 1


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_awaiting_terminology_is_not_reachable_from_triage(api: ApiTestApp) -> None:
    """The state is a valid value, so the request passes validation, but the graph has no move
    into it from `Triage`. The submission is untouched."""
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1], state="Triage")

    response = _move(api, token, submission.id, "Awaiting terminology")

    assert response.status_code == 409, response.text
    _assert_unchanged(api, submission, "Triage")


# --- the request (FR-28, FR-63) ------------------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_unknown_submission_is_a_404(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    unknown = uuid.uuid4()

    response = _move(api, token, unknown, "Triage")

    assert response.status_code == 404, response.text
    assert str(unknown) not in response.text


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
def test_a_blank_reason_is_a_422_that_names_the_field(api: ApiTestApp, reason: str) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _move(api, token, submission.id, "Triage", reason=reason)

    assert response.status_code == 422, response.text
    assert response.json()["field"] == "reason"
    _assert_unchanged(api, submission, "Submitted")


@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_a_reason_with_an_invisible_character_is_a_422_that_names_the_field(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    zero_width_space = chr(0x200B)

    response = _move(api, token, submission.id, "Triage", reason=f"Looks{zero_width_space}complete")

    assert response.status_code == 422, response.text
    assert response.json()["field"] == "reason"
    assert zero_width_space not in response.text
    _assert_unchanged(api, submission, "Submitted")


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize(
    "body",
    [
        {"reason": _REASON, "expected_row_version": 1},
        {"to_state": "Triage", "expected_row_version": 1},
        {"to_state": "Triage", "reason": _REASON},
        {"to_state": "Triage", "reason": _REASON, "expected_row_version": 0},
        {"to_state": "Triage", "reason": _REASON, "expected_row_version": "one"},
        {"to_state": "Triage", "reason": "x" * 2_001, "expected_row_version": 1},
        {"to_state": "Not a state", "reason": _REASON, "expected_row_version": 1},
        {"to_state": "triage", "reason": _REASON, "expected_row_version": 1},
        {"to_state": "Triage", "reason": _REASON, "expected_row_version": 1, "actor": "x"},
    ],
    ids=[
        "no-state",
        "no-reason",
        "no-version",
        "version-zero",
        "version-not-a-number",
        "reason-too-long",
        "unknown-state",
        "state-in-the-wrong-case",
        "unknown-field",
    ],
)
def test_a_malformed_request_is_a_422(api: ApiTestApp, body: dict[str, Any]) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = api.post(f"/submissions/{submission.id}/transitions", token=token, json=body)

    assert response.status_code == 422, response.text
    _assert_unchanged(api, submission, "Submitted")


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_identifier_that_is_not_a_uuid_is_a_422(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)

    assert _move(api, token, "not-a-uuid", "Triage").status_code == 422


# --- optimistic locking (FR-28) ------------------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_stale_row_version_is_a_409_that_carries_the_current_one(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _move(api, token, submission.id, "Triage", expected_row_version=7)

    assert response.status_code == 409, response.text
    assert response.json()["expected_row_version"] == 7
    assert response.json()["current_row_version"] == submission.row_version
    _assert_unchanged(api, submission, "Submitted")


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_row_version_in_the_response_is_the_one_the_next_move_needs(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    first = _move(api, token, submission.id, "Triage")
    second = _move(
        api,
        token,
        submission.id,
        "Under review",
        expected_row_version=first.json()["row_version"],
    )

    assert second.status_code == 200, second.text
    assert second.json()["row_version"] == first.json()["row_version"] + 1


# --- the detail says what the caller may do (FR-28) ----------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_every_state_the_detail_offers_is_accepted_by_the_route(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter, state="Triage")
    detail = api.get(f"/submissions/{submission.id}", token=token).json()
    offered = detail["available_transitions"]
    assert offered == ["Under review", "Rejected"]

    for to_state in offered:
        fresh = _submission(api, submitter, state="Triage")
        response = _move(api, token, fresh.id, to_state)
        assert response.status_code == 200, response.text


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_answer_lists_what_the_caller_may_do_next(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    triaged = _move(api, token, submission.id, "Triage")
    detail = api.get(f"/submissions/{submission.id}", token=token).json()

    assert triaged.json()["available_transitions"] == ["Under review", "Rejected"]
    assert triaged.json()["available_transitions"] == detail["available_transitions"]


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_withdrawal_answers_with_no_further_move(api: ApiTestApp) -> None:
    token, administrator = _sign_in(api, Role.ADMINISTRATOR)
    submission = _submission(api, administrator, state="Triage")

    withdrawn = _move(api, token, submission.id, "Withdrawn")

    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["available_transitions"] == []


# --- an Administrator who has not stepped up (FR-44, NFR-20) ---------------------------------


def _member_and_administrator(api: ApiTestApp, *, with_mfa: bool) -> tuple[str, User]:
    """A user who holds Member and Administrator. Without MFA the Administrator role is suppressed,
    so the token carries Member's permissions only and passes the route gate through `.own`."""
    subject = f"sub-transition-{uuid.uuid4().hex[:10]}"
    email = f"{subject}@example.test"
    api.token_for_role(
        subject=subject, role=Role.MEMBER, with_mfa=False, replace_roles=True, email=email
    )
    token = api.token_for_role(
        subject=subject, role=Role.ADMINISTRATOR, with_mfa=with_mfa, email=email
    )
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    return token, user


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("state", "to_state"),
    [("Approved", "Withdrawn"), ("Published in release", "Withdrawn"), ("Submitted", "Triage")],
)
def test_an_administrator_who_holds_another_role_is_told_to_step_up(
    api: ApiTestApp, state: str, to_state: str
) -> None:
    """The route gate lets them in through Member's `.own`. The move needs a permission only the
    suppressed Administrator role grants, so the refusal is the step-up challenge and not a plain
    403, and nothing is written."""
    token, user = _member_and_administrator(api, with_mfa=False)
    submission = _submission(api, user, state=state)

    response = _move(api, token, submission.id, to_state)

    assert response.status_code == 403, response.text
    assert 'error="insufficient_user_authentication"' in response.headers["WWW-Authenticate"]
    _assert_unchanged(api, submission, state)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_the_same_user_withdraws_from_approved_once_they_have_stepped_up(api: ApiTestApp) -> None:
    token, user = _member_and_administrator(api, with_mfa=True)
    submission = _submission(api, user, state="Approved")

    response = _move(api, token, submission.id, "Withdrawn")

    assert response.status_code == 200, response.text
    assert _state(api, submission) == "Withdrawn"


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_administrator_without_another_role_is_told_to_step_up_at_the_gate(
    api: ApiTestApp,
) -> None:
    subject = f"sub-transition-{uuid.uuid4().hex[:10]}"
    token = api.token_for_role(
        subject=subject,
        role=Role.ADMINISTRATOR,
        with_mfa=False,
        replace_roles=True,
        email=f"{subject}@example.test",
    )
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1], state="Approved")

    response = _move(api, token, submission.id, "Withdrawn")

    assert response.status_code == 403, response.text
    assert 'error="insufficient_user_authentication"' in response.headers["WWW-Authenticate"]
    _assert_unchanged(api, submission, "Approved")
