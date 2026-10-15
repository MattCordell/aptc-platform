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
from nptc.db.models.submission_working_copy import SubmissionWorkingCopy
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity
from nptc_shared.terminology import (
    AU_LANGUAGE_TAG,
    Operation,
    StubConcept,
    TerminologyStatusError,
    TerminologyTimeoutError,
)


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


# --- the move out: an Administrator attaches the code (FR-30, FR-82) -----------------------------

_CODE = "391483001"
_FSN = "Microscopy (acid fast bacilli) (procedure)"
_INACTIVE_CODE = "119364003"
_ATTACH_REASON = "The authority issued the code."


def _seed_concepts(api: ApiTestApp) -> None:
    api.terminology.add_concept(
        StubConcept(
            code=_CODE,
            fsn=_FSN,
            preferred_terms={AU_LANGUAGE_TAG: "Acid fast bacilli microscopy"},
        )
    )
    api.terminology.add_concept(
        StubConcept(
            code=_INACTIVE_CODE,
            fsn="Serum specimen (specimen)",
            preferred_terms={AU_LANGUAGE_TAG: "Serum specimen"},
            active=False,
        )
    )


def _attach(
    api: ApiTestApp,
    token: str | None,
    submission_id: object,
    *,
    snomed_code: object = _CODE,
    reason: object = _ATTACH_REASON,
    expected_row_version: object = 2,
    **extra: object,
) -> Any:
    return api.post(
        f"/submissions/{submission_id}/terminology-request/attach-code",
        token=token,
        json={
            "snomed_code": snomed_code,
            "reason": reason,
            "expected_row_version": expected_row_version,
            **extra,
        },
    )


def _awaiting(api: ApiTestApp) -> Submission:
    """A submission that a reviewer has moved to `Awaiting terminology` through the real route, so
    the record, the transition row and the events are the ones the route writes. `row_version` is 2."""
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    assert _request(api, token, submission.id).status_code == 200
    return submission


def _working_copy(api: ApiTestApp, submission: Submission) -> SubmissionWorkingCopy | None:
    api.session.expire_all()
    return api.session.get(SubmissionWorkingCopy, submission.id)


