"""Who registered interest in a submission, and which organisations they represent (FR-33, FR-34).

Only a caller holding `interest.identities.view` may reach this module's results; the route decides.
A count alone can mislead, because fifteen registrations from one laboratory say less than fifteen
from fifteen, so the identities and the distinct organisations sit beside the count.

**One organisation is one organisation whatever its case or surrounding space.** Records are grouped
on the trimmed, lower-cased copy (spaces, tabs and line breaks trimmed), so `Royal Perth Hospital`
and ` royal perth hospital` count once. The group's label is its most common trimmed spelling, with
a tie going to the one that sorts first by code point (`"C"` collation, so a capital letter wins
over its lower-case twin and the answer never depends on the database's locale or on row order). A
blank copy and a missing one are the same group, kept apart from every named organisation by a null
`organisation` beside a `label`. The grouping holds
whether or not the registration route trims what it stores.

**Both counts read one definition.** `ORGANISATION_KEY` is the grouping expression for the endpoint
and for the list's `distinct_organisation_count`, so the two cannot disagree. The list count leaves
the no-organisation group out, because it is "how many organisations", not "how many groups".

**Only live records count**, closed submissions included: closure freezes interest, it does not
erase it (`nptc.submissions.interest`). A closed account's record stays, under the organisation it
carried at registration.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import ColumnElement, collate, func, select
from sqlalchemy.orm import Session

from nptc.auth.identity import UserRef
from nptc.db.models.submission import Submission
from nptc.db.models.submission_interest import SubmissionInterest
from nptc.db.models.user import User
from nptc.submissions.errors import SubmissionNotFoundError

__all__ = [
    "NO_ORGANISATION_LABEL",
    "InterestIdentities",
    "InterestRecord",
    "OrganisationGroup",
    "distinct_organisation_counts",
    "list_interest_identities",
]

#: What the group with no organisation is called. A person who really belongs to an organisation of
#: that name is told apart by `OrganisationGroup.organisation`, which is null only for this group.
NO_ORGANISATION_LABEL: Final = "No organisation given"

#: What counts as surrounding space. `btrim` alone trims the space character only.
_SPACE: Final = " \t\r\n"
_TRIMMED: Final[ColumnElement[str]] = func.btrim(SubmissionInterest.organisation, _SPACE)

#: The grouping key of an organisation copy. `NULLIF` turns a blank into null, so blank and missing
#: land in one group, and `count(DISTINCT ...)` skips it.
ORGANISATION_KEY: Final[ColumnElement[str]] = func.nullif(func.lower(_TRIMMED), "")

_LIVE = SubmissionInterest.revoked_at.is_(None)


@dataclass(frozen=True, slots=True)
class InterestRecord:
    """One live registration. `person` is the profile as it stands: a closed account's is empty.
    `organisation` is the copy taken at registration, which outlives the account."""

    person: UserRef
    organisation: str | None
    registered_at: datetime


@dataclass(frozen=True, slots=True)
class OrganisationGroup:
    """`organisation` is the group's most common spelling, and null for the group that gave none."""

    organisation: str | None
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class InterestIdentities:
    """`organisations` lists the named groups by size, largest first, then the group that gave none
    when there is one. `records` is newest first."""

    records: tuple[InterestRecord, ...]
    organisations: tuple[OrganisationGroup, ...]

    @property
    def distinct_organisation_count(self) -> int:
        return sum(1 for group in self.organisations if group.organisation is not None)


def list_interest_identities(session: Session, submission_id: uuid.UUID) -> InterestIdentities:
    """Raises `SubmissionNotFoundError` for an unknown submission. Two statements: the records,
    then the grouped counts."""
    if session.get(Submission, submission_id) is None:
        raise SubmissionNotFoundError("no submission has that identifier")
    rows = session.execute(
        select(SubmissionInterest.organisation, SubmissionInterest.created_at, User)
        .join(User, User.id == SubmissionInterest.user_id)
        .where(SubmissionInterest.submission_id == submission_id, _LIVE)
        .order_by(SubmissionInterest.created_at.desc(), SubmissionInterest.id.desc())
    ).all()
    records = tuple(
        InterestRecord(
            person=UserRef.from_user(user), organisation=organisation, registered_at=created_at
        )
        for organisation, created_at, user in rows
    )
    return InterestIdentities(records=records, organisations=_groups(session, submission_id))


def _groups(session: Session, submission_id: uuid.UUID) -> tuple[OrganisationGroup, ...]:
    spelling = func.mode().within_group(collate(_TRIMMED, "C"))
    rows = session.execute(
        select(ORGANISATION_KEY, spelling, func.count())
        .where(SubmissionInterest.submission_id == submission_id, _LIVE)
        .group_by(ORGANISATION_KEY)
    ).all()
    named = [
        OrganisationGroup(organisation=label, label=label, count=count)
        for key, label, count in rows
        if key is not None
    ]
    unnamed = [
        OrganisationGroup(organisation=None, label=NO_ORGANISATION_LABEL, count=count)
        for key, _, count in rows
        if key is None
    ]
    return (*sorted(named, key=lambda group: (-group.count, group.label)), *unnamed)


def distinct_organisation_counts(
    session: Session, submission_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Named organisations among each submission's live records, in one statement. A submission
    with none is present with zero."""
    if not submission_ids:
        return {}
    rows = session.execute(
        select(SubmissionInterest.submission_id, func.count(ORGANISATION_KEY.distinct()))
        .where(SubmissionInterest.submission_id.in_(submission_ids), _LIVE)
        .group_by(SubmissionInterest.submission_id)
    ).all()
    found = dict(rows)
    return {submission_id: found.get(submission_id, 0) for submission_id in submission_ids}
