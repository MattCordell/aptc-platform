"""`GET /api/v1/submissions` and `GET /api/v1/submissions/{id}` (FR-28, FR-42, NFR-20).

The real app over a stub identity provider (`api_app_support`). Submissions are inserted directly,
because no write path reaches a closed state yet. `backend/tests` shares one Postgres container, so
every assertion is scoped to rows this test created: a list is read back by the ids it was given,
and the paging tests read `mine=true` for a user the test made.
"""

from __future__ import annotations

import importlib.util
import random
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.identity import close_account
from nptc.auth.permissions import Role
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.submission import (
    CLOSED_SUBMISSION_STATES,
    Submission,
    SubmissionKind,
    SubmissionState,
)
from nptc.db.models.user import User, UserStatus
from nptc.db.models.user_identity import UserIdentity
from nptc.submissions.queries import list_submissions


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

_PATH = "/submissions"
_PLACEHOLDER = "Closed account"
_ORGANISATION_COPY = "Copy Pathology Services"
_DISPLAY_NAME = "Ada Submitter"
_IDENTITY_KEYS = {"display_name", "organisation"}
_IDENTITY_ROLES = [Role.REVIEWER, Role.ADMINISTRATOR]
_PLAIN_ROLES = [Role.OBSERVER, Role.PROVISIONAL, Role.MEMBER]


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _sign_in(api: ApiTestApp, role: Role) -> tuple[str, User]:
    """A token holding exactly `role`, and the user it belongs to. The user has a display name and a
    profile organisation, so a response that leaked either would show it."""
    subject = f"sub-read-{uuid.uuid4().hex[:10]}"
    token = api.token_for_role(
        subject=subject, role=role, replace_roles=True, email=f"{subject}@example.test"
    )
    user = api.session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .where(UserIdentity.subject == subject)
    ).scalar_one()
    user.display_name = _DISPLAY_NAME
    user.organisation = "Profile Pathology"
    api.session.flush()
    return token, user


def _add(
    api: ApiTestApp,
    submitter: User,
    *,
    state: str = SubmissionState.SUBMITTED.value,
    kind: SubmissionKind = SubmissionKind.NEW_TEST,
    created_at: datetime | None = None,
    entry: CatalogueEntry | None = None,
    **fields: Any,
) -> Submission:
    is_new_test = kind is SubmissionKind.NEW_TEST
    when = created_at or datetime.now(UTC)
    if kind is SubmissionKind.AMENDMENT and entry is None:
        entry = CatalogueEntry(
            business_key=f"NPTC-{random.randrange(100_000_000, 999_999_999)}",
            preferred_term=f"Read entry {uuid.uuid4()}",
            status="active",
        )
        api.session.add(entry)
        api.session.flush()
    values: dict[str, Any] = {
        "kind": kind.value,
        "state": state,
        "preferred_term": f"Read submission {uuid.uuid4()}",
        "submitter_id": submitter.id,
        "organisation": _ORGANISATION_COPY,
        "entry_id": entry.id if entry else None,
        "reference_url": "https://example.org/evidence" if is_new_test else None,
        "reference_checked_at": when if is_new_test else None,
        "reference_status": 200 if is_new_test else None,
        "created_at": when,
    }
    submission = Submission(**{**values, **fields})
    api.session.add(submission)
    api.session.flush()
    return submission


def _listed(api: ApiTestApp, token: str, **params: Any) -> list[dict[str, Any]]:
    response = api.get(_PATH, token=token, params={"limit": 200, **params})
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    return items


def _ids(items: list[dict[str, Any]], *submissions: Submission) -> set[str]:
    """The ids in `items` that belong to `submissions`, so another test's rows never count."""
    wanted = {str(submission.id) for submission in submissions}
    return {item["id"] for item in items} & wanted


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _all_keys(item)}
    return set()