def _assert_still_awaiting(api: ApiTestApp, submission: Submission) -> None:
    """Nothing the attach writes exists: the state, the version, the transitions and the events are
    those the move in left, and there is no working copy."""
    assert _state(api, submission) == "Awaiting terminology"
    assert submission.row_version == 2
    assert _working_copy(api, submission) is None
    assert len(_transitions(api, submission)) == 1
    assert [event.action for event in _events(api, submission)] == [
        "submission.terminology_requested"
    ]


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-06")
@pytest.mark.req("FR-82")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_an_administrator_attaches_the_code_and_the_submission_is_ready_for_approval(
    api: ApiTestApp,
) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, admin = _sign_in(api, Role.ADMINISTRATOR)

    response = _attach(api, token, submission.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "Ready for approval"
    assert body["row_version"] == 3
    assert (body["snomed_code"], body["snomed_fsn"]) == (_CODE, _FSN)
    assert isinstance(body["snomed_code"], str)
    assert set(body["label_provenance"]) == {"snomed_fsn"}
    assert _state(api, submission) == "Ready for approval"
    copy = _working_copy(api, submission)
    assert copy is not None
    assert (copy.snomed_code, copy.snomed_fsn) == (_CODE, _FSN)
    assert copy.preferred_term == submission.preferred_term
    assert [(t.from_state, t.to_state) for t in _transitions(api, submission)] == [
        ("Under review", "Awaiting terminology"),
        ("Awaiting terminology", "Ready for approval"),
    ]
    assert _transitions(api, submission)[-1].actor_id == admin.id
    assert _transitions(api, submission)[-1].reason == _ATTACH_REASON
    # The submitter's own content is untouched, and the record stays as the history of the request.
    assert submission.snomed_code is None
    assert _record(api, submission) is not None


@pytest.mark.req("FR-30")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_the_attach_appends_one_event_that_carries_the_state_and_the_code(
    api: ApiTestApp,
) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.ADMINISTRATOR)

    assert _attach(api, token, submission.id).status_code == 200

    moved, attached = _events(api, submission)
    assert moved.action == "submission.terminology_requested"
    assert attached.action == "submission.terminology_attached"
    assert attached.before == {
        "state": "Awaiting terminology",
        "snomed_code": None,
        "snomed_fsn": None,
    }
    assert attached.after == {
        "state": "Ready for approval",
        "snomed_code": _CODE,
        "snomed_fsn": _FSN,
    }
    assert attached.reason == _ATTACH_REASON


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_an_edit_made_while_waiting_survives_the_attach(api: ApiTestApp) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    edit = api.request(
        "PATCH",
        f"/submissions/{submission.id}/working-copy",
        token=reviewer_token,
        json={"notes": "Raised with the authority", "reason": "Note", "expected_row_version": 2},
    )
    assert edit.status_code == 200, edit.text
    admin_token, _ = _sign_in(api, Role.ADMINISTRATOR)

    response = _attach(api, admin_token, submission.id, expected_row_version=3)

    assert response.status_code == 200, response.text
    copy = _working_copy(api, submission)
    assert copy is not None
    assert copy.notes == "Raised with the authority"
    assert (copy.snomed_code, copy.snomed_fsn) == (_CODE, _FSN)


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_the_attach_looks_the_code_up_whatever_the_target_edition_says(
    api: ApiTestApp,
) -> None:
    _seed_concepts(api)
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    assert _request(api, reviewer_token, submission.id, target_edition="int").status_code == 200
    token, _ = _sign_in(api, Role.ADMINISTRATOR)

    assert _attach(api, token, submission.id).status_code == 200

    lookups = [r for r in api.terminology.requests if r.operation is Operation.LOOKUP]
    assert lookups
    assert all(r.detail == _CODE for r in lookups)


