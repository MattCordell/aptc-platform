"""The `submission_terminology_request` table: a request for a code that does not exist yet (FR-30, FR-31).

A reviewer who cannot find a SNOMED CT code for a proposed test moves the submission to
`Awaiting terminology` and records here what was asked for and where. One row for each request,
written in the same transaction as that move (`aptc.submissions.terminology_request`).

**A submission can wait more than once.** A reviewer may return it to `Under review` when the
request is declined or abandoned, and ask again later. The old row stays as history, because no
privilege lets it be rewritten or removed, so the current request is the newest row of the
submission (`ix_submission_terminology_request_submission_id_created_at`).

**What is asked for is fixed, how the request is going can change.** `proposed_fsn`,
`target_edition`, `justification` and `raised_date` say what was requested and when, so
`aptc.db.roles.GRANT_SUBMISSION_TERMINOLOGY_REQUEST_UPDATE_SQL` leaves them out of the `UPDATE`
grant. The external reference and the expected release are tracking fields and stay editable while
the submission waits. A wrong request is a new submission or an amendment, not an edit of the
record.

**The external reference is two columns (FR-31).** `external_reference_system` names the system
that holds the concept request and `external_reference_ref` is the identifier inside it, so a
reader never parses one string to find either. Both are required, because a reference without its
system cannot be followed.

**One lock.** There is no version column here. A change to the tracking fields bumps
`submission.row_version`, so an edit and a state transition cannot cross, as for the working copy.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from aptc.db.base import Base

__all__ = ["SubmissionTerminologyRequest", "TargetEdition"]


class TargetEdition(StrEnum):
    """The SNOMED CT edition a requested concept is meant for (FR-31)."""

    AU = "au"
    INT = "int"


#: Plain literals, never built at run time: `test_sql_parameterisation.py` forbids SQL built from
#: runtime data.
_TARGET_EDITION_CHECK_SQL = "target_edition IN ('au','int')"
_PROPOSED_FSN_NOT_BLANK_SQL = "length(btrim(proposed_fsn)) > 0"
_JUSTIFICATION_NOT_BLANK_SQL = "length(btrim(justification)) > 0"
_EXPECTED_RELEASE_ORDER_SQL = "expected_release IS NULL OR expected_release >= raised_date"
_EXTERNAL_REFERENCE_SYSTEM_NOT_BLANK_SQL = "length(btrim(external_reference_system)) > 0"
_EXTERNAL_REFERENCE_REF_NOT_BLANK_SQL = "length(btrim(external_reference_ref)) > 0"


class SubmissionTerminologyRequest(Base):
    __tablename__ = "submission_terminology_request"

    # aptc.audit.policy (NFR-08): every real column classified.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {
            "proposed_fsn",
            "target_edition",
            "justification",
            "external_reference_system",
            "external_reference_ref",
            "raised_date",
            "expected_release",
        }
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"id", "submission_id", "created_at", "updated_at"}
    )

    __table_args__ = (
        CheckConstraint(_PROPOSED_FSN_NOT_BLANK_SQL, name="proposed_fsn_not_blank"),
        CheckConstraint(_TARGET_EDITION_CHECK_SQL, name="target_edition"),
        CheckConstraint(_JUSTIFICATION_NOT_BLANK_SQL, name="justification_not_blank"),
        CheckConstraint(
            _EXTERNAL_REFERENCE_SYSTEM_NOT_BLANK_SQL, name="external_reference_system_not_blank"
        ),
        CheckConstraint(
            _EXTERNAL_REFERENCE_REF_NOT_BLANK_SQL, name="external_reference_ref_not_blank"
        ),
        CheckConstraint(_EXPECTED_RELEASE_ORDER_SQL, name="expected_release_not_before_raised"),
        # A submission's requests, newest first, is the one read on this table. The index serves
        # either direction.
        Index(
            "ix_submission_terminology_request_submission_id_created_at",
            "submission_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submission.id"), nullable=False
    )
    # `active_history=True` on every column in `__audit_fields__`: SQLAlchemy knows an attribute's
    # prior value only if it was loaded before reassignment.
    proposed_fsn: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    target_edition: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    justification: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    external_reference_system: Mapped[str] = mapped_column(
        Text, nullable=False, active_history=True
    )
    external_reference_ref: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    raised_date: Mapped[date] = mapped_column(Date, nullable=False, active_history=True)
    expected_release: Mapped[date | None] = mapped_column(Date, nullable=True, active_history=True)
    # `clock_timestamp()`, not `now()`: two requests in one transaction still order by the instant.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
