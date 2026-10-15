"""The `submission_working_copy` table: a reviewer's edited version of a submission's content (FR-23).

**The original never changes.** `nptc.db.roles.GRANT_SUBMISSION_UPDATE_SQL` leaves every content
column of `submission` out of the `UPDATE` grant, so what the submitter sent stays a privilege-level
fact. A reviewer's edits go here instead: one row per submission, created by the first effective
edit. A submission with no row has not been edited, and its working content is its original.

**One lock.** There is no version column on this table. Every effective edit bumps
`submission.row_version`, so an edit and a state transition cannot cross (FR-23). The service in
`nptc.submissions.working_copy` is the only writer.

**Same content, same rules.** The columns and checks repeat those of `submission`, so a working copy
cannot hold a value the original could not. `snomed_fsn` comes from the terminology server and a
request never supplies it (FR-82). The reference, duplicate and submitter columns are not here: a
reviewer does not edit the evidence the submitter sent.

**`synonyms` and `property_values` are plain `JSONB`,** for the reason on `Submission`: a write
replaces the whole attribute.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base

__all__ = ["SubmissionWorkingCopy"]

#: Plain literals, never built at run time: `test_sql_parameterisation.py` forbids SQL built from
#: runtime data. Each one repeats the matching check on `submission`.
_PREFERRED_TERM_NOT_BLANK_SQL = "length(btrim(preferred_term)) > 0"
_SYNONYMS_IS_ARRAY_SQL = "jsonb_typeof(synonyms) = 'array'"
_PROPERTY_VALUES_IS_OBJECT_SQL = "jsonb_typeof(property_values) = 'object'"
_SNOMED_CODE_CHECK_SQL = "nptc_sctid_is_valid(snomed_code)"
_SNOMED_FSN_NOT_BLANK_SQL = "snomed_fsn IS NULL OR length(btrim(snomed_fsn)) > 0"
_SNOMED_CODE_WITH_FSN_SQL = "(snomed_code IS NULL) = (snomed_fsn IS NULL)"
_NOTES_NOT_BLANK_SQL = "notes IS NULL OR length(btrim(notes)) > 0"


class SubmissionWorkingCopy(Base):
    __tablename__ = "submission_working_copy"

    # nptc.audit.policy (NFR-08): every real column classified.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"preferred_term", "synonyms", "snomed_code", "snomed_fsn", "property_values", "notes"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset(
        {"submission_id", "created_at", "updated_at"}
    )

    __table_args__ = (
        CheckConstraint(_PREFERRED_TERM_NOT_BLANK_SQL, name="preferred_term_not_blank"),
        CheckConstraint(_SYNONYMS_IS_ARRAY_SQL, name="synonyms_is_array"),
        CheckConstraint(_PROPERTY_VALUES_IS_OBJECT_SQL, name="property_values_is_object"),
        CheckConstraint(_SNOMED_CODE_CHECK_SQL, name="snomed_code"),
        CheckConstraint(_SNOMED_FSN_NOT_BLANK_SQL, name="snomed_fsn_not_blank"),
        CheckConstraint(_SNOMED_CODE_WITH_FSN_SQL, name="snomed_code_with_fsn"),
        CheckConstraint(_NOTES_NOT_BLANK_SQL, name="notes_not_blank"),
    )

    # One row per submission, so the foreign key is the primary key.
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submission.id"), primary_key=True
    )
    # `active_history=True` on every column in `__audit_fields__`: SQLAlchemy knows an attribute's
    # prior value only if it was loaded before reassignment.
    preferred_term: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    synonyms: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb"), active_history=True
    )
    snomed_code: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    snomed_fsn: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    property_values: Mapped[dict[str, list[dict[str, object]]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), active_history=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