# --- a refused attach writes nothing (FR-30, FR-06, FR-82) ---------------------------------------


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-81")
@pytest.mark.integration
def test_a_reviewer_is_refused_the_attach_and_no_terminology_call_is_made(
    api: ApiTestApp,
) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.REVIEWER)
    calls_before = len(api.terminology.requests)

    response = _attach(api, token, submission.id)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers
    assert len(api.terminology.requests) == calls_before
    _assert_still_awaiting(api, submission)


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize("role", _NON_REVIEWING_ROLES)
def test_a_role_without_any_review_permission_is_refused_the_attach(
    api: ApiTestApp, role: Role
) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, _ = _sign_in(api, role)

    response = _attach(api, token, submission.id)

    assert response.status_code == 403, response.text
    _assert_still_awaiting(api, submission)


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-81")
@pytest.mark.integration
def test_the_submitter_cannot_attach_a_code_to_their_own_submission(api: ApiTestApp) -> None:
    _seed_concepts(api)
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    member_token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)
    assert _request(api, reviewer_token, submission.id).status_code == 200

    response = _attach(api, member_token, submission.id)

    assert response.status_code == 403, response.text
    _assert_still_awaiting(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_reviewer_is_refused_the_attach_before_the_submission_is_looked_up(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)

    assert _attach(api, token, uuid.uuid4()).status_code == 403


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_anonymous_caller_is_refused_the_attach_with_401(api: ApiTestApp) -> None:
    submission = _awaiting(api)

    response = _attach(api, None, submission.id)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
    _assert_still_awaiting(api, submission)


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_an_administrator_who_has_not_accepted_the_current_terms_is_refused(
    api: ApiTestApp,
) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, admin = _sign_in(api, Role.ADMINISTRATOR, accept_terms=False)

    refused = _attach(api, token, submission.id)

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == TERMS_ACCEPTANCE_REQUIRED_CODE
    _assert_still_awaiting(api, submission)

    api.accept_current_terms(admin.id)
    assert _attach(api, token, submission.id).status_code == 200


@pytest.mark.req("NFR-06")
@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_administrator_without_mfa_gets_a_step_up_challenge(api: ApiTestApp) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    subject = f"sub-terminology-no-mfa-{uuid.uuid4().hex[:8]}"
    token = api.admin_token(subject=subject, with_mfa=False)

    response = _attach(api, token, submission.id)

    assert response.status_code == 403, response.text
    challenge = response.headers["WWW-Authenticate"]
    assert 'error="insufficient_user_authentication"' in challenge
    _assert_still_awaiting(api, submission)


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-06")
@pytest.mark.req("FR-82")
@pytest.mark.integration
@pytest.mark.parametrize(
    "code",
    ["26604008", "not-a-code", "0123", _INACTIVE_CODE],
    ids=["bad_check_digit", "not_digits", "too_short", "inactive"],
)
def test_a_code_that_cannot_be_stored_is_a_422_and_the_submission_keeps_waiting(
    api: ApiTestApp, code: str
) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.ADMINISTRATOR)

    response = _attach(api, token, submission.id, snomed_code=code)

    assert response.status_code == 422, response.text
    assert response.json()["field"] == "snomed_code"
    _assert_still_awaiting(api, submission)


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-06")
@pytest.mark.req("FR-81")
@pytest.mark.integration
def test_a_code_the_server_does_not_know_is_a_422_and_the_submission_keeps_waiting(
    api: ApiTestApp,
) -> None:
    api.terminology.seed_error(
        Operation.LOOKUP, TerminologyStatusError("not found", status_code=404)
    )
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.ADMINISTRATOR)

    response = _attach(api, token, submission.id, snomed_code="26604007")

    assert response.status_code == 422, response.text
    assert response.json()["field"] == "snomed_code"
    _assert_still_awaiting(api, submission)


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_an_unreachable_terminology_server_is_a_503_and_the_submission_keeps_waiting(
    api: ApiTestApp,
) -> None:
    api.terminology.seed_error(Operation.LOOKUP, TerminologyTimeoutError("timed out"))
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.ADMINISTRATOR)

    response = _attach(api, token, submission.id)

    assert response.status_code == 503, response.text
    assert response.json()["field"] == "snomed_code"
    _assert_still_awaiting(api, submission)


@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_the_caller_cannot_supply_the_fsn(api: ApiTestApp) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.ADMINISTRATOR)

    response = _attach(api, token, submission.id, snomed_fsn="Caller's own label")

    assert response.status_code == 422, response.text
    _assert_still_awaiting(api, submission)


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("overrides", "field"),
    [({"reason": "   "}, "reason"), ({"reason": f"Done{ZERO_WIDTH_SPACE}"}, "reason")],
    ids=["blank_reason", "invisible_in_reason"],
)
def test_a_blank_or_unclean_reason_is_a_422_that_names_it(
    api: ApiTestApp, overrides: dict[str, object], field: str
) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.ADMINISTRATOR)

    response = _attach(api, token, submission.id, **overrides)

    assert response.status_code == 422, response.text
    assert response.json()["field"] == field
    _assert_still_awaiting(api, submission)


@pytest.mark.req("FR-30")
@pytest.mark.integration
@pytest.mark.parametrize(
    "overrides",
    [
        {"snomed_code": None},
        {"snomed_code": 391483001},
        {"snomed_code": "9" * 40},
        {"expected_row_version": 0},
        {"unexpected": "field"},
    ],
    ids=["code_null", "code_a_number", "code_too_long", "version_zero", "extra_field"],
)
def test_an_attach_body_that_fails_validation_is_a_422_and_writes_nothing(
    api: ApiTestApp, overrides: dict[str, object]
) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.ADMINISTRATOR)

    response = _attach(api, token, submission.id, **overrides)

    assert response.status_code == 422, response.text
    _assert_still_awaiting(api, submission)


