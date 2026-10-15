"""The `internal_comment` table: staff-only comments on a submission or a catalogue entry (FR-95).

**One row targets exactly one thing.** `submission_id` and `entry_id` are each nullable, and a check
requires one and only one of them. A comment on an entry is not tied to a submission, so it survives
whichever submission led to it.

**Append-only.** `nptc.db.roles.GRANT_INTERNAL_COMMENT_UPDATE_SQL` leaves `body`, `author_id` and
`created_at` out of the `UPDATE` grant, and there is no `DELETE` grant, so what was said, by whom and
when is a privilege-level fact. Withdrawing sets the three `withdrawn_*` columns together and keeps
the text, so a withdrawal marks a comment as retracted and does not hide it.

**Nothing on the parent.** Neither `Submission` nor `CatalogueEntry` has a relationship, count or
column for comments, and writing a comment never touches the parent row. A submitter can read
`row_version` and `updated_at` on their submission, so a write there would show that a comment exists.

**`body` is the staff member's own text,** cleaned by `nptc.submissions.shared.clean_free_text`. The
audit policy withholds it: the audit log records that a comment was written, never what it said.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from nptc.db.base import Base

__all__ = ["InternalComment"]

#: Plain literals, never built at run time: `test_sql_parameterisation.py` forbids SQL built from
#: runtime data. Both sides of each `=` or `<>` are plain booleans, so neither can be NULL and a check
#: cannot pass by being unknown.
_EXACTLY_ONE_TARGET_SQL = "(submission_id IS NULL) <> (entry_id IS NULL)"
#: `btrim` strips only spaces, so a body of tabs or newlines would pass it.
_BODY_NOT_BLANK_SQL = "body ~ '[^[:space:]]'"
_WITHDRAWAL_COMPLETE_SQL = (
    "(withdrawn_at IS NULL) = (withdrawn_by IS NULL) "
    "AND (withdrawn_at IS NULL) = (withdrawn_reason IS NULL)"
)
_WITHDRAWN_REASON_NOT_BLANK_SQL = "withdrawn_reason IS NULL OR withdrawn_reason ~ '[^[:space:]]'"


class InternalComment(Base):
    __tablename__ = "internal_comment"

    # nptc.audit.policy (NFR-08): every real column classified. `body` is withheld: a diff records
    # that it was set, never its value.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {
            "submission_id",
            "entry_id",
            "author_id",
            "withdrawn_at",
            "withdrawn_by",
            "withdrawn_reason",
        }
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset({"body"})
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset({"id", "created_at"})

    __table_args__ = (
        CheckConstraint(_EXACTLY_ONE_TARGET_SQL, name="exactly_one_target"),
        CheckConstraint(_BODY_NOT_BLANK_SQL, name="body_not_blank"),
        CheckConstraint(_WITHDRAWAL_COMPLETE_SQL, name="withdrawal_complete"),
        CheckConstraint(_WITHDRAWN_REASON_NOT_BLANK_SQL, name="withdrawn_reason_not_blank"),
        # A target's comments, oldest first, are the one read on this table.
        Index("ix_internal_comment_submission_id_created_at", "submission_id", "created_at"),
        Index("ix_internal_comment_entry_id_created_at", "entry_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # `active_history=True` on every column in `__audit_fields__` and `__audit_withheld_fields__`:
    # SQLAlchemy knows an attribute's prior value only if it was loaded before reassignment.
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submission.id"), nullable=True, active_history=True
    )
    entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalogue_entry.id"), nullable=True, active_history=True
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False, active_history=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    # `clock_timestamp()`, not `now()`: two comments in one transaction still order by the instant.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, active_history=True
    )
    withdrawn_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=True, active_history=True
    )
    withdrawn_reason: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
