"""`PATCH /api/v1/submissions/{id}/working-copy` and the detail's `working_copy` (FR-23, FR-06, FR-44,
FR-82, NFR-08, NFR-45).

The real app over a stub identity provider (`api_app_support`). Submissions are inserted directly, so
any state is reachable. `backend/tests` shares one Postgres container, so every assertion is scoped to
rows this test created: working copies are found by submission, and audit events by the submission
they describe. Every refusal asserts that nothing was written.
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
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.property_definition import PropertyDefinition, PropertyOrigin, PropertyScope
from nptc.db.models.submission import Submission, SubmissionKind
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
_REASON = "Corrected against the lab's own sheet."
_CODE = "391483001"
_FSN = "Microscopy (acid fast bacilli) (procedure)"
_INACTIVE_CODE = "119364003"
_ORIGINAL_TERM = "Original serum sodium wording"
_ORIGINAL_NOTES = "Notes the submitter wrote"
_REVIEWING_ROLES = [Role.REVIEWER, Role.ADMINISTRATOR]
#: Roles that may sign in and hold no review permission.
_NON_REVIEWING_ROLES = [Role.PROVISIONAL, Role.MEMBER, Role.OBSERVER]
ZERO_WIDTH_SPACE = chr(0x200B)


@pytest.fixture
def api(app_db: Connection) -> Iterator[ApiTestApp]:
    yield from build_api_test_app(app_db)


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


def _sign_in(api: ApiTestApp, role: Role, *, accept_terms: bool = True) -> tuple[str, User]:
    """A token holding exactly `role`, and the user it belongs to. An administrator gets the MFA
    claim the realm demands for that role. Each call gives its own email, so each is a distinct user
    (NFR-05)."""
    subject = f"sub-working-copy-{uuid.uuid4().hex[:10]}"
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


def _submission(api: ApiTestApp, submitter: User, *, state: str = "Triage") -> Submission:
    submission = Submission(
        kind=SubmissionKind.NEW_TEST.value,
        state=state,
        preferred_term=_ORIGINAL_TERM,
        synonyms=["Original synonym"],
        property_values={},
        notes=_ORIGINAL_NOTES,
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=submitter.id,
        organisation="Submitter Lab",
    )
    api.session.add(submission)
    api.session.flush()
    return submission


def _amendment(api: ApiTestApp, submitter: User) -> Submission:
    entry = CatalogueEntry(
        business_key=f"NPTC-{uuid.uuid4().int % 900_000_000 + 100_000_000}",
        preferred_term="Entry term",
        status=CatalogueEntryStatus.ACTIVE.value,
    )
    api.session.add(entry)
    api.session.flush()
    submission = Submission(
        kind=SubmissionKind.AMENDMENT.value,
        state="Triage",
        entry_id=entry.id,
        preferred_term="Entry term",
        synonyms=["Original synonym"],
        property_values={},
        submitter_id=submitter.id,
    )
    api.session.add(submission)
    api.session.flush()
    return submission


def _property(api: ApiTestApp, key: str) -> None:
    api.session.add(
        PropertyDefinition(
            key=key,
            label=key.replace("_", " ").title(),
            datatype="string",
            cardinality="0..1",
            scope=PropertyScope.SUBMISSION,
            required_for_submission=False,
            required_for_publication=False,
            filterable=False,
            origin=PropertyOrigin.ADMIN,
            display_order=0,
            constraints={},
        )
    )
    api.session.flush()


def _edit(
    api: ApiTestApp,
    token: str | None,
    submission_id: object,
    *,
    reason: object = _REASON,
    expected_row_version: object = 1,
    **fields: object,
) -> Any:
    return api.request(
        "PATCH",
        f"/submissions/{submission_id}/working-copy",
        token=token,
        json={"reason": reason, "expected_row_version": expected_row_version, **fields},
    )


def _copy(api: ApiTestApp, submission: Submission) -> SubmissionWorkingCopy | None:
    api.session.expire_all()
    return api.session.get(SubmissionWorkingCopy, submission.id)


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


def _row_version(api: ApiTestApp, submission: Submission) -> int:
    api.session.expire(submission)
    return submission.row_version


def _assert_unchanged(api: ApiTestApp, submission: Submission, version: int = 1) -> None:
    assert _copy(api, submission) is None
    assert _row_version(api, submission) == version
    assert _events(api, submission) == []


def _detail(api: ApiTestApp, token: str, submission: Submission) -> dict[str, Any]:
    response = api.get(f"/submissions/{submission.id}", token=token)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# --- a reviewer and an administrator edit every field (FR-23) ------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-06")
@pytest.mark.req("FR-82")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REVIEWING_ROLES)
def test_a_reviewing_role_edits_every_field_and_the_original_is_unchanged(
    api: ApiTestApp, role: Role
) -> None:
    _seed_concepts(api)
    _property(api, "wc_api_note")
    _, submitter = _sign_in(api, Role.MEMBER)
    token, reviewer = _sign_in(api, role)
    submission = _submission(api, submitter)

    response = _edit(
        api,
        token,
        submission.id,
        preferred_term="  Reviewed serum sodium  ",
        synonyms=["Sodium, serum", "Na (serum)"],
        snomed_code=_CODE,
        property_values={"wc_api_note": [{"value": "checked"}]},
        notes="Confirmed with the lab",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["submission_id"] == str(submission.id)
    assert body["changed"] is True
    assert body["row_version"] == 2
    assert body["preferred_term"] == "Reviewed serum sodium"
    assert body["synonyms"] == ["Sodium, serum", "Na (serum)"]
    assert (body["snomed_code"], body["snomed_fsn"]) == (_CODE, _FSN)
    assert body["property_values"] == {"wc_api_note": [{"value": "checked", "justification": None}]}
    assert body["notes"] == "Confirmed with the lab"
    assert set(body["label_provenance"]) == {"preferred_term", "synonyms", "snomed_fsn"}

    copy = _copy(api, submission)
    assert copy is not None
    assert copy.preferred_term == "Reviewed serum sodium"
    api.session.refresh(submission)
    assert submission.preferred_term == _ORIGINAL_TERM
    assert submission.synonyms == ["Original synonym"]
    assert submission.snomed_code is None
    assert submission.snomed_fsn is None
    assert submission.property_values == {}
    assert submission.notes == _ORIGINAL_NOTES
    assert submission.row_version == 2
    [event] = _events(api, submission)
    assert event.action == "submission.working_copy_edited"
    assert event.actor_user_id == reviewer.id
    assert event.reason == _REASON
    assert set(event.after or {}) == {
        "preferred_term",
        "synonyms",
        "snomed_code",
        "snomed_fsn",
        "property_values",
        "notes",
    }
    assert (event.before or {})["preferred_term"] == _ORIGINAL_TERM


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_second_edit_carries_the_new_row_version_and_updates_the_same_row(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    first = _edit(api, token, submission.id, notes="First")
    second = _edit(
        api, token, submission.id, notes="Second", expected_row_version=first.json()["row_version"]
    )

    assert (first.status_code, second.status_code) == (200, 200)
    assert (first.json()["row_version"], second.json()["row_version"]) == (2, 3)
    copy = _copy(api, submission)
    assert copy is not None
    assert copy.notes == "Second"
    assert len(_events(api, submission)) == 2


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_field_left_out_keeps_its_original_value_in_the_working_copy(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    body = _edit(api, token, submission.id, notes="Only the note").json()

    assert body["preferred_term"] == _ORIGINAL_TERM
    assert body["synonyms"] == ["Original synonym"]
    assert body["notes"] == "Only the note"


# --- an edit that changes nothing (FR-23, NFR-08) ------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_an_edit_that_changes_nothing_writes_nothing_and_says_so(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(
        api, token, submission.id, preferred_term=_ORIGINAL_TERM, notes=_ORIGINAL_NOTES
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is False
    assert body["row_version"] == 1
    assert body["preferred_term"] == _ORIGINAL_TERM
    _assert_unchanged(api, submission)


# --- who may edit (FR-44) ------------------------------------------------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", _NON_REVIEWING_ROLES)
def test_a_role_without_the_review_permission_is_refused(api: ApiTestApp, role: Role) -> None:
    token, user = _sign_in(api, role)
    submission = _submission(api, user)

    response = _edit(api, token, submission.id, notes="Edited")

    assert response.status_code == 403, response.text
    assert "WWW-Authenticate" not in response.headers
    assert not _UUID.search(response.text)
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_the_submitter_cannot_edit_their_own_submission(api: ApiTestApp) -> None:
    token, submitter = _sign_in(api, Role.MEMBER)
    submission = _submission(api, submitter)

    response = _edit(api, token, submission.id, notes="Edited")

    assert response.status_code == 403, response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REVIEWING_ROLES)
def test_a_reviewing_role_may_edit_a_submission_it_made_itself(api: ApiTestApp, role: Role) -> None:
    """The PRD sets no separation of duties for an edit: the permission alone decides (FR-44)."""
    token, user = _sign_in(api, role)
    submission = _submission(api, user)

    response = _edit(api, token, submission.id, notes="Edited")

    assert response.status_code == 200, response.text
    assert _copy(api, submission) is not None


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_a_caller_without_the_permission_is_refused_before_the_submission_is_looked_up(
    api: ApiTestApp,
) -> None:
    token, _ = _sign_in(api, Role.OBSERVER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    for target in (submission.id, uuid.uuid4()):
        response = _edit(api, token, target, notes="Edited")
        assert response.status_code == 403, response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_anonymous_caller_is_refused_with_401(api: ApiTestApp) -> None:
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(api, None, submission.id, notes="Edited")

    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"
    _assert_unchanged(api, submission)


@pytest.mark.req("NFR-45")
@pytest.mark.integration
def test_a_reviewer_who_has_not_accepted_the_current_terms_is_refused(api: ApiTestApp) -> None:
    token, reviewer = _sign_in(api, Role.REVIEWER, accept_terms=False)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    refused = _edit(api, token, submission.id, notes="Edited")

    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == TERMS_ACCEPTANCE_REQUIRED_CODE
    _assert_unchanged(api, submission)

    api.accept_current_terms(reviewer.id)
    assert _edit(api, token, submission.id, notes="Edited").status_code == 200


# --- state, version and identity (FR-23) -------------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize(
    "state", ["Triage", "Under review", "Awaiting terminology", "Ready for approval"]
)
def test_an_edit_is_accepted_in_every_working_state(api: ApiTestApp, state: str) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1], state=state)

    assert _edit(api, token, submission.id, notes="Edited").status_code == 200


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize(
    "state", ["Submitted", "Approved", "Published in release", "Rejected", "Withdrawn"]
)
def test_an_edit_outside_the_working_states_is_a_409(api: ApiTestApp, state: str) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1], state=state)

    response = _edit(api, token, submission.id, notes="Edited")

    assert response.status_code == 409, response.text
    assert "Triage" in response.json()["detail"]
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_stale_row_version_is_a_409_that_carries_the_current_one(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(api, token, submission.id, notes="Edited", expected_row_version=7)

    assert response.status_code == 409, response.text
    assert response.json()["current_row_version"] == 1
    assert response.json()["expected_row_version"] == 7
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_edit_and_a_change_of_state_cannot_cross(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    assert _edit(api, token, submission.id, notes="Edited").status_code == 200

    stale = api.post(
        f"/submissions/{submission.id}/transitions",
        token=token,
        json={"to_state": "Under review", "reason": "Ready", "expected_row_version": 1},
    )
    current = api.post(
        f"/submissions/{submission.id}/transitions",
        token=token,
        json={"to_state": "Under review", "reason": "Ready", "expected_row_version": 2},
    )

    assert stale.status_code == 409, stale.text
    assert stale.json()["current_row_version"] == 2
    assert current.status_code == 200, current.text
    copy = _copy(api, submission)
    assert copy is not None
    assert copy.notes == "Edited"


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_an_unknown_submission_is_a_404_and_a_malformed_identifier_a_422(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)

    assert _edit(api, token, uuid.uuid4(), notes="Edited").status_code == 404
    assert _edit(api, token, "not-a-uuid", notes="Edited").status_code == 422


# --- what a refused edit says (FR-06, FR-24, FR-63) ---------------------------------------------


@pytest.mark.req("FR-06")
@pytest.mark.req("FR-82")
@pytest.mark.integration
@pytest.mark.parametrize(
    "code",
    ["26604008", "not-a-code", _INACTIVE_CODE],
    ids=["bad_check_digit", "not_digits", "inactive"],
)
def test_a_code_that_cannot_be_stored_is_a_422_that_names_the_field(
    api: ApiTestApp, code: str
) -> None:
    _seed_concepts(api)
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(api, token, submission.id, snomed_code=code)

    assert response.status_code == 422, response.text
    assert response.json()["field"] == "snomed_code"
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_a_code_the_server_does_not_know_is_a_422_and_writes_nothing(api: ApiTestApp) -> None:
    api.terminology.seed_error(
        Operation.LOOKUP, TerminologyStatusError("not found", status_code=404)
    )
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(api, token, submission.id, snomed_code="26604007")

    assert response.status_code == 422, response.text
    assert response.json()["field"] == "snomed_code"
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_a_code_is_stored_as_the_string_the_caller_sent(api: ApiTestApp) -> None:
    _seed_concepts(api)
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    body = _edit(api, token, submission.id, snomed_code=_CODE).json()

    assert body["snomed_code"] == _CODE
    assert isinstance(body["snomed_code"], str)


@pytest.mark.req("FR-82")
@pytest.mark.integration
def test_the_caller_cannot_supply_the_fsn(api: ApiTestApp) -> None:
    _seed_concepts(api)
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(api, token, submission.id, snomed_code=_CODE, snomed_fsn="Caller's own label")

    assert response.status_code == 422, response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-06")
@pytest.mark.integration
def test_an_unreachable_terminology_server_is_a_503_and_writes_nothing(api: ApiTestApp) -> None:
    api.terminology.seed_error(Operation.LOOKUP, TerminologyTimeoutError("timed out"))
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(api, token, submission.id, snomed_code=_CODE)

    assert response.status_code == 503, response.text
    assert response.json()["field"] == "snomed_code"
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-24")
@pytest.mark.integration
def test_an_unknown_property_is_a_422_with_its_issue(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(
        api, token, submission.id, property_values={"no_such_property": [{"value": "x"}]}
    )

    assert response.status_code == 422, response.text
    assert [(i["property_key"], i["code"]) for i in response.json()["issues"]] == [
        ("no_such_property", "unknown-property")
    ]
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-63")
@pytest.mark.integration
@pytest.mark.parametrize(
    ("fields", "reason", "field"),
    [
        ({"notes": "Edited"}, "   ", "reason"),
        ({"notes": "Edited"}, "Why" + ZERO_WIDTH_SPACE, "reason"),
        ({"notes": "No" + ZERO_WIDTH_SPACE + "te"}, _REASON, "notes"),
        ({"preferred_term": "   "}, _REASON, "preferred_term"),
        ({"synonyms": ["   "]}, _REASON, "synonyms"),
    ],
    ids=["blank_reason", "invisible_reason", "invisible_notes", "blank_term", "blank_synonym"],
)
def test_text_that_cannot_be_cleaned_is_a_422_that_names_the_field(
    api: ApiTestApp, fields: dict[str, Any], reason: str, field: str
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(api, token, submission.id, reason=reason, **fields)

    assert response.status_code == 422, response.text
    assert response.json()["field"] == field
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize(
    "fields",
    [
        {"notes": None},
        {"snomed_code": None},
        {"preferred_term": None},
        {"synonyms": None},
        {"property_values": None},
        {},
        {"unknown_field": "x"},
        {"notes": "x" * 10_001},
    ],
    ids=[
        "null_notes",
        "null_code",
        "null_term",
        "null_synonyms",
        "null_properties",
        "no_content_field",
        "unknown_field",
        "notes_too_long",
    ],
)
def test_a_body_that_fails_validation_is_a_422_and_writes_nothing(
    api: ApiTestApp, fields: dict[str, Any]
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(api, token, submission.id, **fields)

    assert response.status_code == 422, response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-23")
@pytest.mark.integration
@pytest.mark.parametrize("version", [0, -1, "one"])
def test_an_unusable_expected_row_version_is_a_422(api: ApiTestApp, version: object) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(api, token, submission.id, notes="Edited", expected_row_version=version)

    assert response.status_code == 422, response.text
    _assert_unchanged(api, submission)


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_a_missing_reason_is_a_422(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    response = api.request(
        "PATCH",
        f"/submissions/{submission.id}/working-copy",
        token=token,
        json={"notes": "Edited", "expected_row_version": 1},
    )

    assert response.status_code == 422, response.text
    _assert_unchanged(api, submission)


# --- an amendment (FR-23, FR-35) ---------------------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_accepts_synonyms_and_a_code(api: ApiTestApp) -> None:
    _seed_concepts(api)
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _amendment(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(api, token, submission.id, synonyms=["Na (serum)"], snomed_code=_CODE)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["preferred_term"] == "Entry term"
    assert body["synonyms"] == ["Na (serum)"]
    assert body["snomed_code"] == _CODE


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-35")
@pytest.mark.integration
@pytest.mark.parametrize(
    "fields",
    [
        {"preferred_term": "Renamed"},
        {"notes": "A note"},
        {"property_values": {}},
        {"synonyms": []},
    ],
    ids=["preferred_term", "notes", "property_values", "emptied"],
)
def test_an_amendment_refuses_what_an_amendment_does_not_propose(
    api: ApiTestApp, fields: dict[str, Any]
) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _amendment(api, _sign_in(api, Role.MEMBER)[1])

    response = _edit(api, token, submission.id, **fields)

    assert response.status_code == 422, response.text
    _assert_unchanged(api, submission)


# --- who sees the working copy (FR-23) ---------------------------------------------------------


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", _REVIEWING_ROLES)
def test_a_reviewing_role_sees_the_working_copy_beside_the_original(
    api: ApiTestApp, role: Role
) -> None:
    token, _ = _sign_in(api, role)
    editor, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])

    before = _detail(api, token, submission)
    assert before["working_copy"] is None

    assert (
        _edit(api, editor, submission.id, preferred_term="Reviewed wording", notes="Reviewed note")
    ).status_code == 200
    after = _detail(api, token, submission)

    assert after["preferred_term"] == _ORIGINAL_TERM
    assert after["notes"] == _ORIGINAL_NOTES
    assert after["row_version"] == 2
    copy = after["working_copy"]
    assert copy["preferred_term"] == "Reviewed wording"
    assert copy["notes"] == "Reviewed note"
    assert copy["synonyms"] == ["Original synonym"]
    assert set(copy["label_provenance"]) == {"preferred_term", "synonyms", "snomed_fsn"}
    assert "updated_at" in copy


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-44")
@pytest.mark.integration
@pytest.mark.parametrize("role", _NON_REVIEWING_ROLES)
def test_a_role_without_the_review_permission_never_sees_the_working_copy(
    api: ApiTestApp, role: Role
) -> None:
    token, user = _sign_in(api, role)
    editor, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, user)
    for version, text in enumerate(("Reviewed wording one", "Reviewed wording two"), start=1):
        assert (
            _edit(api, editor, submission.id, preferred_term=text, expected_row_version=version)
        ).status_code == 200

    response = api.get(f"/submissions/{submission.id}", token=token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert "working_copy" not in body
    assert "Reviewed wording" not in response.text
    assert body["preferred_term"] == _ORIGINAL_TERM


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_the_submitter_sees_no_working_copy_key_before_or_after_an_edit(api: ApiTestApp) -> None:
    submitter_token, submitter = _sign_in(api, Role.MEMBER)
    editor, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, submitter)

    assert "working_copy" not in _detail(api, submitter_token, submission)
    assert _edit(api, editor, submission.id, notes="Edited").status_code == 200
    assert "working_copy" not in _detail(api, submitter_token, submission)


@pytest.mark.req("FR-23")
@pytest.mark.integration
def test_the_list_never_carries_a_working_copy(api: ApiTestApp) -> None:
    token, _ = _sign_in(api, Role.REVIEWER)
    submission = _submission(api, _sign_in(api, Role.MEMBER)[1])
    assert _edit(api, token, submission.id, preferred_term="Reviewed wording").status_code == 200

    response = api.get("/submissions", token=token, params={"limit": 200})

    assert response.status_code == 200, response.text
    assert "working_copy" not in response.text
    assert "Reviewed wording" not in response.text