@pytest.mark.req("FR-30")
@pytest.mark.integration
@pytest.mark.parametrize(
    "state",
    [
        "Submitted",
        "Triage",
        "Under review",
        "Ready for approval",
        "Approved",
        "Published in release",
        "Rejected",
        "Withdrawn",
    ],
)
def test_a_submission_that_is_not_awaiting_terminology_is_a_409(
    api: ApiTestApp, state: str
) -> None:
    _seed_concepts(api)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1], state=state)
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    calls_before = len(api.terminology.requests)

    response = _attach(api, token, submission.id, expected_row_version=1)

    assert response.status_code == 409, response.text
    assert _state(api, submission) == state
    assert _working_copy(api, submission) is None
    assert _transitions(api, submission) == []
    assert len(api.terminology.requests) == calls_before


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_a_stale_row_version_is_a_409_that_carries_the_current_one_on_the_attach(
    api: ApiTestApp,
) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.ADMINISTRATOR)

    response = _attach(api, token, submission.id, expected_row_version=1)

    assert response.status_code == 409, response.text
    assert response.json()["current_row_version"] == 2
    assert response.json()["expected_row_version"] == 1
    _assert_still_awaiting(api, submission)


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_the_attach_cannot_be_repeated(api: ApiTestApp) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    assert _attach(api, token, submission.id).status_code == 200

    again = _attach(api, token, submission.id, expected_row_version=3)

    assert again.status_code == 409, again.text
    assert len(_transitions(api, submission)) == 2


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_an_unknown_submission_is_a_404_and_a_malformed_identifier_a_422_on_the_attach(
    api: ApiTestApp,
) -> None:
    _seed_concepts(api)
    token, _ = _sign_in(api, Role.ADMINISTRATOR)

    assert _attach(api, token, uuid.uuid4()).status_code == 404
    assert _attach(api, token, "not-a-uuid").status_code == 422


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-30")
@pytest.mark.req("FR-81")
@pytest.mark.integration
def test_the_general_route_cannot_take_a_submission_out_of_awaiting_terminology(
    api: ApiTestApp,
) -> None:
    submission = _awaiting(api)
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    admin_token, _ = _sign_in(api, Role.ADMINISTRATOR)
    body = {"to_state": "Ready for approval", "reason": _ATTACH_REASON, "expected_row_version": 2}

    as_reviewer = api.post(
        f"/submissions/{submission.id}/transitions", token=reviewer_token, json=body
    )
    as_admin = api.post(f"/submissions/{submission.id}/transitions", token=admin_token, json=body)

    assert as_reviewer.status_code == 403, as_reviewer.text
    assert as_admin.status_code == 409, as_admin.text
    assert "attach-code" in as_admin.json()["detail"]
    _assert_still_awaiting(api, submission)


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_the_detail_offers_the_attach_to_an_administrator_and_not_to_a_reviewer(
    api: ApiTestApp,
) -> None:
    submission = _awaiting(api)
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    admin_token, _ = _sign_in(api, Role.ADMINISTRATOR)

    reviewer = api.get(f"/submissions/{submission.id}", token=reviewer_token).json()
    admin = api.get(f"/submissions/{submission.id}", token=admin_token).json()

    assert reviewer["available_transitions"] == []
    assert admin["available_transitions"] == ["Ready for approval"]


# --- tracking the request while the submission waits (FR-31, NFR-08) -----------------------------


def _track(
    api: ApiTestApp,
    token: str | None,
    submission_id: object,
    *,
    reason: object = "Moved to the next release.",
    expected_row_version: object = 2,
    **fields: object,
) -> Any:
    return api.request(
        "PATCH",
        f"/submissions/{submission_id}/terminology-request",
        token=token,
        json={"reason": reason, "expected_row_version": expected_row_version, **fields},
    )


