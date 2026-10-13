"""`PUT` and `DELETE /api/v1/submissions/{id}/interest` (FR-32, FR-34, FR-44, NFR-08, NFR-45).

The real app over a stub identity provider (`api_app_support`). Submissions are inserted directly.
`backend/tests` shares one Postgres container, so every assertion is scoped to rows this test
created: a count is read for a submission the test made, and audit events are found by the user who
caused them.
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
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from nptc.api.dependencies import current_principal
from nptc.api.terms_gate import TERMS_ACCEPTANCE_REQUIRED_CODE
from nptc.auth.permissions import Permission, Role
from nptc.auth.principal import Principal
from nptc.db.models.audit import AuditEvent
from nptc.db.models.submission import CLOSED_SUBMISSION_STATES, Submission, SubmissionKind
from nptc.db.models.submission_interest import SubmissionInterest
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
_REGISTRANT_ROLES = [Role.MEMBER, Role.REVIEWER, Role.ADMINISTRATOR]
_REFUSED_ROLES = [Role.PROVISIONAL, Role.OBSERVER]
_PROFILE_ORGANISATION = "Profile Pathology"


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _sign_in(api: ApiTestApp, role: Role, *, accept_terms: bool = True) -> tuple[str, User]:
    """A token holding exactly `role`, and the user it belongs to, whose profile names an
    organisation. An administrator gets the MFA claim the realm demands for that role. Each call
    gives its own email, so each is a distinct user (NFR-05)."""
    subject = f"sub-interest-{uuid.uuid4().hex[:10]}"
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
    user.organisation = _PROFILE_ORGANISATION
    api.session.flush()
    return token, user


def _submission(api: ApiTestApp, submitter: User, *, state: str = "Submitted") -> Submission:
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
    api.session.add(submission)
    api.session.flush()
    return submission


def _path(submission_id: object) -> str:
    return f"/submissions/{submission_id}/interest"


def _put(api: ApiTestApp, token: str | None, submission_id: object) -> Any:
    return api.request("PUT", _path(submission_id), token=token)


def _delete(api: ApiTestApp, token: str | None, submission_id: object) -> Any:
    return api.request("DELETE", _path(submission_id), token=token)


def _live_count(api: ApiTestApp, submission: Submission) -> int:
    return api.session.execute(
        select(func.count())
        .select_from(SubmissionInterest)
        .where(
            SubmissionInterest.submission_id == submission.id,
            SubmissionInterest.revoked_at.is_(None),
        )
    ).scalar_one()


def _row_count(api: ApiTestApp, submission: Submission) -> int:
    return api.session.execute(
        select(func.count())
        .select_from(SubmissionInterest)
        .where(SubmissionInterest.submission_id == submission.id)
    ).scalar_one()


def _events(api: ApiTestApp, user: User) -> list[str]:
    return list(
        api.session.execute(
            select(AuditEvent.action)
            .where(
                AuditEvent.actor_user_id == user.id,
                AuditEvent.entity_type == "submission_interest",
            )
            .order_by(AuditEvent.sequence)
        ).scalars()
    )


# --- registering and revoking (FR-32) ------------------------------------------------------


@pytest.mark.req("FR-32")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REGISTRANT_ROLES)
def test_a_registrant_registers_and_the_body_says_where_things_stand(
    api: ApiTestApp, role: Role
) -> None:
    token, member = _sign_in(api, role)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _put(api, token, submission.id)

    assert response.status_code == 200, response.text
    assert response.json() == {"has_registered_interest": True, "interest_count": 1}
    assert _live_count(api, submission) == 1
    assert _events(api, member) == ["submission_interest.registered"]


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_the_record_holds_a_copy_of_the_profile_organisation(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    _put(api, token, submission.id)

    organisation = api.session.execute(
        select(SubmissionInterest.organisation).where(
            SubmissionInterest.submission_id == submission.id,
            SubmissionInterest.user_id == member.id,
        )
    ).scalar_one()
    assert organisation == _PROFILE_ORGANISATION


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_registering_twice_answers_the_same_and_writes_nothing_more(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    first = _put(api, token, submission.id)

    second = _put(api, token, submission.id)

    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert _row_count(api, submission) == 1
    assert _events(api, member) == ["submission_interest.registered"]


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_revoking_answers_with_the_new_standing_and_keeps_the_record(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    _put(api, token, submission.id)

    response = _delete(api, token, submission.id)

    assert response.status_code == 200, response.text
    assert response.json() == {"has_registered_interest": False, "interest_count": 0}
    assert _row_count(api, submission) == 1
    assert _live_count(api, submission) == 0
    assert _events(api, member) == [
        "submission_interest.registered",
        "submission_interest.revoked",
    ]


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_revoking_twice_or_without_registering_answers_the_same_and_writes_nothing(
    api: ApiTestApp,
) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    never = _delete(api, token, submission.id)
    _put(api, token, submission.id)
    _delete(api, token, submission.id)
    again = _delete(api, token, submission.id)

    assert never.status_code == again.status_code == 200
    assert never.json() == again.json() == {"has_registered_interest": False, "interest_count": 0}
    assert _events(api, member) == [
        "submission_interest.registered",
        "submission_interest.revoked",
    ]


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_registering_again_after_a_revocation_adds_a_new_record(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    _put(api, token, submission.id)
    _delete(api, token, submission.id)

    response = _put(api, token, submission.id)

    assert response.json() == {"has_registered_interest": True, "interest_count": 1}
    assert _row_count(api, submission) == 2
    assert _live_count(api, submission) == 1


# --- counts (FR-34) ------------------------------------------------------------------------


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_the_count_covers_every_user_and_the_flag_only_the_caller(api: ApiTestApp) -> None:
    first_token, _ = _sign_in(api, Role.MEMBER)
    second_token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    _put(api, first_token, submission.id)

    second = _put(api, second_token, submission.id)
    after_revoke = _delete(api, first_token, submission.id)

    assert second.json() == {"has_registered_interest": True, "interest_count": 2}
    assert after_revoke.json() == {"has_registered_interest": False, "interest_count": 1}


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_the_response_names_no_user_and_no_organisation(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _put(api, token, submission.id)

    assert set(response.json()) == {"has_registered_interest", "interest_count"}
    assert str(member.id) not in response.text
    assert _PROFILE_ORGANISATION not in response.text


# --- refusals ------------------------------------------------------------------------------


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_a_submitter_registering_on_their_own_submission_is_refused_with_409(
    api: ApiTestApp,
) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submission = _submission(api, member)

    response = _put(api, token, submission.id)

    assert response.status_code == 409, response.text
    assert "your own submission" in response.json()["detail"]
    assert _row_count(api, submission) == 0
    assert _events(api, member) == []


@pytest.mark.req("FR-32")
@pytest.mark.integration
@pytest.mark.parametrize("state", CLOSED_SUBMISSION_STATES)
def test_registering_on_a_closed_submission_is_refused_with_409(
    api: ApiTestApp, state: str
) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1], state=state)

    response = _put(api, token, submission.id)

    assert response.status_code == 409, response.text
    assert "closed" in response.json()["detail"]
    assert _row_count(api, submission) == 0
    assert _events(api, member) == []


@pytest.mark.req("FR-32")
@pytest.mark.integration
@pytest.mark.parametrize("state", CLOSED_SUBMISSION_STATES)
def test_revoking_on_a_closed_submission_is_refused_and_the_count_stays(
    api: ApiTestApp, state: str
) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    _put(api, token, submission.id)
    submission.state = state
    api.session.flush()

    response = _delete(api, token, submission.id)

    assert response.status_code == 409, response.text
    assert _live_count(api, submission) == 1
    assert _events(api, member) == ["submission_interest.registered"]


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_an_unknown_submission_is_a_404_for_both_routes(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    unknown = uuid.uuid4()

    for response in (_put(api, token, unknown), _delete(api, token, unknown)):
        assert response.status_code == 404, response.text
        assert str(unknown) not in response.text
    assert _events(api, member) == []


@pytest.mark.req("FR-32")
@pytest.mark.integration
def test_an_identifier_that_is_not_a_uuid_is_a_422(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)

    assert _put(api, token, "not-a-uuid").status_code == 422
    assert _delete(api, token, "not-a-uuid").status_code == 422


# --- authorisation (FR-44, NFR-45) ---------------------------------------------------------


@pytest.mark.req("FR-34")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REFUSED_ROLES)
def test_a_role_without_interest_register_is_refused_with_403_on_both_routes(
    api: ApiTestApp, role: Role
) -> None:
    """Provisional and Observer: the permission is absent, so the route refuses before it looks
    for the submission, and nothing is written or audited."""
    token, user = _sign_in(api, role)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    for send in (_put, _delete):
        response = send(api, token, submission.id)
        assert response.status_code == 403, response.text
        assert "WWW-Authenticate" not in response.headers
        for name in Role:
            assert name.value not in response.text
        assert not _UUID.search(response.text)
    assert _row_count(api, submission) == 0
    assert _events(api, user) == []


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_anonymous_caller_is_refused_with_401_on_both_routes(api: ApiTestApp) -> None:
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    for send in (_put, _delete):
        response = send(api, None, submission.id)
        assert response.status_code == 401, response.text
        assert response.headers["WWW-Authenticate"] == "Bearer"
    assert _row_count(api, submission) == 0


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_user_who_has_not_accepted_the_current_terms_is_refused(api: ApiTestApp) -> None:
    token, user = _sign_in(api, Role.MEMBER, accept_terms=False)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    refused = _put(api, token, submission.id)

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == TERMS_ACCEPTANCE_REQUIRED_CODE
    assert _row_count(api, submission) == 0
    assert _events(api, user) == []

    api.accept_current_terms(user.id)
    accepted = _put(api, token, submission.id)

    assert accepted.status_code == 200, accepted.text


# --- the list and the detail carry interest (FR-34) ----------------------------------------


def _listed_item(api: ApiTestApp, token: str, submission: Submission) -> dict[str, Any]:
    """The list item for `submission`, read from its submitter's own list so the page is small and
    holds only rows this test made."""
    response = api.get("/submissions", token=token, params={"mine": "true", "limit": 200})
    assert response.status_code == 200, response.text
    [item] = [item for item in response.json()["items"] if item["id"] == str(submission.id)]
    assert isinstance(item, dict)
    return item


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_the_detail_and_the_list_carry_the_count_and_the_callers_own_flag(
    api: ApiTestApp,
) -> None:
    submitter_token, submitter = _sign_in(api, Role.MEMBER)
    registrant_token, _ = _sign_in(api, Role.MEMBER)
    other_token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, submitter)
    _put(api, registrant_token, submission.id)
    _put(api, other_token, submission.id)
    _delete(api, other_token, submission.id)

    as_registrant = api.get(f"/submissions/{submission.id}", token=registrant_token).json()
    as_submitter = api.get(f"/submissions/{submission.id}", token=submitter_token).json()
    listed = _listed_item(api, submitter_token, submission)

    assert (as_registrant["interest_count"], as_registrant["has_registered_interest"]) == (1, True)
    assert (as_submitter["interest_count"], as_submitter["has_registered_interest"]) == (1, False)
    assert (listed["interest_count"], listed["has_registered_interest"]) == (1, False)


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_a_submission_nobody_backed_reports_zero_and_false(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submission = _submission(api, member)

    detail = api.get(f"/submissions/{submission.id}", token=token).json()

    assert (detail["interest_count"], detail["has_registered_interest"]) == (0, False)


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_a_closed_submission_still_reports_the_interest_that_stood_at_closure(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.MEMBER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    _put(api, token, submission.id)
    submission.state = CLOSED_SUBMISSION_STATES[0]
    api.session.flush()

    detail = api.get(f"/submissions/{submission.id}", token=token).json()

    assert (detail["interest_count"], detail["has_registered_interest"]) == (1, True)


@pytest.mark.req("FR-34")
@pytest.mark.integration
@pytest.mark.parametrize("role", [Role.OBSERVER, Role.PROVISIONAL, *_REGISTRANT_ROLES])
def test_every_signed_in_role_sees_the_count_and_none_sees_who(api: ApiTestApp, role: Role) -> None:
    registrant_token, registrant = _sign_in(api, Role.MEMBER)
    token, _ = _sign_in(api, role)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    _put(api, registrant_token, submission.id)

    detail = api.get(f"/submissions/{submission.id}", token=token)

    assert detail.status_code == 200, detail.text
    assert detail.json()["interest_count"] == 1
    assert detail.json()["has_registered_interest"] is False
    assert str(registrant.id) not in detail.text


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_a_caller_without_the_view_counts_permission_gets_no_count(api: ApiTestApp) -> None:
    """No role lacks `interest.view_counts` today, so the gate is exercised with a principal that
    holds `submission.view` alone. The flag is the caller's own, so it stays."""
    token, viewer = _sign_in(api, Role.MEMBER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    _put(api, token, submission.id)

    api.app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=viewer.id,
        user_ref=None,
        status=viewer.status,
        roles=frozenset({Role.MEMBER}),
        permissions=frozenset({Permission.SUBMISSION_VIEW}),
        mfa_satisfied=True,
        mfa_suppressed_roles=frozenset(),
    )
    try:
        detail = api.get(f"/submissions/{submission.id}", token=token)
    finally:
        del api.app.dependency_overrides[current_principal]

    assert detail.status_code == 200, detail.text
    assert detail.json()["interest_count"] is None
    assert detail.json()["has_registered_interest"] is True


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_a_page_of_submissions_costs_one_interest_statement(
    api: ApiTestApp, app_db: Connection, capture_statements: Any
) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submissions = [_submission(api, member) for _ in range(5)]
    _put(api, _sign_in(api, Role.REVIEWER)[0], submissions[0].id)

    with capture_statements(
        app_db, lambda statement, _: "submission_interest" in statement
    ) as seen:
        response = api.get("/submissions", token=token, params={"mine": "true", "limit": 200})

    assert response.status_code == 200, response.text
    assert len(seen) == 1
