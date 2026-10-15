"""`POST /api/v1/submissions/{id}/terminology-request` and the generic route's refusal of the move
(FR-30, FR-31, FR-44, FR-81, NFR-08, NFR-45).

The real app over a stub identity provider (`api_app_support`). Submissions are inserted directly, so
any state is reachable. `backend/tests` shares one Postgres container, so every assertion is scoped to
rows this test created: records are found by submission, and audit events by the submission they
describe. Every refusal asserts that nothing was written.
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
from nptc.db.models.submission_terminology_request import SubmissionTerminologyRequest
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
_REASON = "No concept covers this measurement."
_REVIEWING_ROLES = [Role.REVIEWER, Role.ADMINISTRATOR]
#: Roles that may sign in and hold no review permission.
_NON_REVIEWING_ROLES = [Role.PROVISIONAL, Role.MEMBER, Role.OBSERVER]
ZERO_WIDTH_SPACE = chr(0x200B)


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _sign_in(api: ApiTestApp, role: Role, *, accept_terms: bool = True) -> tuple[str, User]:
    """A token holding exactly `role`, and the user it belongs to. An administrator gets the MFA
    claim the realm demands for that role. Each call gives its own email, so each is a distinct user
    (NFR-05)."""
    subject = f"sub-terminology-{uuid.uuid4().hex[:10]}"
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


def _submission(api: ApiTestApp, submitter: User, *, state: str = "Under review") -> Submission:
    submission = Submission(
        kind=SubmissionKind.NEW_TEST.value,
        state=state,
        preferred_term=f"Terminology submission {uuid.uuid4()}",
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=submitter.id,
        organisation="Submitter Lab",
    )
    api.session.add(submission)
    api.session.flush()
    return submission


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "proposed_fsn": "Serum sodium level (observable entity)",
        "target_edition": "au",
        "justification": "No concept covers this measurement.",
        "external_reference": {"system": "snomed-request-service", "reference": "REQ-1"},
        "raised_date": "2026-10-01",
        "expected_release": "2027-03-31",
        "reason": _REASON,
        "expected_row_version": 1,
    }
    body.update(overrides)
    return body


def _request(api: ApiTestApp, token: str | None, submission_id: object, **overrides: object) -> Any:
    return api.post(
        f"/submissions/{submission_id}/terminology-request",
        token=token,
        json=_body(**overrides),
    )


def _record(api: ApiTestApp, submission: Submission) -> SubmissionTerminologyRequest | None:
    api.session.expire_all()
    return api.session.get(SubmissionTerminologyRequest, submission.id)


def _transitions(api: ApiTestApp, submission: Submission) -> list[SubmissionTransition]:
    return list(
        api.session.execute(
            select(SubmissionTransition)
            .where(SubmissionTransition.submission_id == submission.id)
            .order_by(SubmissionTransition.created_at)
        ).scalars()
    )


def _events(
    api: ApiTestApp, submission: Submission, *, entity_type: str = "submission"
) -> list[Any]:
    return list(
        api.session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == entity_type, AuditEvent.entity_id == str(submission.id)
            )
            .order_by(AuditEvent.sequence)
        ).scalars()
    )


def _all_events(api: ApiTestApp, submission: Submission) -> list[Any]:
    return _events(api, submission) + _events(
        api, submission, entity_type="submission_terminology_request"
    )


def _state(api: ApiTestApp, submission: Submission) -> str:
    api.session.expire(submission)
    return submission.state


def _assert_unchanged(api: ApiTestApp, submission: Submission, state: str = "Under review") -> None:
    assert _state(api, submission) == state
    assert submission.row_version == 1
    assert _record(api, submission) is None
    assert _transitions(api, submission) == []
    assert _all_events(api, submission) == []


# --- the move in (FR-30, FR-31, NFR-08) ----------------------------------------------------------


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-31")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REVIEWING_ROLES)
def test_a_reviewing_role_moves_a_submission_to_awaiting_terminology_with_a_record(
    api: ApiTestApp, role: Role
) -> None:
    _, submitter = _sign_in(api, Role.MEMBER)
    token, actor = _sign_in(api, role)
    submission = _submission(api, submitter)

    response = _request(
        api,
        token,
        submission.id,
        proposed_fsn="  Serum sodium level (observable entity)  ",
        target_edition="int",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(submission.id)
    assert body["state"] == "Awaiting terminology"
    assert body["row_version"] == 2
    assert body["terminology_request"] == {
        "proposed_fsn": "Serum sodium level (observable entity)",
        "target_edition": "int",
        "justification": "No concept covers this measurement.",
        "external_reference": {"system": "snomed-request-service", "reference": "REQ-1"},
        "raised_date": "2026-10-01",
        "expected_release": "2027-03-31",
        "updated_at": body["terminology_request"]["updated_at"],
    }
    assert _state(api, submission) == "Awaiting terminology"
    record = _record(api, submission)
    assert record is not None
    assert (record.external_reference_system, record.external_reference_ref) == (
        "snomed-request-service",
        "REQ-1",
    )
    transitions = _transitions(api, submission)
    assert [(t.from_state, t.to_state, t.actor_id, t.reason) for t in transitions] == [
        ("Under review", "Awaiting terminology", actor.id, _REASON)
    ]


@pytest.mark.req("FR-30")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_the_move_appends_one_state_event_and_one_record_event(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    assert _request(api, token, submission.id).status_code == 200

    [moved] = _events(api, submission)
    assert moved.action == "submission.terminology_requested"
    assert moved.before == {"state": "Under review"}
    assert moved.after == {"state": "Awaiting terminology"}
    assert moved.reason == _REASON
    [created] = _events(api, submission, entity_type="submission_terminology_request")
    assert created.action == "submission.terminology_request_created"
    assert created.before is None
    assert created.after == {
        "proposed_fsn": "Serum sodium level (observable entity)",
        "target_edition": "au",
        "justification": "No concept covers this measurement.",
        "external_reference_system": "snomed-request-service",
        "external_reference_ref": "REQ-1",
        "raised_date": "2026-10-01",
        "expected_release": "2027-03-31",
    }


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_the_expected_release_may_be_left_out(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    body = _body()
    del body["expected_release"]

    response = api.post(f"/submissions/{submission.id}/terminology-request", token=token, json=body)

    assert response.status_code == 200, response.text
    assert response.json()["terminology_request"]["expected_release"] is None
    record = _record(api, submission)
    assert record is not None and record.expected_release is None


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_the_detail_offers_the_move_to_a_reviewer_and_not_to_the_submitter(
    api: ApiTestApp,
) -> None:
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    member_token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    reviewer = api.get(f"/submissions/{submission.id}", token=reviewer_token).json()
    member = api.get(f"/submissions/{submission.id}", token=member_token).json()

    assert "Awaiting terminology" in reviewer["available_transitions"]
    assert "Awaiting terminology" not in member["available_transitions"]


# --- a refused request writes nothing (FR-30, FR-31) ---------------------------------------------


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-31")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"proposed_fsn": "   "}, "proposed_fsn"),
        ({"proposed_fsn": ""}, "proposed_fsn"),
        ({"justification": "  "}, "justification"),
        (
            {"external_reference": {"system": " ", "reference": "REQ-1"}},
            "external_reference_system",
        ),
        (
            {"external_reference": {"system": "svc", "reference": ""}},
            "external_reference_ref",
        ),
        ({"reason": "   "}, "reason"),
        ({"proposed_fsn": f"Serum{ZERO_WIDTH_SPACE}sodium"}, "proposed_fsn"),
        ({"justification": f"No{ZERO_WIDTH_SPACE}concept"}, "justification"),
        (
            {"external_reference": {"system": f"s{ZERO_WIDTH_SPACE}", "reference": "REQ-1"}},
            "external_reference_system",
        ),
        (
            {"external_reference": {"system": "svc", "reference": f"R{ZERO_WIDTH_SPACE}"}},
            "external_reference_ref",
        ),
        ({"reason": f"Because{ZERO_WIDTH_SPACE}"}, "reason"),
    ],
    ids=[
        "blank_fsn",
        "empty_fsn",
        "blank_justification",
        "blank_system",
        "empty_reference",
        "blank_reason",
        "invisible_in_fsn",
        "invisible_in_justification",
        "invisible_in_system",
        "invisible_in_reference",
        "invisible_in_reason",
    ],
)
def test_a_text_field_that_is_blank_or_unclean_is_a_422_that_names_it(
    api: ApiTestApp, overrides: dict[str, object], field: str
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _request(api, token, submission.id, **overrides)

    assert response.status_code == 422, response.text
    assert response.json()["field"] == field
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-31")
@pytest.mark.integration
@pytest.mark.parametrize(
    "overrides",
    [
        {"target_edition": "intl"},
        {"target_edition": "AU"},
        {"target_edition": ""},
        {"target_edition": None},
        {"raised_date": "not-a-date"},
        {"raised_date": None},
        {"expected_release": "next spring"},
        {"external_reference": None},
        {"external_reference": "REQ-1"},
        {"external_reference": {"system": "svc"}},
        {"external_reference": {"reference": "REQ-1"}},
        {"external_reference": {"system": "svc", "reference": "R", "extra": "x"}},
        {"proposed_fsn": None},
        {"justification": None},
        {"proposed_fsn": "x" * 501},
        {"unexpected": "field"},
        {"expected_row_version": 0},
        {"expected_row_version": "one"},
    ],
    ids=[
        "edition_intl",
        "edition_upper_case",
        "edition_empty",
        "edition_null",
        "date_not_a_date",
        "date_null",
        "release_not_a_date",
        "reference_null",
        "reference_a_string",
        "reference_without_reference",
        "reference_without_system",
        "reference_extra_key",
        "fsn_null",
        "justification_null",
        "fsn_too_long",
        "extra_field",
        "version_zero",
        "version_text",
    ],
)
def test_a_body_that_fails_validation_is_a_422_and_writes_nothing(
    api: ApiTestApp, overrides: dict[str, object]
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _request(api, token, submission.id, **overrides)

    assert response.status_code == 422, response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-31")
@pytest.mark.integration
@pytest.mark.parametrize(
    "missing",
    [
        "proposed_fsn",
        "target_edition",
        "justification",
        "external_reference",
        "raised_date",
        "reason",
        "expected_row_version",
    ],
)
def test_a_missing_required_field_is_a_422_and_writes_nothing(
    api: ApiTestApp, missing: str
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    body = _body()
    del body[missing]

    response = api.post(f"/submissions/{submission.id}/terminology-request", token=token, json=body)

    assert response.status_code == 422, response.text
    _assert_unchanged(api, submission)


# --- who may make the move (FR-44, FR-81, NFR-45) ------------------------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize("role", _NON_REVIEWING_ROLES)
def test_a_role_without_the_review_permission_is_refused(api: ApiTestApp, role: Role) -> None:
    token, user = _sign_in(api, role)
    submission = _submission(api, user)

    response = _request(api, token, submission.id)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers
    assert not _UUID.search(response.text)
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
def test_the_submitter_cannot_move_their_own_submission_to_awaiting_terminology(
    api: ApiTestApp,
) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    response = _request(api, token, submission.id)

    assert response.status_code == 403, response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_caller_without_the_permission_is_refused_before_the_submission_is_looked_up(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.OBSERVER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    for target in (submission.id, uuid.uuid4()):
        response = _request(api, token, target)
        assert response.status_code == 403, response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_anonymous_caller_is_refused_with_401(api: ApiTestApp) -> None:
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _request(api, None, submission.id)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
    _assert_unchanged(api, submission)


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_reviewer_who_has_not_accepted_the_current_terms_is_refused(api: ApiTestApp) -> None:
    token, reviewer = _sign_in(api, Role.REVIEWER, accept_terms=False)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    refused = _request(api, token, submission.id)

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == TERMS_ACCEPTANCE_REQUIRED_CODE
    _assert_unchanged(api, submission)

    api.accept_current_terms(reviewer.id)
    assert _request(api, token, submission.id).status_code == 200


# --- state, version and identity (FR-28, FR-30) --------------------------------------------------


@pytest.mark.req("FR-30")
@pytest.mark.integration
@pytest.mark.parametrize(
    "state",
    [
        "Submitted",
        "Triage",
        "Awaiting terminology",
        "Ready for approval",
        "Approved",
        "Published in release",
        "Rejected",
        "Withdrawn",
    ],
)
def test_a_submission_that_is_not_under_review_is_a_409(api: ApiTestApp, state: str) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1], state=state)

    response = _request(api, token, submission.id)

    assert response.status_code == 409, response.text
    assert _state(api, submission) == state
    assert _record(api, submission) is None
    assert _transitions(api, submission) == []
    assert _all_events(api, submission) == []


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_a_second_request_is_refused_and_the_first_record_stands(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    assert _request(api, token, submission.id).status_code == 200

    again = _request(
        api,
        token,
        submission.id,
        expected_row_version=2,
        external_reference={"system": "other", "reference": "REQ-2"},
    )

    assert again.status_code == 409, again.text
    record = _record(api, submission)
    assert record is not None and record.external_reference_ref == "REQ-1"
    assert len(_transitions(api, submission)) == 1


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_a_stale_row_version_is_a_409_that_carries_the_current_one(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _request(api, token, submission.id, expected_row_version=7)

    assert response.status_code == 409, response.text
    assert response.json()["current_row_version"] == 1
    assert response.json()["expected_row_version"] == 7
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_an_unknown_submission_is_a_404_and_a_malformed_identifier_a_422(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)

    assert _request(api, token, uuid.uuid4()).status_code == 404
    assert _request(api, token, "not-a-uuid").status_code == 422


# --- the general route refuses the move (FR-28, FR-30) -------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_the_general_route_refuses_the_move_and_names_the_dedicated_route(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = api.post(
        f"/submissions/{submission.id}/transitions",
        token=token,
        json={
            "to_state": "Awaiting terminology",
            "reason": _REASON,
            "expected_row_version": 1,
        },
    )

    assert response.status_code == 409, response.text
    assert "/terminology-request" in response.json()["detail"]
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize("role", [Role.PROVISIONAL, Role.MEMBER])
def test_the_general_route_tells_a_caller_without_the_permission_403_not_409(
    api: ApiTestApp, role: Role
) -> None:
    token, user = _sign_in(api, role)
    submission = _submission(api, user)

    response = api.post(
        f"/submissions/{submission.id}/transitions",
        token=token,
        json={
            "to_state": "Awaiting terminology",
            "reason": _REASON,
            "expected_row_version": 1,
        },
    )

    assert response.status_code == 403, response.text
    _assert_unchanged(api, submission)