def _record_events(api: ApiTestApp, submission: Submission) -> list[Any]:
    return _events(api, submission, entity_type="submission_terminology_request")


def _assert_untracked(api: ApiTestApp, submission: Submission) -> None:
    """Nothing the tracking change writes exists: the record is as the move in wrote it, the version
    is the one it left, and only the creation event is on the record."""
    record = _record(api, submission)
    assert record is not None
    assert (record.external_reference_system, record.external_reference_ref) == (
        "snomed-request-service",
        "REQ-1",
    )
    assert str(record.expected_release) == "2027-03-31"
    assert submission.row_version == 2
    assert [event.action for event in _record_events(api, submission)] == [
        "submission.terminology_request_created"
    ]


@pytest.mark.req("FR-31")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REVIEWING_ROLES)
def test_a_reviewing_role_changes_the_tracking_fields_and_the_change_is_audited(
    api: ApiTestApp, role: Role
) -> None:
    submission = _awaiting(api)
    token, _ = _sign_in(api, role)

    response = _track(
        api,
        token,
        submission.id,
        external_reference={"system": "  new-system  ", "reference": "REQ-9"},
        expected_release="2027-06-30",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    assert body["row_version"] == 3
    assert body["terminology_request"]["external_reference"] == {
        "system": "new-system",
        "reference": "REQ-9",
    }
    assert body["terminology_request"]["expected_release"] == "2027-06-30"
    # What was asked for is as the move in left it.
    assert body["terminology_request"]["proposed_fsn"] == "Serum sodium level (observable entity)"
    assert body["terminology_request"]["raised_date"] == "2026-10-01"
    record = _record(api, submission)
    assert record is not None
    assert (record.external_reference_system, record.external_reference_ref) == (
        "new-system",
        "REQ-9",
    )
    created, updated = _record_events(api, submission)
    assert created.action == "submission.terminology_request_created"
    assert updated.action == "submission.terminology_request_updated"
    assert updated.before == {
        "external_reference_ref": "REQ-1",
        "external_reference_system": "snomed-request-service",
        "expected_release": "2027-03-31",
    }
    assert updated.after == {
        "external_reference_ref": "REQ-9",
        "external_reference_system": "new-system",
        "expected_release": "2027-06-30",
    }
    assert updated.reason == "Moved to the next release."
    assert _state(api, submission) == "Awaiting terminology"


@pytest.mark.req("FR-31")
@pytest.mark.integration
def test_each_tracking_field_changes_on_its_own_and_the_release_can_be_cleared(
    api: ApiTestApp,
) -> None:
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.REVIEWER)

    only_release = _track(api, token, submission.id, expected_release="2028-01-01")
    only_reference = _track(
        api,
        token,
        submission.id,
        expected_row_version=3,
        external_reference={"system": "other", "reference": "REQ-2"},
    )
    cleared = _track(api, token, submission.id, expected_row_version=4, expected_release=None)

    assert only_release.status_code == 200, only_release.text
    assert only_release.json()["terminology_request"]["external_reference"]["reference"] == "REQ-1"
    assert only_reference.status_code == 200, only_reference.text
    assert only_reference.json()["terminology_request"]["expected_release"] == "2028-01-01"
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["terminology_request"]["expected_release"] is None
    assert cleared.json()["terminology_request"]["external_reference"]["system"] == "other"
    assert cleared.json()["row_version"] == 5


@pytest.mark.req("FR-31")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_change_that_alters_nothing_writes_nothing_and_says_so(api: ApiTestApp) -> None:
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.REVIEWER)

    response = _track(
        api,
        token,
        submission.id,
        external_reference={"system": "snomed-request-service", "reference": "REQ-1"},
        expected_release="2027-03-31",
    )

    assert response.status_code == 200, response.text
    assert response.json()["changed"] is False
    assert response.json()["row_version"] == 2
    _assert_untracked(api, submission)


