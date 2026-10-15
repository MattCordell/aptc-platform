"""The internal comment routes (FR-95, FR-44, FR-63, NFR-08, NFR-23, NFR-45).

The real app over a stub identity provider (`api_app_support`). Submissions and entries are inserted
directly. `backend/tests` shares one Postgres container, so every assertion is scoped to rows this
test created: comments are found by target, and audit events by the comment they describe.
"""

from __future__ import annotations

import importlib.util
import random
import sys
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Connection

from nptc.api.terms_gate import TERMS_ACCEPTANCE_REQUIRED_CODE
from nptc.audit.writer import AuditContext
from nptc.auth.identity import close_account
from nptc.auth.permissions import Role
from nptc.db.models.audit import AuditEvent
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

_ALLOWED_ROLES = [Role.REVIEWER, Role.ADMINISTRATOR]
_REFUSED_ROLES = [Role.MEMBER, Role.OBSERVER, Role.PROVISIONAL]
_BODY = "Check the reference range with the lab."
_REASON = "Posted on the wrong submission"
_ZERO_WIDTH_SPACE = chr(0x200B)
_MISSING_KEY = "NPTC-999999999"

#: A target is named by its comments path: `/submissions/{id}/comments` or
#: `/catalogue/entries/{key}/comments`.
_KINDS = ["submission", "entry"]


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _sign_in(api: ApiTestApp, role: Role, *, accept_terms: bool = True) -> tuple[str, User]:
    """A token holding exactly `role`, and the user it belongs to. An administrator gets the MFA
    claim the realm demands for that role. Each call gives its own email, so each is a distinct user
    (NFR-05)."""
    subject = f"sub-comments-{uuid.uuid4().hex[:10]}"
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


def _target(api: ApiTestApp, kind: str) -> str:
    """The comments path of a new target of `kind`. The entry is a draft, which a comment can
    still be made on."""
    if kind == "submission":
        _, submitter = _sign_in(api, Role.MEMBER)
        submission = Submission(
            kind=SubmissionKind.NEW_TEST.value,
            preferred_term=f"Comment submission {uuid.uuid4()}",
            reference_url="https://example.org/evidence",
            reference_checked_at=datetime.now(UTC),
            reference_status=200,
            submitter_id=submitter.id,
            organisation="Submitter Lab",
        )
        api.session.add(submission)
        api.session.flush()
        return f"/submissions/{submission.id}/comments"
    entry = CatalogueEntry(
        business_key=f"NPTC-9{random.SystemRandom().randrange(10**8):08d}",
        preferred_term=f"Comment entry {uuid.uuid4()}",
        status=CatalogueEntryStatus.DRAFT.value,
    )
    api.session.add(entry)
    api.session.flush()
    return f"/catalogue/entries/{entry.business_key}/comments"


def _missing(kind: str) -> str:
    """The comments path of a target that does not exist."""
    if kind == "submission":
        return f"/submissions/{uuid.uuid4()}/comments"
    return f"/catalogue/entries/{_MISSING_KEY}/comments"


def _post(api: ApiTestApp, token: str | None, path: str, body: object = _BODY) -> Any:
    return api.post(path, token=token, json={"body": body})


def _withdraw(
    api: ApiTestApp, token: str | None, path: str, comment_id: object, reason: object = _REASON
) -> Any:
    return api.post(f"{path}/{comment_id}/withdraw", token=token, json={"reason": reason})


def _posted(api: ApiTestApp, path: str, body: str = _BODY) -> dict[str, Any]:
    token, _ = _sign_in(api, Role.REVIEWER)
    response = _post(api, token, path, body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def _events(api: ApiTestApp, comment_id: str) -> list[AuditEvent]:
    return list(
        api.session.execute(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "internal_comment", AuditEvent.entity_id == comment_id)
            .order_by(AuditEvent.sequence)
        ).scalars()
    )


#: Each verb on a comments path, as a call taking the token and the path.
_Verb = Callable[[ApiTestApp, str | None, str], Any]
_VERBS: dict[str, _Verb] = {
    "list": lambda api, token, path: api.get(path, token=token),
    "post": lambda api, token, path: _post(api, token, path),
    "withdraw": lambda api, token, path: _withdraw(api, token, path, uuid.uuid4()),
}


# --- who may use the routes (FR-44) --------------------------------------------------------


