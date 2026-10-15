"""`POST /api/v1/submissions/{id}/withdraw` (FR-28, FR-44, FR-63, NFR-08, NFR-45).

The real app over a stub identity provider (`api_app_support`). Submissions are inserted directly.
`backend/tests` shares one Postgres container, so every assertion is scoped to rows this test
created: transition rows are found by submission, and audit events by the submission they describe.
"""

from __future__ import annotations

import importlib.util
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

_UUID = __import__("re").compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_REASON = "Entered by mistake"
#: Roles that hold `submission.withdraw.own`, so may withdraw their own submission.
_WITHDRAWER_ROLES = [Role.PROVISIONAL, Role.MEMBER, Role.REVIEWER]


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _sign_in(api: ApiTestApp, role: Role, *, accept_terms: bool = True) -> tuple[str, User]:
    """A token holding exactly `role`, and the user it belongs to. An administrator gets the MFA
    claim the realm demands for that role. Each call gives its own email, so each is a distinct user
    (NFR-05)."""
    subject = f"sub-withdraw-{uuid.uuid4().hex[:10]}"
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
        preferred_term=f"Withdraw submission {uuid.uuid4()}",
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=submitter.id,
        organisation="Submitter Lab",
    )
    api.session.add(submission)
    api.session.flush()
    return submission


def _withdraw(
    api: ApiTestApp,
    token: str | None,
    submission_id: object,
    *,
    reason: object = _REASON,
    expected_row_version: object = 1,
    **extra: object,
) -> Any:
    return api.post(
        f"/submissions/{submission_id}/withdraw",
        token=token,
        json={"reason": reason, "expected_row_version": expected_row_version, **extra},
    )


def _transitions(api: ApiTestApp, submission: Submission) -> list[SubmissionTransition]:
    return list(
        api.session.execute(
            select(SubmissionTransition).where(SubmissionTransition.submission_id == submission.id)
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


def _assert_unchanged(api: ApiTestApp, submission: Submission, state: str = "Submitted") -> None:
    assert _state(api, submission) == state
    assert _transitions(api, submission) == []
    assert _events(api, submission) == []


# --- the withdrawal (FR-28, NFR-08) --------------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Submitted", "Triage"])
def test_the_submitter_withdraws_from_an_open_state(api: ApiTestApp, state: str) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter, state=state)

    response = _withdraw(api, token, submission.id)

    assert response.status_code == 200, response.text
    assert response.json() == {"id": str(submission.id), "state": "Withdrawn", "row_version": 2}
    assert _state(api, submission) == "Withdrawn"
    [transition] = _transitions(api, submission)
    assert (transition.from_state, transition.to_state) == (state, "Withdrawn")
    assert transition.actor_id == submitter.id
    assert transition.reason == _REASON
    [event] = _events(api, submission)
    assert event.action == "submission.withdrawn"
    assert event.actor_user_id == submitter.id
    assert event.reason == _REASON


@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", _WITHDRAWER_ROLES)
def test_each_role_with_the_own_permission_withdraws_its_own_submission(
    api: ApiTestApp, role: Role
) -> None:
    token, submitter = _sign_in(api, role)
    submission = _submission(api, submitter)

    response = _withdraw(api, token, submission.id)

    assert response.status_code == 200, response.text


# --- the withdrawn submission leaves the open views (FR-28, FR-32, FR-42) ------------------


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-42")
@pytest.mark.integration
def test_a_withdrawn_submission_leaves_the_default_list_and_stays_in_the_submitters_own(
    api: ApiTestApp,
) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    def listed(**params: object) -> bool:
        response = api.get("/submissions", token=token, params={"limit": 200, **params})
        assert response.status_code == 200, response.text
        return str(submission.id) in {item["id"] for item in response.json()["items"]}

    assert listed()
    _withdraw(api, token, submission.id)

    assert not listed()
    assert listed(mine="true")
    assert listed(state="Withdrawn")
    detail = api.get(f"/submissions/{submission.id}", token=token)
    assert detail.status_code == 200, detail.text
    assert detail.json()["state"] == "Withdrawn"


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_interest_in_a_withdrawn_submission_is_refused(api: ApiTestApp) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    other_token, _ = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)
    path = f"/submissions/{submission.id}/interest"
    assert api.request("PUT", path, token=other_token).status_code == 200
    _withdraw(api, token, submission.id)

    register = api.request("PUT", path, token=_sign_in(api, Role.MEMBER)[0])
    revoke = api.request("DELETE", path, token=other_token)

    assert register.status_code == 409, register.text
    assert revoke.status_code == 409, revoke.text


