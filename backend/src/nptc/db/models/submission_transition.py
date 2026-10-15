"""The `submission_transition` table: one row per change of a submission's state (FR-28, NFR-08).

**Insert-only.** `nptc.db.roles.REVOKE_SUBMISSION_TRANSITION_WRITE_SQL` takes `UPDATE`, `DELETE` and
`TRUNCATE` from the app role, so "who moved this submission, from where, to where and why" is a
privilege-level fact and not an application convention, as for `terms_acceptance`. A submission that
moves twice has two rows.

**Written only by `nptc.submissions.transitions`,** in the same transaction as the change of
`submission.state` and its audit event, so the three agree or none exists.

**`reason` is the actor's own text,** cleaned and never blank. It is stored here and also reaches the
audit event, so a reader of either sees why the submission moved.
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

__all__ = ["SubmissionTransition"]

#: Plain literals, never built from `SubmissionState`: `test_sql_parameterisation.py` forbids SQL
#: built from runtime data. They repeat the state check on `submission`, so a row can never record a
#: state the submission itself could not hold.
_FROM_STATE_CHECK_SQL = (
    "from_state IN ('Submitted','Triage','Under review','Awaiting terminology',"
    "'Ready for approval','Approved','Published in release','Rejected','Withdrawn')"
)
_TO_STATE_CHECK_SQL = (
    "to_state IN ('Submitted','Triage','Under review','Awaiting terminology',"
    "'Ready for approval','Approved','Published in release','Rejected','Withdrawn')"
)
_STATES_DIFFER_SQL = "from_state <> to_state"
_REASON_NOT_BLANK_SQL = "length(btrim(reason)) > 0"


class SubmissionTransition(Base):
    __tablename__ = "submission_transition"

    # nptc.audit.policy (NFR-08): every real column classified. The row is never the subject of an
    # audit event itself: the event is on the submission, and carries the same reason.
    __audit_fields__: ClassVar[frozenset[str] | None] = frozenset(
        {"submission_id", "from_state", "to_state", "actor_id", "reason"}
    )
    __audit_withheld_fields__: ClassVar[frozenset[str]] = frozenset()
    __audit_ignored_fields__: ClassVar[frozenset[str]] = frozenset({"id", "created_at"})

    __table_args__ = (
        CheckConstraint(_FROM_STATE_CHECK_SQL, name="from_state"),
        CheckConstraint(_TO_STATE_CHECK_SQL, name="to_state"),
        CheckConstraint(_STATES_DIFFER_SQL, name="states_differ"),
        CheckConstraint(_REASON_NOT_BLANK_SQL, name="reason_not_blank"),
        # A submission's history, oldest first, is the one read on this table.
        Index("ix_submission_transition_submission_id_created_at", "submission_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    # `active_history=True` on every column in `__audit_fields__`: SQLAlchemy knows an attribute's
    # prior value only if it was loaded before reassignment.
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submission.id"), nullable=False, active_history=True
    )
    from_state: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    to_state: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False, active_history=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, active_history=True)
    # `clock_timestamp()`, not `now()`: two moves in one transaction still order by the instant.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