# --- the default list is the open submissions (FR-28) ---------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_default_list_omits_every_closed_state_and_keeps_the_rest(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    open_one = _add(api, member)
    closed = [_add(api, member, state=state) for state in CLOSED_SUBMISSION_STATES]

    items = _listed(api, token)

    assert _ids(items, open_one) == {str(open_one.id)}
    assert _ids(items, *closed) == set()


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_default_list_is_one_statement_that_excludes_closed_states_by_name(
    api: ApiTestApp, app_db: Connection, capture_statements: Any
) -> None:
    """A state added later must count as open without a change here, so the statement must say
    `NOT IN` over the closed states. The `state` check stops a test storing a state the code does
    not name, so the shape of the SQL is the evidence. One statement serves the page, joins
    included, so a page costs the same however many rows it holds."""
    token, member = _sign_in(api, Role.MEMBER)
    rows = [_add(api, member), _add(api, member, kind=SubmissionKind.AMENDMENT), _add(api, member)]

    with capture_statements(app_db, lambda statement, _: "FROM submission" in statement) as seen:
        items = _listed(api, token)

    assert len(_ids(items, *rows)) == len(rows)
    assert len(seen) == 1
    assert "submission.state NOT IN" in seen[0]
    assert "submission.state IN" not in seen[0].replace("NOT IN", "")


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_caller_can_ask_for_closed_states_by_name(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submitted = _add(api, member)
    rejected = _add(api, member, state=SubmissionState.REJECTED.value)
    withdrawn = _add(api, member, state=SubmissionState.WITHDRAWN.value)

    only_rejected = _listed(api, token, state="Rejected")
    two_states = _listed(api, token, state=["Rejected", "Withdrawn"])

    assert _ids(only_rejected, submitted, rejected, withdrawn) == {str(rejected.id)}
    assert _ids(two_states, submitted, rejected, withdrawn) == {
        str(rejected.id),
        str(withdrawn.id),
    }


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_unknown_state_is_a_422(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)

    response = api.get(_PATH, token=token, params={"state": "Approved"})

    assert response.status_code == 422


@pytest.mark.req("FR-42")
@pytest.mark.integration
def test_mine_returns_only_the_callers_rows_in_every_state(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    _, other = _sign_in(api, Role.MEMBER)
    mine = [
        _add(api, member),
        _add(api, member, state=SubmissionState.REJECTED.value),
        _add(api, member, state=SubmissionState.PUBLISHED_IN_RELEASE.value),
    ]
    theirs = _add(api, other)

    items = _listed(api, token, mine="true")

    assert {item["id"] for item in items} == {str(s.id) for s in mine}
    assert str(theirs.id) not in {item["id"] for item in items}
    assert all(item["is_own"] for item in items)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_kind_filter_keeps_one_kind(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    new_test = _add(api, member)
    amendment = _add(api, member, kind=SubmissionKind.AMENDMENT)

    new_tests = _listed(api, token, mine="true", kind="new_test")
    amendments = _listed(api, token, mine="true", kind="amendment")
    refused = api.get(_PATH, token=token, params={"kind": "withdrawal"})

    assert {item["id"] for item in new_tests} == {str(new_test.id)}
    assert {item["id"] for item in amendments} == {str(amendment.id)}
    assert refused.status_code == 422


# --- order and paging (ADR-0024) -------------------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_pages_are_newest_first_and_cover_every_row_once(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    now = datetime.now(UTC).replace(microsecond=0)
    tied = now - timedelta(hours=1)
    rows = [
        _add(api, member, created_at=now - timedelta(hours=3)),
        _add(api, member, created_at=tied),
        _add(api, member, created_at=tied),
        _add(api, member, created_at=now),
        _add(api, member, created_at=now - timedelta(hours=2)),
    ]
    expected = sorted(rows, key=lambda row: (row.created_at, row.id), reverse=True)

    served: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"mine": "true", "limit": 2}
        if cursor:
            params["after"] = cursor
        response = api.get(_PATH, token=token, params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        served += [item["id"] for item in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert pages == 3
    assert served == [str(row.id) for row in expected]


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_full_last_page_has_no_next_cursor(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    _add(api, member)
    _add(api, member)

    response = api.get(_PATH, token=token, params={"mine": "true", "limit": 2})

    assert len(response.json()["items"]) == 2
    assert response.json()["next_cursor"] is None


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor",
        "!!!!",
        # Valid base64 of text that is not `<timestamp>|<uuid>`.
        "aGVsbG8",
        # A naive timestamp with a real id.
        "MjAyNi0xMC0xM1QxMDowMDowMHw1ZjRkYjZhYS0wMDAwLTQwMDAtODAwMC0wMDAwMDAwMDAwMDA",
    ],
)
def test_a_cursor_this_api_did_not_issue_is_a_422(api: ApiTestApp, cursor: str) -> None:
    token, _ = _sign_in(api, Role.MEMBER)

    response = api.get(_PATH, token=token, params={"after": cursor})

    assert response.status_code == 422, response.text
    assert "cursor" in response.json()["detail"]


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("limit", [0, 201])
def test_a_limit_outside_the_range_is_a_422(api: ApiTestApp, limit: int) -> None:
    token, _ = _sign_in(api, Role.MEMBER)

    assert api.get(_PATH, token=token, params={"limit": limit}).status_code == 422


# --- the submitter-identity rule (FR-42, NFR-20) ---------------------------------------------


@pytest.mark.req("FR-42")
@pytest.mark.req("NFR-20")
@pytest.mark.integration
@pytest.mark.parametrize("role", _IDENTITY_ROLES)
def test_a_caller_with_the_identity_permission_sees_the_name_and_the_organisation_copy(
    api: ApiTestApp, role: Role
) -> None:
    token, _ = _sign_in(api, role)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _add(api, submitter)

    listed = next(i for i in _listed(api, token) if i["id"] == str(submission.id))
    detail = api.get(f"{_PATH}/{submission.id}", token=token)

    expected = {
        "username": submitter.username,
        "display_name": _DISPLAY_NAME,
        "organisation": _ORGANISATION_COPY,
    }
    assert listed["submitter"] == expected
    assert detail.status_code == 200, detail.text
    assert detail.json()["submitter"] == expected


@pytest.mark.req("FR-42")
@pytest.mark.req("NFR-20")
@pytest.mark.integration
@pytest.mark.parametrize("role", _PLAIN_ROLES)
def test_every_other_caller_gets_the_username_and_no_identity_key_at_all(
    api: ApiTestApp, role: Role
) -> None:
    token, _ = _sign_in(api, role)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _add(api, submitter)

    page = api.get(_PATH, token=token, params={"limit": 200})
    detail = api.get(f"{_PATH}/{submission.id}", token=token)

    assert page.status_code == 200, page.text
    listed = next(i for i in page.json()["items"] if i["id"] == str(submission.id))
    assert listed["submitter"] == {"username": submitter.username}
    assert detail.json()["submitter"] == {"username": submitter.username}
    for response in (page, detail):
        assert not _IDENTITY_KEYS & _all_keys(response.json())
        assert _DISPLAY_NAME not in response.text
        assert _ORGANISATION_COPY not in response.text


@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_no_response_carries_a_user_id(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _add(api, submitter)

    page = api.get(_PATH, token=token, params={"limit": 200})
    detail = api.get(f"{_PATH}/{submission.id}", token=token)

    assert str(submitter.id) not in page.text
    assert str(submitter.id) not in detail.text


@pytest.mark.req("FR-42")
@pytest.mark.integration
def test_a_closed_account_shows_the_placeholder_and_keeps_the_organisation_copy(
    api: ApiTestApp,
) -> None:
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    member_token, _ = _sign_in(api, Role.MEMBER)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _add(api, submitter)
    close_account(api.session, submitter.id, AuditContext.system())
    api.session.flush()

    for_reviewer = api.get(f"{_PATH}/{submission.id}", token=reviewer_token)
    for_member = api.get(f"{_PATH}/{submission.id}", token=member_token)
    listed = next(i for i in _listed(api, reviewer_token) if i["id"] == str(submission.id))

    expected = {
        "username": _PLACEHOLDER,
        "display_name": _PLACEHOLDER,
        "organisation": _ORGANISATION_COPY,
    }
    assert for_reviewer.json()["submitter"] == expected
    assert listed["submitter"] == expected
    assert for_member.json()["submitter"] == {"username": _PLACEHOLDER}
    assert _ORGANISATION_COPY not in for_member.text


@pytest.mark.req("FR-42")
@pytest.mark.integration
def test_is_own_is_true_for_the_submitter_alone(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    other_token, _ = _sign_in(api, Role.MEMBER)
    submission = _add(api, member)

    own = api.get(f"{_PATH}/{submission.id}", token=token)
    elsewhere = api.get(f"{_PATH}/{submission.id}", token=other_token)
    own_item = next(i for i in _listed(api, token) if i["id"] == str(submission.id))
    other_item = next(i for i in _listed(api, other_token) if i["id"] == str(submission.id))

    assert own.json()["is_own"] is True
    assert own_item["is_own"] is True
    assert elsewhere.json()["is_own"] is False
    assert other_item["is_own"] is False


# --- the detail ------------------------------------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_detail_carries_the_submitted_content_in_any_state(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    submission = _add(
        api,
        member,
        state=SubmissionState.WITHDRAWN.value,
        synonyms=["Na+", "Sodium level"],
        notes="Seen in a new panel",
        snomed_code="391483001",
        snomed_fsn="Microscopy (acid fast bacilli) (procedure)",
        property_values={"specimen": [{"value": {"code": "119364003"}, "justification": None}]},
    )

    body = api.get(f"{_PATH}/{submission.id}", token=token).json()

    assert body["state"] == "Withdrawn"
    assert body["kind"] == "new_test"
    assert body["entry_business_key"] is None
    assert body["synonyms"] == ["Na+", "Sodium level"]
    assert body["notes"] == "Seen in a new panel"
    assert body["reference_url"] == "https://example.org/evidence"
    assert body["reference_status"] == 200
    assert body["snomed_code"] == "391483001"
    assert body["property_values"]["specimen"][0]["value"] == {"code": "119364003"}
    assert set(body["label_provenance"]) == {"preferred_term", "synonyms", "snomed_fsn"}


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_names_the_entry_it_changes_by_business_key(api: ApiTestApp) -> None:
    token, member = _sign_in(api, Role.MEMBER)
    amendment = _add(api, member, kind=SubmissionKind.AMENDMENT)
    entry = api.session.get(CatalogueEntry, amendment.entry_id)
    assert entry is not None

    detail = api.get(f"{_PATH}/{amendment.id}", token=token).json()
    listed = next(i for i in _listed(api, token) if i["id"] == str(amendment.id))

    assert detail["kind"] == "amendment"
    assert detail["entry_business_key"] == entry.business_key
    assert listed["entry_business_key"] == entry.business_key


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_unknown_id_is_a_404_and_a_malformed_one_a_422(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.MEMBER)

    missing = api.get(f"{_PATH}/{uuid.uuid4()}", token=token)
    malformed = api.get(f"{_PATH}/not-a-uuid", token=token)

    assert missing.status_code == 404
    assert str(missing.json()["detail"]) and "submission" in missing.json()["detail"].lower()
    assert malformed.status_code == 422


# --- who may read (FR-44) --------------------------------------------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_anonymous_caller_gets_401_on_both_routes(api: ApiTestApp) -> None:
    assert api.get(_PATH).status_code == 401
    assert api.get(f"{_PATH}/{uuid.uuid4()}").status_code == 401


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_caller_without_the_view_permission_gets_403_on_both_routes(api: ApiTestApp) -> None:
    """A suspended account keeps only the anonymous read surface, which has no `submission.view`."""
    token, user = _sign_in(api, Role.MEMBER)
    submission = _add(api, user)
    user.status = UserStatus.SUSPENDED.value
    api.session.flush()

    assert api.get(_PATH, token=token).status_code == 403
    assert api.get(f"{_PATH}/{submission.id}", token=token).status_code == 403


@pytest.mark.req("FR-42")
@pytest.mark.integration
def test_mine_with_no_viewer_returns_nothing_rather_than_another_users_rows(
    app_session: Session,
) -> None:
    """Every holder of `submission.view` has a user row, so the route never passes no viewer. If it
    ever did, `mine` must fail closed: an empty page, never someone else's submissions."""
    user = User(username=f"mine-none-{uuid.uuid4().hex[:8]}", display_name="Mine None")
    app_session.add(user)
    app_session.flush()
    app_session.add(
        Submission(
            kind=SubmissionKind.NEW_TEST.value,
            preferred_term=f"Read submission {uuid.uuid4()}",
            submitter_id=user.id,
            reference_url="https://example.org/evidence",
            reference_checked_at=datetime.now(UTC),
            reference_status=200,
        )
    )
    app_session.flush()

    page = list_submissions(app_session, viewer_id=None, limit=200, mine=True)

    assert page.records == ()
    assert page.next_cursor is None
