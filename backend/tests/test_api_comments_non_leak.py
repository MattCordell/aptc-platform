"""Internal comments never show on another route (FR-95, FR-44, NFR-04, NFR-08).

A submitter must not learn that comments exist, or how many. Each test reads every route a
submitter, the public or another staff caller can reach before a comment exists, posts and then
withdraws one, and reads the same routes again. The responses must match byte for byte, so a new
field, a count, or a changed `row_version` or `updated_at` all fail here. The responses must also be
free of the comment's text and id.

The real app over a stub identity provider (`api_app_support`). `backend/tests` shares one Postgres
container, so every route read is scoped to the target this test made.
"""

from __future__ import annotations

import importlib.util
import random
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

from nptc.auth.permissions import Role
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.submission import Submission, SubmissionKind
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

_SECRET = "An unmistakable remark for staff only"
_REASON = "Posted on the wrong record"

#: A request is a path and the token it is made with, `None` for an anonymous caller.
_Request = tuple[str, str | None]


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _sign_in(api: ApiTestApp, role: Role) -> tuple[str, User]:
    subject = f"sub-leak-{uuid.uuid4().hex[:10]}"
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
    return token, user


def _read(api: ApiTestApp, requests: list[_Request]) -> list[tuple[int, str]]:
    """Every request's status and body. Each one must succeed, so a route that quietly refuses the
    caller cannot make the comparison pass by comparing two refusals."""
    answers = [(r.status_code, r.text) for r in (api.get(p, token=t) for p, t in requests)]
    assert [status for status, _ in answers] == [200] * len(requests), answers
    return answers


def _comment_on(api: ApiTestApp, path: str, token: str) -> str:
    created = api.post(path, token=token, json={"body": _SECRET})
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


def _withdraw(api: ApiTestApp, path: str, token: str, comment_id: str) -> None:
    response = api.post(f"{path}/{comment_id}/withdraw", token=token, json={"reason": _REASON})
    assert response.status_code == 200, response.text


def _assert_unchanged(
    api: ApiTestApp, requests: list[_Request], before: list[tuple[int, str]], comment_id: str
) -> None:
    after = _read(api, requests)
    assert after == before
    for _, text in after:
        assert _SECRET not in text
        assert comment_id not in text
        assert "comment" not in text.lower()


# --- a submission (FR-95) ------------------------------------------------------------------


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_comment_on_a_submission_changes_no_submission_response(api: ApiTestApp) -> None:
    submitter_token, submitter = _sign_in(api, Role.MEMBER)
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    admin_token, _ = _sign_in(api, Role.ADMINISTRATOR)
    submission = Submission(
        kind=SubmissionKind.NEW_TEST.value,
        preferred_term=f"Leak submission {uuid.uuid4()}",
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=submitter.id,
        organisation="Submitter Lab",
    )
    api.session.add(submission)
    api.session.flush()
    path = f"/submissions/{submission.id}/comments"
    requests: list[_Request] = [
        (f"/submissions/{submission.id}", submitter_token),
        (f"/submissions/{submission.id}", reviewer_token),
        (f"/submissions/{submission.id}", admin_token),
        ("/submissions?mine=true", submitter_token),
        (f"/submissions/{submission.id}/interest", reviewer_token),
    ]
    before = _read(api, requests)

    comment_id = _comment_on(api, path, reviewer_token)
    _assert_unchanged(api, requests, before, comment_id)

    _withdraw(api, path, admin_token, comment_id)
    _assert_unchanged(api, requests, before, comment_id)