@pytest.mark.req("FR-95")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("role", _ALLOWED_ROLES)
def test_a_reviewer_and_an_administrator_post_list_and_withdraw(
    api: ApiTestApp, role: Role, kind: str
) -> None:
    path = _target(api, kind)
    token, user = _sign_in(api, role)

    created = _post(api, token, path)
    assert created.status_code == 201, created.text
    comment = created.json()
    assert comment["body"] == _BODY
    assert comment["author"]["username"] == user.username
    assert comment["withdrawn_at"] is None
    assert comment["withdrawn_by"] is None
    assert comment["withdrawn_reason"] is None
    assert datetime.fromisoformat(comment["created_at"]).tzinfo is not None

    listed = api.get(path, token=token)
    assert listed.status_code == 200, listed.text
    assert [c["id"] for c in listed.json()["comments"]] == [comment["id"]]

    withdrawn = _withdraw(api, token, path, comment["id"])
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["body"] == _BODY
    assert withdrawn.json()["withdrawn_reason"] == _REASON

    relisted = api.get(path, token=token).json()["comments"]
    assert [(c["id"], c["body"], c["withdrawn_reason"]) for c in relisted] == [
        (comment["id"], _BODY, _REASON)
    ]


@pytest.mark.req("FR-95")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("verb", _VERBS)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("role", _REFUSED_ROLES)
def test_a_caller_without_the_permission_gets_403_and_no_comment_text(
    api: ApiTestApp, role: Role, kind: str, verb: str
) -> None:
    path = _target(api, kind)
    _posted(api, path, "A remark a submitter must never see")
    token, _ = _sign_in(api, role)

    response = _VERBS[verb](api, token, path)

    assert response.status_code == 403
    assert "A remark a submitter" not in response.text


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("verb", _VERBS)
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("role", _REFUSED_ROLES)
def test_the_refusal_is_the_same_whether_or_not_the_target_has_comments(
    api: ApiTestApp, role: Role, kind: str, verb: str
) -> None:
    with_comments = _target(api, kind)
    _posted(api, with_comments)
    without_comments = _target(api, kind)
    token, _ = _sign_in(api, role)

    refusals = [
        _VERBS[verb](api, token, path) for path in (with_comments, without_comments, _missing(kind))
    ]

    assert {response.status_code for response in refusals} == {403}
    assert len({response.text for response in refusals}) == 1


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("verb", _VERBS)
@pytest.mark.parametrize("kind", _KINDS)
def test_an_anonymous_caller_gets_401(api: ApiTestApp, kind: str, verb: str) -> None:
    path = _target(api, kind)

    assert _VERBS[verb](api, None, path).status_code == 401


@pytest.mark.req("FR-95")
@pytest.mark.req("NFR-45")
@pytest.mark.integration
@pytest.mark.parametrize("kind", _KINDS)
def test_a_reviewer_who_has_not_accepted_the_terms_cannot_post(api: ApiTestApp, kind: str) -> None:
    path = _target(api, kind)
    token, _ = _sign_in(api, Role.REVIEWER, accept_terms=False)

    response = _post(api, token, path)

    assert response.status_code == 403
    assert response.json()["code"] == TERMS_ACCEPTANCE_REQUIRED_CODE
    assert api.get(path, token=_sign_in(api, Role.REVIEWER)[0]).json()["comments"] == []


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("kind", _KINDS)
def test_any_writer_may_withdraw_any_comment(api: ApiTestApp, kind: str) -> None:
    path = _target(api, kind)
    comment = _posted(api, path)
    admin_token, admin = _sign_in(api, Role.ADMINISTRATOR)

    response = _withdraw(api, admin_token, path, comment["id"])

    assert response.status_code == 200, response.text
    assert response.json()["withdrawn_by"]["username"] == admin.username