@pytest.mark.req("FR-31")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("fields", "field"),
    [
        (
            {"external_reference": {"system": " ", "reference": "REQ-2"}},
            "external_reference_system",
        ),
        ({"external_reference": {"system": "svc", "reference": ""}}, "external_reference_ref"),
        (
            {"external_reference": {"system": f"s{ZERO_WIDTH_SPACE}", "reference": "REQ-2"}},
            "external_reference_system",
        ),
        (
            {"external_reference": {"system": "svc", "reference": f"R{ZERO_WIDTH_SPACE}"}},
            "external_reference_ref",
        ),
    ],
    ids=["blank_system", "empty_reference", "invisible_in_system", "invisible_in_reference"],
)
def test_a_reference_part_that_is_blank_or_unclean_is_a_422_that_names_it(
    api: ApiTestApp, fields: dict[str, object], field: str
) -> None:
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.REVIEWER)

    response = _track(api, token, submission.id, **fields)

    assert response.status_code == 422, response.text
    assert response.json()["field"] == field
    _assert_untracked(api, submission)


@pytest.mark.req("FR-31")
@pytest.mark.integration
@pytest.mark.parametrize("reason", ["   ", f"Done{ZERO_WIDTH_SPACE}"], ids=["blank", "invisible"])
def test_a_blank_or_unclean_reason_is_a_422_on_the_tracking_change(
    api: ApiTestApp, reason: str
) -> None:
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.REVIEWER)

    response = _track(api, token, submission.id, reason=reason, expected_release="2028-01-01")

    assert response.status_code == 422, response.text
    assert response.json()["field"] == "reason"
    _assert_untracked(api, submission)


@pytest.mark.req("FR-31")
@pytest.mark.integration
@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"external_reference": None},
        {"external_reference": {"system": "svc"}},
        {"external_reference": {"reference": "REQ-2"}},
        {"expected_release": "next spring"},
        {"expected_release": "2028-01-01", "proposed_fsn": "Something else"},
        {"expected_release": "2028-01-01", "target_edition": "int"},
        {"expected_release": "2028-01-01", "justification": "Different"},
        {"expected_release": "2028-01-01", "raised_date": "2026-11-01"},
    ],
    ids=[
        "nothing_sent",
        "reference_null",
        "reference_without_reference",
        "reference_without_system",
        "release_not_a_date",
        "fsn_is_fixed",
        "edition_is_fixed",
        "justification_is_fixed",
        "raised_date_is_fixed",
    ],
)
def test_a_tracking_body_that_fails_validation_is_a_422_and_what_was_asked_for_is_fixed(
    api: ApiTestApp, fields: dict[str, object]
) -> None:
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.REVIEWER)

    response = _track(api, token, submission.id, **fields)

    assert response.status_code == 422, response.text
    _assert_untracked(api, submission)