# --- an entry (FR-95) ----------------------------------------------------------------------


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_comment_on_an_entry_changes_no_entry_response_and_no_public_route(
    api: ApiTestApp,
) -> None:
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    admin_token, _ = _sign_in(api, Role.ADMINISTRATOR)
    member_token, _ = _sign_in(api, Role.MEMBER)
    term = f"Leakproof entry {uuid.uuid4().hex}"
    entry = CatalogueEntry(
        business_key=f"NPTC-9{random.SystemRandom().randrange(10**8):08d}",
        preferred_term=term,
        status=CatalogueEntryStatus.ACTIVE.value,
    )
    api.session.add(entry)
    api.session.flush()
    key = entry.business_key
    path = f"/catalogue/entries/{key}/comments"
    requests: list[_Request] = [
        (f"/catalogue/entries/{key}", None),
        (f"/catalogue/entries/{key}/history", None),
        (f"/catalogue/entries/{key}/designations", None),
        (f"/catalogue/entries/{key}/bindings", None),
        (f"/catalogue/entries/{key}/properties", None),
        (f"/catalogue/search?q={term.replace(' ', '+')}", None),
        (f"/catalogue/entries/{key}", member_token),
        (f"/catalogue/admin/entries/{key}", admin_token),
        (f"/catalogue/admin/entries/{key}/history", admin_token),
    ]
    before = _read(api, requests)

    comment_id = _comment_on(api, path, reviewer_token)
    _assert_unchanged(api, requests, before, comment_id)

    _withdraw(api, path, reviewer_token, comment_id)
    _assert_unchanged(api, requests, before, comment_id)


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_comment_on_a_draft_entry_does_not_make_it_visible(api: ApiTestApp) -> None:
    reviewer_token, _ = _sign_in(api, Role.REVIEWER)
    entry = CatalogueEntry(
        business_key=f"NPTC-9{random.SystemRandom().randrange(10**8):08d}",
        preferred_term=f"Draft leak entry {uuid.uuid4()}",
        status=CatalogueEntryStatus.DRAFT.value,
    )
    api.session.add(entry)
    api.session.flush()
    public = f"/catalogue/entries/{entry.business_key}"
    assert api.get(public).status_code == 404

    _comment_on(api, f"{public}/comments", reviewer_token)

    assert api.get(public).status_code == 404
    assert api.get(f"{public}/history").status_code == 404


# --- the audit log (NFR-08) ----------------------------------------------------------------


@pytest.mark.req("FR-95")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_the_audit_log_records_the_comment_event_without_its_text(api: ApiTestApp) -> None:
    admin_token, _ = _sign_in(api, Role.ADMINISTRATOR)
    entry = CatalogueEntry(
        business_key=f"NPTC-9{random.SystemRandom().randrange(10**8):08d}",
        preferred_term=f"Audited entry {uuid.uuid4()}",
        status=CatalogueEntryStatus.ACTIVE.value,
    )
    api.session.add(entry)
    api.session.flush()
    comment_id = _comment_on(api, f"/catalogue/entries/{entry.business_key}/comments", admin_token)

    filters = f"entity_type=internal_comment&entity_id={comment_id}"
    searched = api.get(f"/audit/events?{filters}", token=admin_token)
    exported = api.get(f"/audit/events/export?{filters}", token=admin_token)

    assert searched.status_code == 200 and exported.status_code == 200
    assert [item["action"] for item in searched.json()["items"]] == ["comment.created"]
    assert _SECRET not in searched.text
    assert _SECRET not in exported.text
    # The entry's own audit history, which the public history is built from, has no such event.
    own = api.get(
        f"/audit/events?entity_type=catalogue_entry&entity_id={entry.id}", token=admin_token
    )
    assert "comment" not in own.text.lower()


# --- the API description (FR-95) -----------------------------------------------------------


def _reachable_property_names(document: dict[str, Any], operation: dict[str, Any]) -> set[str]:
    """Every property name in the response schemas of `operation`, following `$ref`s."""
    names: set[str] = set()
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref not in seen:
                seen.add(ref)
                walk(document["components"]["schemas"][ref.rsplit("/", 1)[-1]])
            names.update(node.get("properties", {}))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(operation.get("responses", {}))
    return names


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_no_route_outside_the_comment_routes_describes_a_comment(api: ApiTestApp) -> None:
    document = api.app.openapi()
    comment_path = re.compile(r"/comments(/|$)")
    checked = 0
    for path, operations in document["paths"].items():
        if comment_path.search(path):
            continue
        for method, operation in operations.items():
            checked += 1
            leaked = {
                name
                for name in _reachable_property_names(document, operation)
                if "comment" in name.lower()
            }
            assert not leaked, (method, path, leaked)
    assert checked > 30
