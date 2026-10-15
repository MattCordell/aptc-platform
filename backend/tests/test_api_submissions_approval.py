"""`POST /api/v1/submissions/{id}/approve` and the generic route's refusal of the move (FR-28,
FR-29, FR-35, FR-44, FR-81, NFR-08, NFR-45).

The real app over a stub identity provider (`api_app_support`). Submissions are inserted directly, so
any state is reachable. `backend/tests` shares one Postgres container, so every assertion is scoped to
rows this test created: an entry is found by its unique preferred term, a transition by its
submission. Every refusal asserts that nothing was written. The service's own cases are in
`test_submissions_approval.py`.
"""

from __future__ import annotations

import importlib.util
import random
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from nptc.api.terms_gate import TERMS_ACCEPTANCE_REQUIRED_CODE
from nptc.auth.permissions import Role
from nptc.catalogue.entries import allocate_business_key
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry
from nptc.db.models.code_binding import CodeBinding
from nptc.db.models.designation import Designation
from nptc.db.models.submission import Submission, SubmissionKind
from nptc.db.models.submission_transition import SubmissionTransition
from nptc.db.models.user import User
from nptc.db.models.user_identity import UserIdentity
from nptc_shared.sctid import has_valid_check_digit


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

_NOTE = "Approved after clinical review of the evidence"
_FSN = "Microscopy (acid fast bacilli) (procedure)"
_READY = "Ready for approval"
#: Roles that may sign in and hold no approve permission. The Reviewer is the one that matters: it
#: holds every other permission over a submission.
_REFUSED_ROLES = [Role.REVIEWER, Role.MEMBER, Role.OBSERVER, Role.PROVISIONAL]
ZERO_WIDTH_SPACE = chr(0x200B)


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


def _sign_in(api: ApiTestApp, role: Role, *, accept_terms: bool = True) -> tuple[str, User]:
    """A token holding exactly `role`, and the user it belongs to. An administrator gets the MFA
    claim the realm demands for that role. Each call gives its own email, so each is a distinct user
    (NFR-05)."""
    subject = f"sub-approval-{uuid.uuid4().hex[:10]}"
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


def _code() -> str:
    """A valid SCTID no other test has bound, because an active code belongs to one entry."""
    while True:
        candidate = f"{random.randrange(10**8, 10**9 - 1)}"
        for check in "0123456789":
            if has_valid_check_digit(candidate + check):
                return candidate + check


def _term(label: str) -> str:
    return f"{label} {uuid.uuid4().hex[:12]}"


def _new_test(
    api: ApiTestApp,
    submitter: User,
    *,
    term: str | None = None,
    synonyms: list[str] | None = None,
    code: str | None = None,
    state: str = _READY,
) -> Submission:
    submission = Submission(
        kind=SubmissionKind.NEW_TEST.value,
        state=state,
        preferred_term=term or _term("Approval API new test"),
        synonyms=[] if synonyms is None else synonyms,
        snomed_code=code,
        snomed_fsn=None if code is None else _FSN,
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=submitter.id,
    )
    api.session.add(submission)
    api.session.flush()
    return submission


def _entry(api: ApiTestApp, *, status: str = "active", term: str | None = None) -> CatalogueEntry:
    entry = CatalogueEntry(
        business_key=allocate_business_key(api.session),
        preferred_term=term or _term("Approval API entry"),
        status=status,
    )
    api.session.add(entry)
    api.session.flush()
    return entry


def _amendment(
    api: ApiTestApp,
    submitter: User,
    entry: CatalogueEntry,
    *,
    synonyms: list[str] | None = None,
    code: str | None = None,
) -> Submission:
    submission = Submission(
        kind=SubmissionKind.AMENDMENT.value,
        state=_READY,
        entry_id=entry.id,
        preferred_term=entry.preferred_term,
        synonyms=[] if synonyms is None else synonyms,
        snomed_code=code,
        snomed_fsn=None if code is None else _FSN,
        submitter_id=submitter.id,
    )
    api.session.add(submission)
    api.session.flush()
    return submission


def _approve(
    api: ApiTestApp,
    token: str | None,
    submission_id: object,
    *,
    reason: str = _NOTE,
    expected_row_version: int = 1,
) -> Any:
    return api.post(
        f"/submissions/{submission_id}/approve",
        token=token,
        json={"reason": reason, "expected_row_version": expected_row_version},
    )


def _transitions(api: ApiTestApp, submission: Submission) -> list[SubmissionTransition]:
    api.session.expire_all()
    return list(
        api.session.execute(
            select(SubmissionTransition).where(SubmissionTransition.submission_id == submission.id)
        ).scalars()
    )


def _events(api: ApiTestApp, submission: Submission) -> list[AuditEvent]:
    return list(
        api.session.execute(
            select(AuditEvent).where(
                AuditEvent.entity_type == "submission",
                AuditEvent.entity_id == str(submission.id),
            )
        ).scalars()
    )


def _entries_named(api: ApiTestApp, term: str) -> int:
    return api.session.execute(
        select(func.count())
        .select_from(CatalogueEntry)
        .where(CatalogueEntry.preferred_term == term)
    ).scalar_one()