# --- a missing target (FR-95) --------------------------------------------------------------


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("verb", _VERBS)
@pytest.mark.parametrize("kind", _KINDS)
def test_an_unknown_target_is_a_404_for_a_caller_who_may_use_the_route(
    api: ApiTestApp, kind: str, verb: str
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)

    assert _VERBS[verb](api, token, _missing(kind)).status_code == 404


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("verb", _VERBS)
def test_a_malformed_identifier_is_a_422_for_a_reviewer_and_a_403_for_a_member(
    api: ApiTestApp, verb: str
) -> None:
    reviewer, _ = _sign_in(api, Role.REVIEWER)
    member, _ = _sign_in(api, Role.MEMBER)
    for path in ("/submissions/not-a-uuid/comments", "/catalogue/entries/not-a-key/comments"):
        assert _VERBS[verb](api, reviewer, path).status_code == 422
        assert _VERBS[verb](api, member, path).status_code == 403


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_an_entry_in_any_status_can_be_commented_on(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    for status in CatalogueEntryStatus:
        entry = CatalogueEntry(
            business_key=f"NPTC-9{random.SystemRandom().randrange(10**8):08d}",
            preferred_term=f"Status entry {uuid.uuid4()}",
            status=status.value,
        )
        api.session.add(entry)
        api.session.flush()

        response = _post(api, token, f"/catalogue/entries/{entry.business_key}/comments")

        assert response.status_code == 201, (status, response.text)


# --- the text (FR-63, NFR-23) --------------------------------------------------------------


@pytest.mark.req("FR-95")
@pytest.mark.req("NFR-23")
@pytest.mark.integration
@pytest.mark.parametrize("kind", _KINDS)
def test_markup_comes_back_as_literal_text_in_json(api: ApiTestApp, kind: str) -> None:
    path = _target(api, kind)
    token, _ = _sign_in(api, Role.REVIEWER)
    markup = '<img src=x onerror="alert(1)"> <b>bold</b> &amp;'

    created = _post(api, token, path, markup)
    listed = api.get(path, token=token)

    assert created.json()["body"] == markup
    assert listed.json()["comments"][0]["body"] == markup
    assert listed.headers["content-type"].startswith("application/json")


@pytest.mark.req("FR-95")
@pytest.mark.req("FR-63")
@pytest.mark.integration
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("body", ["", "   ", "\n\t  "], ids=["empty", "spaces", "whitespace"])
def test_a_blank_body_is_a_422_that_names_the_field(api: ApiTestApp, kind: str, body: str) -> None:
    path = _target(api, kind)
    token, _ = _sign_in(api, Role.REVIEWER)

    response = _post(api, token, path, body)

    assert response.status_code == 422
    assert response.json()["field"] == "body"
    assert api.get(path, token=token).json()["comments"] == []


@pytest.mark.req("FR-95")
@pytest.mark.req("FR-63")
@pytest.mark.integration
def test_a_body_with_an_invisible_character_is_a_422_that_names_the_field(
    api: ApiTestApp,
) -> None:
    path = _target(api, "submission")
    token, _ = _sign_in(api, Role.REVIEWER)

    response = _post(api, token, path, f"hid{_ZERO_WIDTH_SPACE}den")

    assert response.status_code == 422
    assert response.json()["field"] == "body"
    assert api.get(path, token=token).json()["comments"] == []


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_body_over_the_size_bound_or_with_an_extra_field_fails_validation(
    api: ApiTestApp,
) -> None:
    path = _target(api, "submission")
    token, _ = _sign_in(api, Role.REVIEWER)

    too_long = _post(api, token, path, "x" * 2_001)
    extra = api.post(path, token=token, json={"body": _BODY, "author": "someone else"})
    missing = api.post(path, token=token, json={})

    assert [too_long.status_code, extra.status_code, missing.status_code] == [422, 422, 422]
    assert api.get(path, token=token).json()["comments"] == []


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("reason", ["", "  ", "\n\t"], ids=["empty", "spaces", "whitespace"])
def test_a_blank_reason_is_a_422_that_names_the_field(
    api: ApiTestApp, kind: str, reason: str
) -> None:
    path = _target(api, kind)
    comment = _posted(api, path)
    token, _ = _sign_in(api, Role.REVIEWER)

    response = _withdraw(api, token, path, comment["id"], reason)

    assert response.status_code == 422
    assert response.json()["field"] == "reason"
    assert api.get(path, token=token).json()["comments"][0]["withdrawn_at"] is None


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_withdrawal_without_a_reason_fails_validation(api: ApiTestApp) -> None:
    path = _target(api, "submission")
    comment = _posted(api, path)
    token, _ = _sign_in(api, Role.REVIEWER)

    response = api.post(f"{path}/{comment['id']}/withdraw", token=token, json={})

    assert response.status_code == 422


# --- withdrawing (FR-95) -------------------------------------------------------------------


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("kind", _KINDS)
def test_a_second_withdrawal_is_a_409_and_the_first_reason_stands(
    api: ApiTestApp, kind: str
) -> None:
    path = _target(api, kind)
    comment = _posted(api, path)
    token, _ = _sign_in(api, Role.REVIEWER)
    assert _withdraw(api, token, path, comment["id"]).status_code == 200

    again = _withdraw(api, token, path, comment["id"], "A different reason")

    assert again.status_code == 409
    [listed] = api.get(path, token=token).json()["comments"]
    assert listed["withdrawn_reason"] == _REASON


@pytest.mark.req("FR-95")
@pytest.mark.integration
def test_a_comment_cannot_be_withdrawn_through_a_different_target(api: ApiTestApp) -> None:
    path = _target(api, "submission")
    other = _target(api, "submission")
    entry = _target(api, "entry")
    comment = _posted(api, path)
    token, _ = _sign_in(api, Role.REVIEWER)

    for wrong in (other, entry):
        assert _withdraw(api, token, wrong, comment["id"]).status_code == 404
    assert _withdraw(api, token, path, uuid.uuid4()).status_code == 404

    [listed] = api.get(path, token=token).json()["comments"]
    assert listed["withdrawn_at"] is None


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("kind", _KINDS)
def test_comments_are_listed_oldest_first_and_only_for_their_target(
    api: ApiTestApp, kind: str
) -> None:
    path = _target(api, kind)
    first = _posted(api, path, "one")
    second = _posted(api, path, "two")
    _posted(api, _target(api, kind), "elsewhere")
    token, _ = _sign_in(api, Role.REVIEWER)

    listed = api.get(path, token=token).json()["comments"]

    assert [c["id"] for c in listed] == [first["id"], second["id"]]


@pytest.mark.req("FR-95")
@pytest.mark.integration
@pytest.mark.parametrize("kind", _KINDS)
def test_a_target_with_no_comments_lists_an_empty_array(api: ApiTestApp, kind: str) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)

    response = api.get(_target(api, kind), token=token)

    assert response.status_code == 200
    assert response.json() == {"comments": []}


