"""`GET /api/v1/submissions/{id}/interest` and the list's `distinct_organisation_count`
(FR-33, FR-34, FR-44, NFR-08, NFR-17).

The real app over a stub identity provider (`api_app_support`). Submissions and interest records
are inserted directly. `backend/tests` shares one Postgres container, so every assertion is scoped
to a submission this test made, and no test counts a whole table.
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

from aptc.api.dependencies import current_principal
from aptc.audit.writer import AuditContext
from aptc.auth.identity import close_account
from aptc.auth.permissions import Permission, Role
from aptc.auth.principal import Principal
from aptc.db.models.audit import AuditEvent
from aptc.db.models.submission import CLOSED_SUBMISSION_STATES, Submission, SubmissionKind
from aptc.db.models.submission_interest import SubmissionInterest
from aptc.db.models.user import User
from aptc.db.models.user_identity import UserIdentity
from aptc.submissions.interest_identities import NO_ORGANISATION_LABEL


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
_ALLOWED_ROLES = [Role.REVIEWER, Role.ADMINISTRATOR]
_REFUSED_ROLES = [Role.MEMBER, Role.OBSERVER, Role.PROVISIONAL]


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _sign_in(api: ApiTestApp, role: Role, *, display_name: str = "Test Person") -> tuple[str, User]:
    """A token holding exactly `role`, and the user it belongs to. Each call is a distinct user."""
    subject = f"sub-identities-{uuid.uuid4().hex[:10]}"
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
    user.display_name = display_name
    api.session.flush()
    return token, user


def _submission(api: ApiTestApp, *, state: str = "Submitted") -> Submission:
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = Submission(
        kind=SubmissionKind.NEW_TEST.value,
        state=state,
        preferred_term=f"Identities submission {uuid.uuid4()}",
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=submitter.id,
        organisation="Submitter Lab",
    )
    api.session.add(submission)
    api.session.flush()
    return submission


def _register(
    api: ApiTestApp,
    submission: Submission,
    organisation: str | None,
    *,
    revoked: bool = False,
    display_name: str = "Test Person",
) -> User:
    """A new user's live interest carrying `organisation` as its copy."""
    _, user = _sign_in(api, Role.MEMBER, display_name=display_name)
    api.session.add(
        SubmissionInterest(
            submission_id=submission.id,
            user_id=user.id,
            organisation=organisation,
            revoked_at=datetime.now(UTC) if revoked else None,
        )
    )
    api.session.flush()
    return user


def _identities(api: ApiTestApp, token: str | None, submission_id: object) -> Any:
    return api.get(f"/submissions/{submission_id}/interest", token=token)


def _groups(body: dict[str, Any]) -> dict[str | None, int]:
    return {group["organisation"]: group["count"] for group in body["distinct_organisations"]}


# --- who may see the identities (FR-34, FR-44) ----------------------------------------------


@pytest.mark.req("FR-34")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REFUSED_ROLES)
def test_a_caller_without_the_identity_permission_gets_403_and_no_identity(
    api: ApiTestApp, role: Role
) -> None:
    submission = _submission(api)
    registrant = _register(api, submission, "Secret Pathology", display_name="Hidden Name")
    token, _ = _sign_in(api, role)

    response = _identities(api, token, submission.id)

    assert response.status_code == 403
    assert "Secret Pathology" not in response.text
    assert "Hidden Name" not in response.text
    assert registrant.username is None or registrant.username not in response.text


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_the_permission_is_checked_before_the_submission_is_looked_up(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)

    assert _identities(api, token, uuid.uuid4()).status_code == 403


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_an_anonymous_caller_gets_401(api: ApiTestApp) -> None:
    submission = _submission(api)

    assert _identities(api, None, submission.id).status_code == 401