@pytest.mark.req("FR-31")
@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize("role", _NON_REVIEWING_ROLES)
def test_a_role_without_the_review_permission_cannot_change_the_tracking_fields(
    api: ApiTestApp, role: Role
) -> None:
    submission = _awaiting(api)
    token, _ = _sign_in(api, role)

    response = _track(api, token, submission.id, expected_release="2028-01-01")

    assert response.status_code == 403, response.text
    _assert_untracked(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
def test_the_submitter_cannot_change_the_tracking_fields_of_their_own_submission(
    api: ApiTestApp,
) -> None:
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    member_token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)
    assert _request(api, reviewer_token, submission.id).status_code == 200

    response = _track(api, member_token, submission.id, expected_release="2028-01-01")

    assert response.status_code == 403, response.text
    _assert_untracked(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_anonymous_caller_is_refused_the_tracking_change_with_401(api: ApiTestApp) -> None:
    submission = _awaiting(api)

    response = _track(api, None, submission.id, expected_release="2028-01-01")

    assert response.status_code == 401, response.text
    _assert_untracked(api, submission)


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_reviewer_who_has_not_accepted_the_current_terms_cannot_track(api: ApiTestApp) -> None:
    submission = _awaiting(api)
    token, reviewer = _sign_in(api, Role.REVIEWER, accept_terms=False)

    refused = _track(api, token, submission.id, expected_release="2028-01-01")

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == TERMS_ACCEPTANCE_REQUIRED_CODE
    _assert_untracked(api, submission)

    api.accept_current_terms(reviewer.id)
    assert _track(api, token, submission.id, expected_release="2028-01-01").status_code == 200


@pytest.mark.req("FR-31")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Submitted", "Triage", "Under review", "Approved", "Withdrawn"])
def test_a_submission_that_is_not_awaiting_terminology_has_no_request_to_change(
    api: ApiTestApp, state: str
) -> None:
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1], state=state)
    token, _ = _sign_in(api, Role.REVIEWER)

    response = _track(
        api, token, submission.id, expected_row_version=1, expected_release="2028-01-01"
    )

    assert response.status_code == 409, response.text
    assert _record(api, submission) is None
    assert _all_events(api, submission) == []


@pytest.mark.req("FR-31")
@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_the_request_is_frozen_once_the_code_is_attached(api: ApiTestApp) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    admin_token, _ = _sign_in(api, Role.ADMINISTRATOR)
    assert _attach(api, admin_token, submission.id).status_code == 200

    response = _track(
        api, admin_token, submission.id, expected_row_version=3, expected_release="2028-01-01"
    )

    assert response.status_code == 409, response.text
    record = _record(api, submission)
    assert record is not None and str(record.expected_release) == "2027-03-31"
    assert [event.action for event in _record_events(api, submission)] == [
        "submission.terminology_request_created"
    ]


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-31")
@pytest.mark.integration
def test_a_stale_row_version_is_a_409_that_carries_the_current_one_on_the_tracking_change(
    api: ApiTestApp,
) -> None:
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.REVIEWER)

    response = _track(api, token, submission.id, expected_row_version=1, expected_release=None)

    assert response.status_code == 409, response.text
    assert response.json()["current_row_version"] == 2
    _assert_untracked(api, submission)


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-31")
@pytest.mark.integration
def test_a_tracking_change_and_the_attach_cannot_cross(api: ApiTestApp) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    admin_token, _ = _sign_in(api, Role.ADMINISTRATOR)
    assert (
        _track(api, reviewer_token, submission.id, expected_release="2028-01-01").status_code == 200
    )

    stale = _attach(api, admin_token, submission.id, expected_row_version=2)
    current = _attach(api, admin_token, submission.id, expected_row_version=3)

    assert stale.status_code == 409, stale.text
    assert stale.json()["current_row_version"] == 3
    assert current.status_code == 200, current.text


@pytest.mark.req("FR-31")
@pytest.mark.integration
def test_an_unknown_submission_is_a_404_and_a_malformed_identifier_a_422_on_the_tracking_change(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)

    assert _track(api, token, uuid.uuid4(), expected_release=None).status_code == 404
    assert _track(api, token, "not-a-uuid", expected_release=None).status_code == 422


# --- the working copy while the submission waits (FR-23, FR-30) ----------------------------------