# --- a move that is not allowed (FR-28) ----------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Withdrawn", "Rejected"])
def test_a_closed_submission_is_refused_with_409_and_nothing_is_written(
    api: ApiTestApp, state: str
) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter, state=state)

    response = _withdraw(api, token, submission.id)

    assert response.status_code == 409, response.text
    assert "not allowed" in response.json()["detail"]
    _assert_unchanged(api, submission, state)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_second_withdrawal_is_a_409_and_adds_nothing(api: ApiTestApp) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)
    assert _withdraw(api, token, submission.id).status_code == 200

    again = _withdraw(api, token, submission.id, expected_row_version=2)

    assert again.status_code == 409, again.text
    assert len(_transitions(api, submission)) == 1
    assert len(_events(api, submission)) == 1


# --- who may withdraw (FR-44) --------------------------------------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_another_member_is_refused_with_403_and_nothing_is_written(api: ApiTestApp) -> None:
    _, submitter = _sign_in(api, Role.MEMBER)
    other_token, _ = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    response = _withdraw(api, other_token, submission.id)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers
    assert not _UUID.search(response.text)
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_observer_is_refused_with_403_before_the_submission_is_looked_up(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.OBSERVER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    for target in (submission.id, uuid.uuid4()):
        response = _withdraw(api, token, target)
        assert response.status_code == 403, response.text
        for name in Role:
            assert name.value not in response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_the_withdraw_route_stays_closed_to_an_administrator(api: ApiTestApp) -> None:
    """The route is gated on `submission.withdraw.own`, which the Administrator lacks. They withdraw
    through `POST /submissions/{id}/transitions`, where the table's `.any` rows apply."""
    token, administrator = _sign_in(api, Role.ADMINISTRATOR)
    submission = _submission(api, administrator)

    response = _withdraw(api, token, submission.id)

    assert response.status_code == 403, response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_anonymous_caller_is_refused_with_401(api: ApiTestApp) -> None:
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _withdraw(api, None, submission.id)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
    _assert_unchanged(api, submission)


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_user_who_has_not_accepted_the_current_terms_is_refused(api: ApiTestApp) -> None:
    token, submitter = _sign_in(api, Role.MEMBER, accept_terms=False)
    submission = _submission(api, submitter)

    refused = _withdraw(api, token, submission.id)

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == TERMS_ACCEPTANCE_REQUIRED_CODE
    _assert_unchanged(api, submission)

    api.accept_current_terms(submitter.id)
    assert _withdraw(api, token, submission.id).status_code == 200


# --- the request (FR-28, FR-63) ------------------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_unknown_submission_is_a_404(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)
    unknown = uuid.uuid4()

    response = _withdraw(api, token, unknown)

    assert response.status_code == 404, response.text
    assert str(unknown) not in response.text


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
def test_a_blank_reason_is_a_422_that_names_the_field(api: ApiTestApp, reason: str) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    response = _withdraw(api, token, submission.id, reason=reason)

    assert response.status_code == 422, response.text
    assert response.json()["field"] == "reason"
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_a_reason_with_an_invisible_character_is_a_422_that_names_the_field(
    api: ApiTestApp,
) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)
    zero_width_space = chr(0x200B)

    response = _withdraw(api, token, submission.id, reason=f"Entered{zero_width_space}in error")

    assert response.status_code == 422, response.text
    assert response.json()["field"] == "reason"
    assert zero_width_space not in response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize(
    "body",
    [
        {"expected_row_version": 1},
        {"reason": _REASON},
        {"reason": _REASON, "expected_row_version": 0},
        {"reason": _REASON, "expected_row_version": "one"},
        {"reason": "x" * 2_001, "expected_row_version": 1},
        {"reason": _REASON, "expected_row_version": 1, "state": "Rejected"},
    ],
    ids=[
        "no-reason",
        "no-version",
        "version-zero",
        "version-not-a-number",
        "reason-too-long",
        "unknown-field",
    ],
)
def test_a_malformed_request_is_a_422(api: ApiTestApp, body: dict[str, Any]) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    response = api.post(f"/submissions/{submission.id}/withdraw", token=token, json=body)

    assert response.status_code == 422, response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_identifier_that_is_not_a_uuid_is_a_422(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)

    assert _withdraw(api, token, "not-a-uuid").status_code == 422


# --- optimistic locking (FR-28) ------------------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_stale_row_version_is_a_409_that_carries_the_current_one(api: ApiTestApp) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    response = _withdraw(api, token, submission.id, expected_row_version=7)

    assert response.status_code == 409, response.text
    assert response.json()["expected_row_version"] == 7
    assert response.json()["current_row_version"] == submission.row_version
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_detail_carries_the_row_version_a_withdrawal_needs(api: ApiTestApp) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    detail = api.get(f"/submissions/{submission.id}", token=token)
    assert detail.status_code == 200, detail.text
    row_version = detail.json()["row_version"]
    assert isinstance(row_version, int)

    withdrawn = _withdraw(api, token, submission.id, expected_row_version=row_version)

    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["row_version"] == row_version + 1
    after = api.get(f"/submissions/{submission.id}", token=token)
    assert after.json()["row_version"] == row_version + 1