def _assert_unchanged(api: ApiTestApp, submission: Submission) -> None:
    api.session.expire_all()
    reloaded = api.session.get(Submission, submission.id)
    assert reloaded is not None
    assert reloaded.state == _READY
    assert reloaded.row_version == 1
    assert reloaded.created_entry_id is None
    assert _transitions(api, submission) == []
    assert _events(api, submission) == []


# --- a new test (FR-28, FR-29, NFR-08) -----------------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-29")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_an_administrator_approves_a_new_test_and_the_entry_exists(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    term, synonym, code = _term("Approved API test"), _term("Approved API synonym"), _code()
    submission = _new_test(api, submitter, term=term, synonyms=[synonym], code=code)

    response = _approve(api, token, submission.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(submission.id)
    assert body["state"] == "Approved"
    assert body["row_version"] == 2
    assert body["entry_created"] is True
    api.session.expire_all()
    entry = api.session.execute(
        select(CatalogueEntry).where(CatalogueEntry.preferred_term == term)
    ).scalar_one()
    assert (body["entry_id"], body["business_key"]) == (str(entry.id), entry.business_key)
    assert entry.status == "active"
    assert [
        d.term for d in api.session.execute(select(Designation)).scalars() if d.entry_id == entry.id
    ] == [synonym]
    [binding] = api.session.execute(
        select(CodeBinding).where(CodeBinding.entry_id == entry.id)
    ).scalars()
    assert binding.code == code
    reloaded = api.session.get(Submission, submission.id)
    assert reloaded is not None and reloaded.created_entry_id == entry.id
    [row] = _transitions(api, submission)
    assert (row.from_state, row.to_state, row.reason) == (_READY, "Approved", _NOTE)
    [event] = _events(api, submission)
    assert event.action == "submission.approved"


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_administrator_approves_an_amendment_and_the_entry_gains_the_change(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    entry = _entry(api)
    synonym, code = _term("Amended API synonym"), _code()
    submission = _amendment(api, submitter, entry, synonyms=[synonym], code=code)

    response = _approve(api, token, submission.id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["entry_id"], body["entry_created"]) == (str(entry.id), False)
    assert body["entry_row_version"] == 2
    api.session.expire_all()
    assert [
        d.term for d in api.session.execute(select(Designation)).scalars() if d.entry_id == entry.id
    ] == [synonym]
    [binding] = api.session.execute(
        select(CodeBinding).where(CodeBinding.entry_id == entry.id)
    ).scalars()
    assert binding.code == code


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_submission_names_the_entry_its_approval_created(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _new_test(api, submitter)
    before = api.get(f"/submissions/{submission.id}", token=token).json()
    assert before["created_entry_business_key"] is None

    approved = _approve(api, token, submission.id).json()

    detail = api.get(f"/submissions/{submission.id}", token=token).json()
    assert detail["created_entry_business_key"] == approved["business_key"]
    assert detail["entry_business_key"] is None


# --- who may approve (FR-44, FR-81, NFR-06, NFR-45) ----------------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REFUSED_ROLES)
def test_a_role_without_the_approve_permission_is_refused_and_nothing_is_written(
    api: ApiTestApp, role: Role
) -> None:
    token, _ = _sign_in(api, role)
    _, submitter = _sign_in(api, Role.MEMBER)
    term = _term("Refused API test")
    submission = _new_test(api, submitter, term=term)

    response = _approve(api, token, submission.id)

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers
    _assert_unchanged(api, submission)
    assert _entries_named(api, term) == 0


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_reviewer_is_refused_before_the_submission_is_looked_up(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)

    assert _approve(api, token, uuid.uuid4()).status_code == 403


@pytest.mark.req("FR-81")
@pytest.mark.integration
def test_the_submitter_cannot_approve_their_own_submission(api: ApiTestApp) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _new_test(api, submitter)

    response = _approve(api, token, submission.id)

    assert response.status_code == 403, response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_anonymous_caller_is_refused_with_401(api: ApiTestApp) -> None:
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _new_test(api, submitter)

    response = _approve(api, None, submission.id)

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
    _assert_unchanged(api, submission)


@pytest.mark.req("NFR-06")
@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_administrator_without_mfa_gets_a_step_up_challenge(api: ApiTestApp) -> None:
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _new_test(api, submitter)
    token = api.admin_token(subject=f"sub-approval-no-mfa-{uuid.uuid4().hex[:8]}", with_mfa=False)

    response = _approve(api, token, submission.id)

    assert response.status_code == 403, response.text
    assert 'error="insufficient_user_authentication"' in response.headers["WWW-Authenticate"]
    _assert_unchanged(api, submission)


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_an_administrator_who_has_not_accepted_the_current_terms_is_refused(
    api: ApiTestApp,
) -> None:
    token, admin = _sign_in(api, Role.ADMINISTRATOR, accept_terms=False)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _new_test(api, submitter)

    refused = _approve(api, token, submission.id)

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == TERMS_ACCEPTANCE_REQUIRED_CODE
    _assert_unchanged(api, submission)

    api.accept_current_terms(admin.id)
    assert _approve(api, token, submission.id).status_code == 200


# --- refusals (FR-28, FR-29, FR-35, FR-37, FR-38) ------------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_unknown_submission_is_a_404(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)

    assert _approve(api, token, uuid.uuid4()).status_code == 404


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Submitted", "Triage", "Under review", "Awaiting terminology"])
def test_a_submission_that_is_not_ready_for_approval_is_a_409(api: ApiTestApp, state: str) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    term = _term("Not ready API test")
    submission = _new_test(api, submitter, term=term, state=state)

    response = _approve(api, token, submission.id)

    assert response.status_code == 409, response.text
    assert "not allowed" in response.json()["detail"]
    assert _transitions(api, submission) == []
    assert _entries_named(api, term) == 0


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_a_stale_row_version_is_a_409_that_carries_the_current_one(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    term = _term("Stale API test")
    submission = _new_test(api, submitter, term=term)

    response = _approve(api, token, submission.id, expected_row_version=7)

    assert response.status_code == 409, response.text
    assert response.json()["current_row_version"] == 1
    _assert_unchanged(api, submission)
    assert _entries_named(api, term) == 0


@pytest.mark.req("FR-37")
@pytest.mark.integration
@pytest.mark.parametrize("reason", ["ok", "Approved", "1234567890"])
def test_a_reason_that_is_not_a_meaningful_changelog_note_is_a_422(
    api: ApiTestApp, reason: str
) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    term = _term("Weak reason API test")
    submission = _new_test(api, submitter, term=term)

    response = _approve(api, token, submission.id, reason=reason)

    assert response.status_code == 422, response.text
    _assert_unchanged(api, submission)
    assert _entries_named(api, term) == 0


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize(
    "reason", ["   ", f"A real reason{ZERO_WIDTH_SPACE} for approval"], ids=["blank", "invisible"]
)
def test_a_blank_reason_or_one_with_an_invisible_character_is_a_422_that_names_the_field(
    api: ApiTestApp, reason: str
) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _new_test(api, submitter)

    response = _approve(api, token, submission.id, reason=reason)

    assert response.status_code == 422, response.text
    assert response.json()["field"] == "reason"
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-05")
@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_preferred_term_that_collides_with_another_entry_is_a_409(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    existing = _entry(api)
    submission = _new_test(api, submitter, term=existing.preferred_term)

    response = _approve(api, token, submission.id)

    assert response.status_code == 409, response.text
    _assert_unchanged(api, submission)
    assert _entries_named(api, existing.preferred_term) == 1


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_to_an_entry_that_is_no_longer_active_is_a_409_that_names_the_status(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    entry = _entry(api, status="deprecated")
    submission = _amendment(api, submitter, entry, synonyms=[_term("Too late synonym")])

    response = _approve(api, token, submission.id)

    assert response.status_code == 409, response.text
    assert "deprecated" in response.json()["detail"]
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_whose_synonym_the_entry_already_has_is_a_409_that_names_the_cause(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    entry = _entry(api)
    gained = _term("Already there")
    api.session.add(Designation(entry_id=entry.id, term=gained))
    api.session.flush()
    submission = _amendment(api, submitter, entry, synonyms=[gained])

    response = _approve(api, token, submission.id)

    assert response.status_code == 409, response.text
    assert "already has a synonym" in response.json()["detail"]
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_whose_code_is_already_active_is_a_409_that_names_the_cause(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    entry = _entry(api)
    code = _code()
    api.session.add(CodeBinding(entry_id=entry.id, code=code, fsn=_FSN))
    api.session.flush()
    submission = _amendment(api, submitter, entry, code=code)

    response = _approve(api, token, submission.id)

    assert response.status_code == 409, response.text
    assert "already carries the code" in response.json()["detail"]
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_second_approval_is_a_409_and_creates_no_second_entry(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    term = _term("Approved once API test")
    submission = _new_test(api, submitter, term=term)
    assert _approve(api, token, submission.id).status_code == 200

    again = _approve(api, token, submission.id, expected_row_version=2)

    assert again.status_code == 409, again.text
    assert _entries_named(api, term) == 1
    assert len(_transitions(api, submission)) == 1


# --- the general route cannot make the move (FR-28) ----------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_general_transition_route_refuses_the_move_and_names_the_approval_route(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    term = _term("Wrong route API test")
    submission = _new_test(api, submitter, term=term)

    response = api.post(
        f"/submissions/{submission.id}/transitions",
        token=token,
        json={"to_state": "Approved", "reason": _NOTE, "expected_row_version": 1},
    )

    assert response.status_code == 409, response.text
    assert "POST /submissions/{id}/approve" in response.json()["detail"]
    _assert_unchanged(api, submission)
    assert _entries_named(api, term) == 0


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_administrator_is_offered_the_approval_move_on_a_ready_submission(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.ADMINISTRATOR)
    _, submitter = _sign_in(api, Role.MEMBER)
    submission = _new_test(api, submitter)

    detail = api.get(f"/submissions/{submission.id}", token=token)

    assert detail.status_code == 200, detail.text
    assert "Approved" in detail.json()["available_transitions"]