@pytest.mark.req("FR-95")
@pytest.mark.req("NFR-04")
@pytest.mark.integration
def test_no_response_carries_a_user_id(api: ApiTestApp) -> None:
    path = _target(api, "submission")
    token, author = _sign_in(api, Role.REVIEWER)
    created = _post(api, token, path)
    withdrawn = _withdraw(api, token, path, created.json()["id"])

    listed = api.get(path, token=token)

    for response in (created, withdrawn, listed):
        assert str(author.id) not in response.text


@pytest.mark.req("FR-95")
@pytest.mark.req("NFR-17")
@pytest.mark.integration
@pytest.mark.parametrize("kind", _KINDS)
def test_a_closed_account_keeps_its_comments_under_the_placeholder(
    api: ApiTestApp, kind: str
) -> None:
    path = _target(api, kind)
    author_token, author = _sign_in(api, Role.REVIEWER)
    created = _post(api, author_token, path)
    assert created.status_code == 201, created.text
    username = author.username
    close_account(api.session, author.id, AuditContext.system())
    api.session.flush()
    reader, _ = _sign_in(api, Role.REVIEWER)

    response = api.get(path, token=reader)

    [comment] = response.json()["comments"]
    assert comment["body"] == _BODY
    assert comment["author"] == {"username": "Closed account", "display_name": "Closed account"}
    assert username is not None
    assert username not in response.text


@pytest.mark.req("FR-95")
@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_only_the_routes_that_write_document_a_terms_refusal(api: ApiTestApp) -> None:
    """The terms gate skips `GET`, so a client reading the schema must not be told a list can be
    refused for unaccepted terms."""
    paths = api.app.openapi()["paths"]
    seen = 0
    for path, operations in paths.items():
        if "/comments" not in path:
            continue
        for method, operation in operations.items():
            description = operation["responses"]["403"]["description"]
            assert ("terms of use" in description) is (method == "post"), (method, path)
            seen += 1
    assert seen == 6


# --- the audit trail (NFR-08) --------------------------------------------------------------


@pytest.mark.req("FR-95")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize("kind", _KINDS)
def test_a_post_and_a_withdrawal_each_write_one_audit_event(api: ApiTestApp, kind: str) -> None:
    path = _target(api, kind)
    token, user = _sign_in(api, Role.REVIEWER)

    comment = _post(api, token, path).json()
    [created] = _events(api, comment["id"])
    assert (created.action, created.actor_user_id) == ("comment.created", user.id)

    _withdraw(api, token, path, comment["id"])
    posted, withdrawn = _events(api, comment["id"])
    assert posted.id == created.id
    assert (withdrawn.action, withdrawn.reason) == ("comment.withdrawn", _REASON)


@pytest.mark.req("FR-95")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_refused_request_writes_no_audit_event(api: ApiTestApp) -> None:
    path = _target(api, "submission")
    comment = _posted(api, path)
    token, _ = _sign_in(api, Role.REVIEWER)
    _withdraw(api, token, path, comment["id"])
    before = len(_events(api, comment["id"]))

    _withdraw(api, token, path, comment["id"], "Again")
    _withdraw(api, token, path, comment["id"], "")
    member, _ = _sign_in(api, Role.MEMBER)
    _withdraw(api, member, path, comment["id"])

    assert len(_events(api, comment["id"])) == before


@pytest.mark.req("FR-95")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize("kind", _KINDS)
def test_the_audit_event_never_carries_the_body(api: ApiTestApp, kind: str) -> None:
    path = _target(api, kind)
    comment = _posted(api, path, "An unmistakable sentence")

    [event] = _events(api, comment["id"])

    assert "An unmistakable sentence" not in str(event.after)
    assert "An unmistakable sentence" not in str(event.before)
