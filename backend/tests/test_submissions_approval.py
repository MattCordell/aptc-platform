"""`nptc.submissions.approval.approve_submission` service-layer tests (FR-28, FR-29, FR-35, FR-44,
FR-81, NFR-08).

Each refusal asserts that nothing was written: no entry, no transition row, no audit event of the
refused call and the submission still `Ready for approval`. `backend/tests` shares one Postgres
container (see `CLAUDE.md`), so every query is scoped to rows this test created: an entry is found by
its unique preferred term, an audit event by the correlation id of the call that wrote it.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nptc.audit.writer import AuditContext
from nptc.auth.errors_authorisation import MfaRequiredError, PermissionDeniedError
from nptc.auth.permissions import Role, permissions_for_roles
from nptc.auth.principal import Principal
from nptc.catalogue.changelog import ChangelogNoteError
from nptc.catalogue.collisions import DesignationCollisionError
from nptc.catalogue.entries import create_entry
from nptc.catalogue.local_codes import DatabaseLocalCodeLookup
from nptc.catalogue.property_values import (
    DeprecatedPropertyWriteError,
    PropertyDefinitionNotFoundError,
)
from nptc.db.definitions import deprecate_definition
from nptc.db.models.audit import AuditEvent
from nptc.db.models.catalogue_entry import CatalogueEntry, CatalogueEntryStatus
from nptc.db.models.code_binding import CodeBinding
from nptc.db.models.designation import Designation
from nptc.db.models.property_definition import PropertyDefinition, PropertyOrigin, PropertyScope
from nptc.db.models.property_value import PropertyValue
from nptc.db.models.submission import Submission, SubmissionKind, SubmissionState
from nptc.db.models.submission_transition import SubmissionTransition
from nptc.db.models.submission_working_copy import SubmissionWorkingCopy
from nptc.db.models.user import User
from nptc.registry.datatypes import build_builtin_handlers
from nptc.registry.handlers import DatatypeRegistry, HandlerDeps
from nptc.submissions.approval import Approval, approve_submission
from nptc.submissions.errors import (
    AmendmentEntryNotActiveError,
    AmendmentNoLongerApplicableCause,
    AmendmentNoLongerApplicableError,
    IllegalSubmissionTransitionError,
    SubmissionNotFoundError,
    SubmissionVersionConflictError,
    TransitionNeedsOwnRouteError,
    TransitionReasonRequiredError,
)
from nptc.submissions.transitions import transition_submission
from nptc_shared.sctid import has_valid_check_digit
from nptc_shared.terminology import StubTerminologyClient

_NOTE = "Approved after clinical review of the evidence"
_FSN = "Microscopy (acid fast bacilli) (procedure)"
_READY = SubmissionState.READY_FOR_APPROVAL.value


def _context(user_id: uuid.UUID) -> AuditContext:
    return AuditContext(
        actor_user_id=user_id, actor_ip=None, user_agent=None, correlation_id=uuid.uuid4()
    )


def _principal(user: User, *roles: Role, suppressed: frozenset[Role] = frozenset()) -> Principal:
    held = frozenset(roles)
    return Principal(
        user_id=user.id,
        user_ref=None,
        status=None,
        roles=held,
        permissions=permissions_for_roles(held),
        mfa_satisfied=not suppressed,
        mfa_suppressed_roles=suppressed,
    )


def _user(session: Session) -> User:
    user = User(username=f"approval-{uuid.uuid4()}", display_name="Approval Tester")
    session.add(user)
    session.flush()
    return user


def _code() -> str:
    """A valid SCTID no other test has bound, because an active code belongs to one entry."""
    while True:
        candidate = f"{random.randrange(10**8, 10**9 - 1)}"
        for check in "0123456789":
            if has_valid_check_digit(candidate + check):
                return candidate + check


def _term(label: str) -> str:
    return f"{label} {uuid.uuid4().hex[:12]}"


def _registry(session: Session) -> DatatypeRegistry:
    return DatatypeRegistry(
        build_builtin_handlers(
            HandlerDeps(
                terminology_client=StubTerminologyClient(),
                local_code_lookup=DatabaseLocalCodeLookup(session),
            )
        )
    )


def _property(session: Session, key: str) -> None:
    session.add(
        PropertyDefinition(
            key=key,
            label=key.replace("_", " ").title(),
            datatype="string",
            cardinality="0..1",
            scope=PropertyScope.BOTH,
            required_for_submission=False,
            required_for_publication=False,
            filterable=False,
            origin=PropertyOrigin.ADMIN,
            display_order=0,
            constraints={},
        )
    )
    session.flush()


def _entry(session: Session, user: User, *, term: str | None = None) -> CatalogueEntry:
    return create_entry(
        session,
        _context(user.id),
        preferred_term=term or _term("Approval entry"),
        reason="Created for an approval test",
        status=CatalogueEntryStatus.ACTIVE,
    )


def _bind(session: Session, user: User, entry: CatalogueEntry, code: str) -> CodeBinding:
    from nptc.catalogue.bindings import create_binding

    return create_binding(
        session,
        _context(user.id),
        entry=entry,
        code=code,
        fsn=_FSN,
        reason="Bound for an approval test",
    )


def _ready_new_test(
    session: Session,
    submitter: User,
    *,
    term: str | None = None,
    synonyms: list[str] | None = None,
    code: str | None = None,
    property_values: dict[str, list[dict[str, object]]] | None = None,
) -> Submission:
    submission = Submission(
        kind=SubmissionKind.NEW_TEST.value,
        state=_READY,
        preferred_term=term or _term("Approval new test"),
        synonyms=[] if synonyms is None else synonyms,
        snomed_code=code,
        snomed_fsn=None if code is None else _FSN,
        property_values={} if property_values is None else property_values,
        reference_url="https://example.org/evidence",
        reference_checked_at=datetime.now(UTC),
        reference_status=200,
        submitter_id=submitter.id,
    )
    session.add(submission)
    session.flush()
    return submission


def _ready_amendment(
    session: Session,
    submitter: User,
    entry: CatalogueEntry,
    *,
    synonyms: list[str] | None = None,
    code: str | None = None,
    proposed_at: datetime | None = None,
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
        **({} if proposed_at is None else {"created_at": proposed_at}),
    )
    session.add(submission)
    session.flush()
    return submission


def _approve(
    session: Session,
    submission: Submission,
    actor: User,
    *roles: Role,
    reason: str = _NOTE,
    expected_row_version: int | None = None,
    ctx: AuditContext | None = None,
    suppressed: frozenset[Role] = frozenset(),
) -> Approval:
    return approve_submission(
        session,
        ctx or _context(actor.id),
        _principal(actor, *(roles or (Role.ADMINISTRATOR,)), suppressed=suppressed),
        submission_id=submission.id,
        reason=reason,
        expected_row_version=(
            submission.row_version if expected_row_version is None else expected_row_version
        ),
        registry=_registry(session),
    )


def _transitions(session: Session, submission_id: uuid.UUID) -> list[SubmissionTransition]:
    return list(
        session.execute(
            select(SubmissionTransition).where(SubmissionTransition.submission_id == submission_id)
        ).scalars()
    )


def _events(session: Session, correlation_id: uuid.UUID) -> list[AuditEvent]:
    return list(
        session.execute(
            select(AuditEvent)
            .where(AuditEvent.correlation_id == correlation_id)
            .order_by(AuditEvent.sequence)
        ).scalars()
    )


def _entries_named(session: Session, term: str) -> int:
    return session.execute(
        select(func.count())
        .select_from(CatalogueEntry)
        .where(CatalogueEntry.preferred_term == term)
    ).scalar_one()


def _designations(session: Session, entry_id: uuid.UUID) -> list[Designation]:
    return list(
        session.execute(select(Designation).where(Designation.entry_id == entry_id)).scalars()
    )


def _bindings(session: Session, entry_id: uuid.UUID) -> list[CodeBinding]:
    return list(
        session.execute(select(CodeBinding).where(CodeBinding.entry_id == entry_id)).scalars()
    )


def _assert_refused_and_nothing_written(
    session: Session, submission: Submission, ctx: AuditContext
) -> None:
    submission_id = submission.id
    session.expire_all()
    reloaded = session.get(Submission, submission_id)
    assert reloaded is not None
    assert reloaded.state == _READY
    assert reloaded.created_entry_id is None
    assert _transitions(session, submission_id) == []
    assert _events(session, ctx.correlation_id) == []


# --- a new test becomes an active entry (FR-28, FR-29, NFR-08) -----------------------------


@pytest.mark.req("FR-28")
@pytest.mark.req("FR-29")
@pytest.mark.integration
def test_approving_a_new_test_creates_an_active_entry_with_its_content(
    app_session: Session,
) -> None:
    admin = _user(app_session)
    submitter = _user(app_session)
    _property(app_session, f"approval_{uuid.uuid4().hex[:8]}")
    key = app_session.execute(
        select(PropertyDefinition.key).order_by(PropertyDefinition.created_at.desc()).limit(1)
    ).scalar_one()
    term = _term("Approved new test")
    synonyms = [_term("Approved synonym one"), _term("Approved synonym two")]
    code = _code()
    submission = _ready_new_test(
        app_session,
        submitter,
        term=term,
        synonyms=synonyms,
        code=code,
        property_values={key: [{"value": "Serum", "justification": "Reviewer confirmed"}]},
    )

    approval = _approve(app_session, submission, admin)

    entry = approval.entry
    assert entry.status == "active"
    assert entry.preferred_term == term
    assert sorted(d.term for d in _designations(app_session, entry.id)) == sorted(synonyms)
    [binding] = _bindings(app_session, entry.id)
    assert (binding.code, binding.fsn, binding.status) == (code, _FSN, "active")
    [value] = app_session.execute(
        select(PropertyValue).where(PropertyValue.entry_id == entry.id)
    ).scalars()
    assert (value.property_key, value.justification) == (key, "Reviewer confirmed")
    assert approval.submission.state == "Approved"
    assert approval.submission.created_entry_id == entry.id


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_new_test_with_no_synonym_code_or_property_is_still_approved(
    app_session: Session,
) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    submission = _ready_new_test(app_session, submitter)

    approval = _approve(app_session, submission, admin)

    assert approval.entry.status == "active"
    assert _designations(app_session, approval.entry.id) == []
    assert _bindings(app_session, approval.entry.id) == []


@pytest.mark.req("FR-23")
@pytest.mark.req("FR-29")
@pytest.mark.integration
def test_the_working_copy_is_what_is_approved_when_there_is_one(app_session: Session) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    submission = _ready_new_test(app_session, submitter, synonyms=[_term("Original synonym")])
    edited_term = _term("Edited by the reviewer")
    edited_synonym = _term("Edited synonym")
    app_session.add(
        SubmissionWorkingCopy(
            submission_id=submission.id, preferred_term=edited_term, synonyms=[edited_synonym]
        )
    )
    app_session.flush()

    approval = _approve(app_session, submission, admin)

    assert approval.entry.preferred_term == edited_term
    assert [d.term for d in _designations(app_session, approval.entry.id)] == [edited_synonym]


@pytest.mark.req("FR-28")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_an_approval_writes_one_transition_row_and_the_audit_events(app_session: Session) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    submission = _ready_new_test(
        app_session, submitter, synonyms=[_term("Audited synonym")], code=_code()
    )
    before = submission.row_version
    ctx = _context(admin.id)

    approval = _approve(app_session, submission, admin, ctx=ctx)

    [row] = _transitions(app_session, submission.id)
    assert (row.from_state, row.to_state, row.actor_id, row.reason) == (
        _READY,
        "Approved",
        admin.id,
        _NOTE,
    )
    assert approval.submission.row_version == before + 1
    events = _events(app_session, ctx.correlation_id)
    assert [e.action for e in events] == [
        "catalogue_entry.created",
        "designation.created",
        "code_binding.created",
        "submission.approved",
    ]
    assert {e.reason for e in events} == {_NOTE}
    approved = events[-1]
    assert approved.before == {"state": _READY, "created_entry_id": None}
    assert approved.after == {"state": "Approved", "created_entry_id": str(approval.entry.id)}


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_reason_is_stored_cleaned_as_the_changelog_note(app_session: Session) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    submission = _ready_new_test(app_session, submitter)
    ctx = _context(admin.id)

    _approve(app_session, submission, admin, ctx=ctx, reason=f"  {_NOTE} \n")

    [row] = _transitions(app_session, submission.id)
    assert row.reason == _NOTE
    assert {e.reason for e in _events(app_session, ctx.correlation_id)} == {_NOTE}


# --- an amendment is applied to its entry (FR-35) ------------------------------------------


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_approving_an_amendment_adds_the_synonyms_and_the_code(app_session: Session) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    entry = _entry(app_session, admin)
    synonym, code = _term("Amended synonym"), _code()
    submission = _ready_amendment(app_session, submitter, entry, synonyms=[synonym], code=code)
    version = entry.row_version
    ctx = _context(admin.id)

    approval = _approve(app_session, submission, admin, ctx=ctx)

    assert approval.entry.id == entry.id
    assert [d.term for d in _designations(app_session, entry.id)] == [synonym]
    [binding] = _bindings(app_session, entry.id)
    assert (binding.code, binding.status) == (code, "active")
    assert approval.submission.state == "Approved"
    assert approval.submission.created_entry_id is None
    assert approval.submission.entry_id == entry.id
    assert entry.row_version == version + 1
    assert {e.reason for e in _events(app_session, ctx.correlation_id)} == {_NOTE}


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_with_only_synonyms_leaves_the_code_alone(app_session: Session) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    entry = _entry(app_session, admin)
    kept = _bind(app_session, admin, entry, _code())
    submission = _ready_amendment(app_session, submitter, entry, synonyms=[_term("Only synonym")])

    _approve(app_session, submission, admin)

    [binding] = _bindings(app_session, entry.id)
    assert (binding.id, binding.status) == (kept.id, "active")


@pytest.mark.req("FR-08")
@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_different_code_retires_the_old_binding_and_links_the_new_one(
    app_session: Session,
) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    entry = _entry(app_session, admin)
    old = _bind(app_session, admin, entry, _code())
    new_code = _code()
    submission = _ready_amendment(app_session, submitter, entry, code=new_code)

    _approve(app_session, submission, admin)

    app_session.refresh(old)
    [successor] = [b for b in _bindings(app_session, entry.id) if b.status == "active"]
    assert successor.code == new_code
    assert old.status == "retired"
    assert old.replaced_by_binding_id == successor.id
    assert old.retirement_reason == _NOTE


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_to_an_entry_that_is_no_longer_active_is_refused(
    app_session: Session,
) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    entry = _entry(app_session, admin)
    submission = _ready_amendment(app_session, submitter, entry, synonyms=[_term("Late synonym")])
    entry.status = "deprecated"
    app_session.flush()
    ctx = _context(admin.id)

    with pytest.raises(AmendmentEntryNotActiveError):
        _approve(app_session, submission, admin, ctx=ctx)

    _assert_refused_and_nothing_written(app_session, submission, ctx)
    assert _designations(app_session, entry.id) == []


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_whose_synonym_the_entry_gained_since_is_refused_whole(
    app_session: Session,
) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    entry = _entry(app_session, admin)
    from nptc.catalogue.designations import add_synonyms

    gained = _term("Gained synonym")
    new_synonym, new_code = _term("Fresh synonym"), _code()
    add_synonyms(
        app_session, _context(admin.id), entry=entry, terms=[gained], reason="Added for a test"
    )
    submission = _ready_amendment(
        app_session, submitter, entry, synonyms=[new_synonym, gained], code=new_code
    )
    ctx = _context(admin.id)

    with pytest.raises(AmendmentNoLongerApplicableError) as refused:
        _approve(app_session, submission, admin, ctx=ctx)

    assert refused.value.cause is AmendmentNoLongerApplicableCause.SYNONYM_EXISTS
    _assert_refused_and_nothing_written(app_session, submission, ctx)
    assert [d.term for d in _designations(app_session, entry.id)] == [gained]
    assert _bindings(app_session, entry.id) == []


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_whose_code_is_already_the_active_code_is_refused(
    app_session: Session,
) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    entry = _entry(app_session, admin)
    code = _code()
    _bind(app_session, admin, entry, code)
    submission = _ready_amendment(app_session, submitter, entry, code=code)
    ctx = _context(admin.id)

    with pytest.raises(AmendmentNoLongerApplicableError) as refused:
        _approve(app_session, submission, admin, ctx=ctx)

    assert refused.value.cause is AmendmentNoLongerApplicableCause.CODE_ALREADY_ACTIVE
    _assert_refused_and_nothing_written(app_session, submission, ctx)


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_an_amendment_is_refused_when_the_entrys_code_was_bound_after_the_proposal(
    app_session: Session,
) -> None:
    """Another change gave the entry a different code since this amendment was proposed. Replacing
    it would retire a code the submitter never saw."""
    admin, submitter = _user(app_session), _user(app_session)
    entry = _entry(app_session, admin)
    unseen = _bind(app_session, admin, entry, _code())
    submission = _ready_amendment(
        app_session, submitter, entry, code=_code(), proposed_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    ctx = _context(admin.id)

    with pytest.raises(AmendmentNoLongerApplicableError) as refused:
        _approve(app_session, submission, admin, ctx=ctx)

    assert refused.value.cause is AmendmentNoLongerApplicableCause.CODE_CHANGED
    _assert_refused_and_nothing_written(app_session, submission, ctx)
    app_session.refresh(unseen)
    assert unseen.status == "active"


@pytest.mark.req("FR-35")
@pytest.mark.integration
def test_a_synonym_only_amendment_is_not_refused_for_a_code_bound_after_the_proposal(
    app_session: Session,
) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    entry = _entry(app_session, admin)
    kept = _bind(app_session, admin, entry, _code())
    submission = _ready_amendment(
        app_session,
        submitter,
        entry,
        synonyms=[_term("Later synonym")],
        proposed_at=datetime(2020, 1, 1, tzinfo=UTC),
    )

    _approve(app_session, submission, admin)

    app_session.refresh(kept)
    assert kept.status == "active"


# --- a failure part-way writes nothing (FR-28, NFR-08) -------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.req("NFR-08")
@pytest.mark.integration
def test_a_failure_after_the_entry_is_written_leaves_nothing_behind(app_session: Session) -> None:
    """The entry, its synonym and its code are written before the property that fails, so the
    rollback has real work to do."""
    admin, submitter = _user(app_session), _user(app_session)
    term = _term("Rolled back new test")
    synonym, code = _term("Rolled back synonym"), _code()
    submission = _ready_new_test(
        app_session,
        submitter,
        term=term,
        synonyms=[synonym],
        code=code,
        property_values={"no_such_property": [{"value": "x"}]},
    )
    ctx = _context(admin.id)

    with pytest.raises(PropertyDefinitionNotFoundError):
        _approve(app_session, submission, admin, ctx=ctx)

    _assert_refused_and_nothing_written(app_session, submission, ctx)
    assert _entries_named(app_session, term) == 0
    bound = app_session.execute(
        select(func.count()).select_from(CodeBinding).where(CodeBinding.code == code)
    ).scalar_one()
    assert bound == 0


@pytest.mark.req("FR-11")
@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_property_deprecated_since_the_proposal_refuses_the_approval(
    app_session: Session,
) -> None:
    """The submission was valid when it was sent. Approval is the next write of the property, so a
    definition retired in between is refused there, with the entry already half written."""
    admin, submitter = _user(app_session), _user(app_session)
    key = f"approval_{uuid.uuid4().hex[:8]}"
    _property(app_session, key)
    definition = app_session.execute(
        select(PropertyDefinition).where(PropertyDefinition.key == key)
    ).scalar_one()
    term = _term("Deprecated property approval")
    submission = _ready_new_test(
        app_session, submitter, term=term, property_values={key: [{"value": "Serum"}]}
    )
    deprecate_definition(
        app_session,
        AuditContext.system(),
        definition=definition,
        expected_row_version=definition.row_version,
        reason="Retired after the proposal was sent",
    )
    app_session.flush()
    ctx = _context(admin.id)

    with pytest.raises(DeprecatedPropertyWriteError):
        _approve(app_session, submission, admin, ctx=ctx)

    _assert_refused_and_nothing_written(app_session, submission, ctx)
    assert _entries_named(app_session, term) == 0


@pytest.mark.req("FR-05")
@pytest.mark.req("FR-28")
@pytest.mark.req("FR-29")
@pytest.mark.integration
def test_a_preferred_term_that_collides_is_refused_and_nothing_is_written(
    app_session: Session,
) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    existing = _entry(app_session, admin)
    submission = _ready_new_test(app_session, submitter, term=existing.preferred_term)
    ctx = _context(admin.id)

    with pytest.raises(DesignationCollisionError):
        _approve(app_session, submission, admin, ctx=ctx)

    _assert_refused_and_nothing_written(app_session, submission, ctx)
    assert _entries_named(app_session, existing.preferred_term) == 1


# --- who may approve (FR-44, FR-81) --------------------------------------------------------


@pytest.mark.req("FR-44")
@pytest.mark.req("FR-81")
@pytest.mark.integration
@pytest.mark.parametrize("role", [Role.REVIEWER, Role.MEMBER, Role.OBSERVER, Role.PROVISIONAL])
def test_a_role_without_the_approve_permission_is_refused_and_nothing_is_written(
    app_session: Session, role: Role
) -> None:
    actor, submitter = _user(app_session), _user(app_session)
    term = _term("Refused for the role")
    submission = _ready_new_test(app_session, submitter, term=term)
    ctx = _context(actor.id)

    with pytest.raises(PermissionDeniedError, match=r"submission\.transition\.approve"):
        _approve(app_session, submission, actor, role, ctx=ctx)

    _assert_refused_and_nothing_written(app_session, submission, ctx)
    assert _entries_named(app_session, term) == 0


@pytest.mark.req("FR-44")
@pytest.mark.integration
def test_an_administrator_who_has_not_stepped_up_is_told_to(app_session: Session) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    submission = _ready_new_test(app_session, submitter)
    ctx = _context(admin.id)

    with pytest.raises(MfaRequiredError):
        _approve(
            app_session,
            submission,
            admin,
            Role.MEMBER,
            ctx=ctx,
            suppressed=frozenset({Role.ADMINISTRATOR}),
        )

    _assert_refused_and_nothing_written(app_session, submission, ctx)


@pytest.mark.req("FR-28")
@pytest.mark.integration
@pytest.mark.parametrize("state", ["Submitted", "Triage", "Under review", "Awaiting terminology"])
def test_a_submission_that_is_not_ready_for_approval_is_refused(
    app_session: Session, state: str
) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    submission = _ready_new_test(app_session, submitter)
    submission.state = state
    app_session.flush()
    ctx = _context(admin.id)

    with pytest.raises(IllegalSubmissionTransitionError):
        _approve(app_session, submission, admin, ctx=ctx)

    assert _transitions(app_session, submission.id) == []
    assert _events(app_session, ctx.correlation_id) == []


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_second_approval_is_refused_and_creates_no_second_entry(app_session: Session) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    term = _term("Approved once")
    submission = _ready_new_test(app_session, submitter, term=term)
    _approve(app_session, submission, admin)

    with pytest.raises(IllegalSubmissionTransitionError):
        _approve(app_session, submission, admin)

    assert _entries_named(app_session, term) == 1
    assert len(_transitions(app_session, submission.id)) == 1


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_an_unknown_submission_is_refused(app_session: Session) -> None:
    admin = _user(app_session)

    with pytest.raises(SubmissionNotFoundError):
        approve_submission(
            app_session,
            _context(admin.id),
            _principal(admin, Role.ADMINISTRATOR),
            submission_id=uuid.uuid4(),
            reason=_NOTE,
            expected_row_version=1,
            registry=_registry(app_session),
        )


# --- the optimistic lock and the reason (FR-28, FR-37, FR-38) ------------------------------


@pytest.mark.req("FR-38")
@pytest.mark.integration
def test_a_stale_row_version_is_a_conflict_and_nothing_is_written(app_session: Session) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    term = _term("Stale approval")
    submission = _ready_new_test(app_session, submitter, term=term)
    ctx = _context(admin.id)

    with pytest.raises(SubmissionVersionConflictError):
        _approve(
            app_session,
            submission,
            admin,
            ctx=ctx,
            expected_row_version=submission.row_version + 1,
        )

    _assert_refused_and_nothing_written(app_session, submission, ctx)
    assert _entries_named(app_session, term) == 0


@pytest.mark.req("FR-37")
@pytest.mark.integration
@pytest.mark.parametrize("reason", ["ok", "Approved", "approved.", "1234567890"])
def test_a_reason_that_is_not_a_meaningful_changelog_note_is_refused(
    app_session: Session, reason: str
) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    term = _term("Weak reason")
    submission = _ready_new_test(app_session, submitter, term=term)
    ctx = _context(admin.id)

    with pytest.raises(ChangelogNoteError):
        _approve(app_session, submission, admin, ctx=ctx, reason=reason)

    _assert_refused_and_nothing_written(app_session, submission, ctx)
    assert _entries_named(app_session, term) == 0


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_a_blank_reason_is_refused(app_session: Session) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    submission = _ready_new_test(app_session, submitter)
    ctx = _context(admin.id)

    with pytest.raises(TransitionReasonRequiredError):
        _approve(app_session, submission, admin, ctx=ctx, reason="   ")

    _assert_refused_and_nothing_written(app_session, submission, ctx)


# --- the general route cannot make the move (FR-28) ----------------------------------------


@pytest.mark.req("FR-28")
@pytest.mark.integration
def test_the_general_transition_refuses_the_approval_move(app_session: Session) -> None:
    admin, submitter = _user(app_session), _user(app_session)
    term = _term("Approved the wrong way")
    submission = _ready_new_test(app_session, submitter, term=term)

    with pytest.raises(TransitionNeedsOwnRouteError):
        transition_submission(
            app_session,
            _context(admin.id),
            _principal(admin, Role.ADMINISTRATOR),
            submission_id=submission.id,
            to_state=SubmissionState.APPROVED,
            reason=_NOTE,
            expected_row_version=submission.row_version,
        )

    assert _entries_named(app_session, term) == 0
