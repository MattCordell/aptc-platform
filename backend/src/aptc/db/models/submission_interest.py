"""The `submission_interest` table: a user's registered interest in a submission (FR-32, FR-34).

**One row per registration episode.** A partial unique index on `(submission_id, user_id)` where
`revoked_at IS NULL` allows one live row for a pair. Revoking sets `revoked_at`, and registering
again inserts a new row, so the history stays whole and no row is edited except by that one column.
Reusing a single row would either overwrite its `organisation` copy or keep a stale one.

**Immutable after insert, except `revoked_at`.** `aptc.db.roles.GRANT_SUBMISSION_INTEREST_UPDATE_SQL`
leaves every other column out of the `UPDATE` grant, and there is no `DELETE` grant, so who was
interested, and when, is a privilege-level fact.

**`organisation` is the registrant's own copy** of the profile value when they registered. Closing an
account clears the profile (NFR-17); the copy and `user_id` stay, and the audit policy withholds the
copy as it does on `submission` and `app_user`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from aptc.db.base import Base

__all__ = ["SubmissionInterest"]

_REVOKED_AFTER_CREATED_SQL = "revoked_at IS NULL OR revoked_at >= created_at"
#: A plain literal, never built at run time: `test_sql_parameterisation.py` forbids SQL built from
#: runtime data.
_LIVE_PREDICATE_SQL = "revoked_at IS NULL"


class SubmissionInterest(Base):
    __tablename__ = "submission_interest"

    # aptc.audit.policy (NFR-08): every real column classified. `organisation` is withheld (NFR-26):
    # a diff records that it was set, never its value.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"submission_id", "user_id", "revoked_at"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset({"organisation"})
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset({"id", "created_at"})

    __table_args__ = (
        CheckConstraint(_REVOKED_AFTER_CREATED_SQL, name="revoked_after_created"),
        # Serves the count for one submission, the caller's own flag across a page, and the
        # one-live-row rule, all by the leading `submission_id`.
        Index(
            "uq_submission_interest_live",
            "submission_id",
            "user_id",
            unique=True,
            postgresql_where=text(_LIVE_PREDICATE_SQL),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # `active_history=True` on every column in `__audit_fields__` and `__audit_withheld_fields__`:
    # SQLAlchemy knows an attribute's prior value only if it was loaded before reassignment.
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submission.id"), nullable=False, active_history=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False, active_history=True
    )
    organisation: Mapped[str | None] = mapped_column(Text, nullable=True, active_history=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, active_history=True
    )