def _edit_working_copy(
    api: ApiTestApp,
    token: str,
    submission_id: object,
    *,
    expected_row_version: int = 2,
    **fields: object,
) -> Any:
    return api.request(
        "PATCH",
        f"/submissions/{submission_id}/working-copy",
        token=token,
        json={"reason": "Corrected.", "expected_row_version": expected_row_version, **fields},
    )


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-30")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REVIEWING_ROLES)
def test_a_reviewing_role_may_edit_the_working_copy_while_the_submission_waits(
    api: ApiTestApp, role: Role
) -> None:
    submission = _awaiting(api)
    token, _ = _sign_in(api, role)

    response = _edit_working_copy(api, token, submission.id, notes="Raised with the authority")

    assert response.status_code == 200, response.text
    assert response.json()["notes"] == "Raised with the authority"
    assert _state(api, submission) == "Awaiting terminology"


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-82")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REVIEWING_ROLES)
def test_no_edit_sets_the_code_while_the_submission_waits(api: ApiTestApp, role: Role) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, _ = _sign_in(api, role)
    calls_before = len(api.terminology.requests)

    response = _edit_working_copy(api, token, submission.id, snomed_code=_CODE)

    assert response.status_code == 422, response.text
    assert response.json()["field"] == "snomed_code"
    assert "Administrator" in response.json()["detail"]
    assert len(api.terminology.requests) == calls_before
    assert _working_copy(api, submission) is None


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_an_edit_that_leaves_the_code_out_is_not_blocked_by_the_wait(api: ApiTestApp) -> None:
    _seed_concepts(api)
    submission = _awaiting(api)
    token, _ = _sign_in(api, Role.REVIEWER)

    response = _edit_working_copy(
        api, token, submission.id, preferred_term="Reworded term", synonyms=["Reworded synonym"]
    )

    assert response.status_code == 200, response.text
    assert response.json()["snomed_code"] is None


# --- who sees the request (FR-30, FR-44) -------------------------------------------------------


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REVIEWING_ROLES)
def test_a_reviewing_role_sees_the_request_in_the_detail(api: ApiTestApp, role: Role) -> None:
    submission = _awaiting(api)
    token, _ = _sign_in(api, role)

    body = api.get(f"/submissions/{submission.id}", token=token).json()

    request = body["terminology_request"]
    assert request["proposed_fsn"] == "Serum sodium level (observable entity)"
    assert request["target_edition"] == "au"
    assert request["justification"] == "No concept covers this measurement."
    assert request["external_reference"] == {
        "system": "snomed-request-service",
        "reference": "REQ-1",
    }
    assert request["raised_date"] == "2026-10-01"
    assert request["expected_release"] == "2027-03-31"
    assert body["state"] == "Awaiting terminology"


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_reviewer_sees_null_when_the_submission_never_waited(api: ApiTestApp) -> None:
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    token, _ = _sign_in(api, Role.REVIEWER)

    body = api.get(f"/submissions/{submission.id}", token=token).json()

    assert "terminology_request" in body
    assert body["terminology_request"] is None


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", _NON_REVIEWING_ROLES)
def test_a_role_without_the_review_permission_never_sees_the_request(
    api: ApiTestApp, role: Role
) -> None:
    submission = _awaiting(api)
    token, _ = _sign_in(api, role)

    response = api.get(f"/submissions/{submission.id}", token=token)

    assert response.status_code == 200, response.text
    assert "terminology_request" not in response.json()
    assert "proposed_fsn" not in response.text
    assert "REQ-1" not in response.text


@pytest.mark.req("FR-30")
@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_the_submitter_sees_the_state_and_never_the_request(api: ApiTestApp) -> None:
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    member_token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)
    assert _request(api, reviewer_token, submission.id).status_code == 200

    response = api.get(f"/submissions/{submission.id}", token=member_token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "Awaiting terminology"
    assert "terminology_request" not in body
    assert "working_copy" not in body
    assert "snomed-request-service" not in response.text
    assert "No concept covers this measurement." not in response.text


@pytest.mark.req("FR-30")
@pytest.mark.integration
def test_the_list_never_carries_the_request(api: ApiTestApp) -> None:
    _awaiting(api)
    token, _ = _sign_in(api, Role.REVIEWER)

    response = api.get("/submissions", token=token, params={"state": "Awaiting terminology"})

    assert response.status_code == 200, response.text
    assert response.json()["items"]
    assert all("terminology_request" not in item for item in response.json()["items"])