@pytest.mark.req("FR-33")
@pytest.mark.integration
@pytest.mark.parametrize("role", _ALLOWED_ROLES)
def test_a_reviewer_and_an_administrator_see_the_live_records(api: ApiTestApp, role: Role) -> None:
    submission = _submission(api)
    _register(api, submission, "Royal Perth Hospital", display_name="Ada Lovelace")
    token, _ = _sign_in(api, role)

    response = _identities(api, token, submission.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["interest_count"] == 1
    [record] = body["records"]
    assert record["display_name"] == "Ada Lovelace"
    assert record["username"]
    assert record["organisation"] == "Royal Perth Hospital"
    assert datetime.fromisoformat(record["registered_at"]).tzinfo is not None


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_no_response_carries_a_user_id(api: ApiTestApp) -> None:
    submission = _submission(api)
    registrant = _register(api, submission, "Some Lab")
    token, _ = _sign_in(api, Role.REVIEWER)

    response = _identities(api, token, submission.id)

    assert str(registrant.id) not in response.text


# --- distinct organisations (FR-33) ---------------------------------------------------------


@pytest.mark.req("FR-33")
@pytest.mark.integration
def test_three_registrations_from_two_organisations_are_two_groups_of_two_and_one(
    api: ApiTestApp,
) -> None:
    submission = _submission(api)
    _register(api, submission, "Lab A")
    _register(api, submission, "Lab A")
    _register(api, submission, "Lab B")
    token, _ = _sign_in(api, Role.REVIEWER)

    body = _identities(api, token, submission.id).json()

    assert body["interest_count"] == 3
    assert body["distinct_organisation_count"] == 2
    assert [(g["organisation"], g["count"]) for g in body["distinct_organisations"]] == [
        ("Lab A", 2),
        ("Lab B", 1),
    ]


@pytest.mark.req("FR-33")
@pytest.mark.integration
def test_spellings_that_differ_by_case_or_space_are_one_organisation(api: ApiTestApp) -> None:
    submission = _submission(api)
    _register(api, submission, "Royal Perth Hospital")
    _register(api, submission, " royal perth hospital ")
    _register(api, submission, "ROYAL PERTH HOSPITAL")
    _register(api, submission, "Royal Perth Hospital")
    token, _ = _sign_in(api, Role.REVIEWER)

    body = _identities(api, token, submission.id).json()

    assert _groups(body) == {"Royal Perth Hospital": 4}
    assert body["distinct_organisation_count"] == 1


@pytest.mark.req("FR-33")
@pytest.mark.integration
def test_tabs_and_line_breaks_around_an_organisation_are_trimmed_too(api: ApiTestApp) -> None:
    submission = _submission(api)
    _register(api, submission, "\tLab A\n")
    _register(api, submission, "Lab A")
    _register(api, submission, "\t\r\n ")
    token, _ = _sign_in(api, Role.REVIEWER)

    body = _identities(api, token, submission.id).json()

    assert _groups(body) == {"Lab A": 2, None: 1}


@pytest.mark.req("FR-33")
@pytest.mark.integration
def test_a_tie_between_spellings_goes_to_the_one_that_sorts_first(api: ApiTestApp) -> None:
    submission = _submission(api)
    _register(api, submission, "pathology north")
    _register(api, submission, "Pathology North")
    token, _ = _sign_in(api, Role.REVIEWER)

    body = _identities(api, token, submission.id).json()

    assert _groups(body) == {"Pathology North": 2}


@pytest.mark.req("FR-33")
@pytest.mark.integration
def test_no_organisation_is_one_group_apart_from_every_named_one(api: ApiTestApp) -> None:
    submission = _submission(api)
    _register(api, submission, None)
    _register(api, submission, "")
    _register(api, submission, "   ")
    _register(api, submission, NO_ORGANISATION_LABEL)
    token, _ = _sign_in(api, Role.REVIEWER)

    body = _identities(api, token, submission.id).json()

    assert body["distinct_organisations"] == [
        {"organisation": NO_ORGANISATION_LABEL, "label": NO_ORGANISATION_LABEL, "count": 1},
        {"organisation": None, "label": NO_ORGANISATION_LABEL, "count": 3},
    ]
    assert body["distinct_organisation_count"] == 1


@pytest.mark.req("FR-33")
@pytest.mark.integration
def test_the_largest_group_comes_first_and_the_unnamed_group_last(api: ApiTestApp) -> None:
    submission = _submission(api)
    for _ in range(3):
        _register(api, submission, None)
    _register(api, submission, "Small Lab")
    _register(api, submission, "Big Lab")
    _register(api, submission, "Big Lab")
    token, _ = _sign_in(api, Role.REVIEWER)

    body = _identities(api, token, submission.id).json()

    assert [g["label"] for g in body["distinct_organisations"]] == [
        "Big Lab",
        "Small Lab",
        NO_ORGANISATION_LABEL,
    ]


@pytest.mark.req("FR-33")
@pytest.mark.integration
def test_a_submission_nobody_registered_in_has_empty_lists(api: ApiTestApp) -> None:
    submission = _submission(api)
    token, _ = _sign_in(api, Role.REVIEWER)

    body = _identities(api, token, submission.id).json()

    assert body == {
        "interest_count": 0,
        "distinct_organisation_count": 0,
        "distinct_organisations": [],
        "records": [],
    }


@pytest.mark.req("FR-33")
@pytest.mark.integration
def test_a_revoked_record_appears_in_neither_the_records_nor_the_groups(
    api: ApiTestApp,
) -> None:
    submission = _submission(api)
    _register(api, submission, "Staying Lab")
    _register(api, submission, "Leaving Lab", revoked=True)
    token, _ = _sign_in(api, Role.REVIEWER)

    body = _identities(api, token, submission.id).json()

    assert _groups(body) == {"Staying Lab": 1}
    assert [r["organisation"] for r in body["records"]] == ["Staying Lab"]
    assert body["interest_count"] == 1


@pytest.mark.req("FR-33")
@pytest.mark.integration
def test_a_person_who_registered_again_after_revoking_counts_once(api: ApiTestApp) -> None:
    submission = _submission(api)
    user = _register(api, submission, "Old Lab", revoked=True)
    api.session.add(
        SubmissionInterest(submission_id=submission.id, user_id=user.id, organisation="New Lab")
    )
    api.session.flush()
    token, _ = _sign_in(api, Role.REVIEWER)

    body = _identities(api, token, submission.id).json()

    assert _groups(body) == {"New Lab": 1}


@pytest.mark.req("FR-33")
@pytest.mark.integration
@pytest.mark.parametrize("state", CLOSED_SUBMISSION_STATES)
def test_interest_in_a_closed_submission_is_still_returned(api: ApiTestApp, state: str) -> None:
    submission = _submission(api, state=state)
    _register(api, submission, "Lab A")
    token, _ = _sign_in(api, Role.REVIEWER)

    body = _identities(api, token, submission.id).json()

    assert _groups(body) == {"Lab A": 1}


# --- a closed account (NFR-17) --------------------------------------------------------------


@pytest.mark.req("NFR-17")
@pytest.mark.req("FR-33")
@pytest.mark.integration
def test_a_closed_account_shows_the_placeholder_and_still_counts_under_its_organisation(
    api: ApiTestApp,
) -> None:
    submission = _submission(api)
    closed = _register(api, submission, "Lab A", display_name="Gone Person")
    closed_username = closed.username
    _register(api, submission, "Lab A")
    close_account(api.session, closed.id, AuditContext.system())
    api.session.flush()
    token, _ = _sign_in(api, Role.REVIEWER)

    response = _identities(api, token, submission.id)

    body = response.json()
    assert _groups(body) == {"Lab A": 2}
    placeholders = [r for r in body["records"] if r["username"] == _PLACEHOLDER]
    assert len(placeholders) == 1
    assert placeholders[0]["display_name"] == _PLACEHOLDER
    assert placeholders[0]["organisation"] == "Lab A"
    assert "Gone Person" not in response.text
    assert closed_username is not None
    assert closed_username not in response.text


# --- not found, and a read writes nothing ---------------------------------------------------


@pytest.mark.req("FR-33")
@pytest.mark.integration
def test_an_unknown_submission_is_a_404_and_a_malformed_id_a_422(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)

    assert _identities(api, token, uuid.uuid4()).status_code == 404
    assert _identities(api, token, "not-a-uuid").status_code == 422


@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_reading_the_identities_appends_no_audit_event(api: ApiTestApp) -> None:
    submission = _submission(api)
    _register(api, submission, "Lab A")
    token, _ = _sign_in(api, Role.REVIEWER)
    # The first request with a token syncs the profile from its claims, which is itself audited.
    assert _identities(api, token, submission.id).status_code == 200
    before = api.session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()

    assert _identities(api, token, submission.id).status_code == 200

    after = api.session.execute(select(func.count()).select_from(AuditEvent)).scalar_one()
    assert after - before == 0


# --- the list's distinct_organisation_count (FR-33, FR-34) ----------------------------------


def _listed_item(api: ApiTestApp, token: str, submission: Submission) -> dict[str, Any]:
    response = api.get("/submissions", token=token, params={"limit": 200})
    assert response.status_code == 200, response.text
    return next(item for item in response.json()["items"] if item["id"] == str(submission.id))


@pytest.mark.req("FR-33")
@pytest.mark.integration
@pytest.mark.parametrize("role", _ALLOWED_ROLES)
def test_the_list_carries_the_count_for_a_caller_with_the_identity_permission(
    api: ApiTestApp, role: Role
) -> None:
    submission = _submission(api)
    _register(api, submission, "Lab A")
    _register(api, submission, " lab a")
    _register(api, submission, "Lab B")
    _register(api, submission, None)
    token, _ = _sign_in(api, role)

    item = _listed_item(api, token, submission)

    assert item["distinct_organisation_count"] == 2
    detail = _identities(api, token, submission.id).json()
    assert item["distinct_organisation_count"] == detail["distinct_organisation_count"]


@pytest.mark.req("FR-33")
@pytest.mark.integration
def test_the_list_count_is_zero_when_nobody_named_an_organisation(api: ApiTestApp) -> None:
    quiet = _submission(api)
    unnamed = _submission(api)
    _register(api, unnamed, None)
    token, _ = _sign_in(api, Role.REVIEWER)

    assert _listed_item(api, token, quiet)["distinct_organisation_count"] == 0
    assert _listed_item(api, token, unnamed)["distinct_organisation_count"] == 0


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_the_list_count_ignores_revoked_records(api: ApiTestApp) -> None:
    submission = _submission(api)
    _register(api, submission, "Lab A")
    _register(api, submission, "Lab B", revoked=True)
    token, _ = _sign_in(api, Role.REVIEWER)

    assert _listed_item(api, token, submission)["distinct_organisation_count"] == 1


@pytest.mark.req("FR-34")
@pytest.mark.integration
@pytest.mark.parametrize("role", [Role.MEMBER, Role.OBSERVER, Role.PROVISIONAL])
def test_every_other_caller_gets_no_organisation_count_key_at_all(
    api: ApiTestApp, role: Role
) -> None:
    submission = _submission(api)
    _register(api, submission, "Lab A")
    token, _ = _sign_in(api, role)

    response = api.get("/submissions", token=token, params={"limit": 200})

    assert response.status_code == 200
    item = next(i for i in response.json()["items"] if i["id"] == str(submission.id))
    assert "distinct_organisation_count" not in item
    assert "distinct_organisation_count" not in response.text


@pytest.mark.req("FR-34")
@pytest.mark.integration
def test_the_detail_does_not_carry_the_organisation_count(api: ApiTestApp) -> None:
    submission = _submission(api)
    _register(api, submission, "Lab A")
    token, _ = _sign_in(api, Role.REVIEWER)

    response = api.get(f"/submissions/{submission.id}", token=token)

    assert response.status_code == 200
    assert "distinct_organisation_count" not in response.json()


@pytest.mark.req("FR-33")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("extra", "has_count", "has_name"),
    [
        (Permission.INTEREST_IDENTITIES_VIEW, True, False),
        (Permission.SUBMITTER_IDENTITY_VIEW, False, True),
    ],
)
def test_each_permission_alone_chooses_its_own_part_of_the_list_item(
    api: ApiTestApp, extra: Permission, has_count: bool, has_name: bool
) -> None:
    """The two permissions are independent, though every role holds both or neither today, so a
    caller is built directly. Whichever class the response model picks, a key the caller may see
    must survive it, and one they may not see must be absent."""
    submission = _submission(api)
    _register(api, submission, "Lab A")
    _register(api, submission, "Lab B")
    token, viewer = _sign_in(api, Role.MEMBER)

    api.app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=viewer.id,
        user_ref=None,
        status=viewer.status,
        roles=frozenset({Role.MEMBER}),
        permissions=frozenset({Permission.SUBMISSION_VIEW, extra}),
        mfa_satisfied=True,
        mfa_suppressed_roles=frozenset(),
    )
    try:
        item = _listed_item(api, token, submission)
    finally:
        del api.app.dependency_overrides[current_principal]

    assert ("distinct_organisation_count" in item) is has_count
    assert ("display_name" in item["submitter"]) is has_name
    if has_count:
        assert item["distinct_organisation_count"] == 2
