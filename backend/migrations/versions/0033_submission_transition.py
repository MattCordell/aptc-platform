"""submission transition

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-14

FR-28, NFR-08: the `Triage` state, and `submission_transition`, one row per change of a submission's
state. See `nptc.db.models.submission_transition` for the reasoning.

The grants live in this migration with the table. `UPDATE`, `DELETE` and `TRUNCATE` are revoked, so
the history of a submission's moves is never edited or removed, at the privilege level.

Widening the `state` check cannot invalidate a stored row. The downgrade refuses while a submission
holds `Triage`, because the narrower check would fail on it and the migration would stop half-way.
It drops the table, which discards every recorded transition.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATE_CONSTRAINT = op.f("ck_submission_state")
_STATES_BEFORE = "state IN ('Submitted','Published in release','Rejected','Withdrawn')"
_STATES_AFTER = "state IN ('Submitted','Triage','Published in release','Rejected','Withdrawn')"
_FROM_STATE_SQL = (
    "from_state IN ('Submitted','Triage','Published in release','Rejected','Withdrawn')"
)
_TO_STATE_SQL = "to_state IN ('Submitted','Triage','Published in release','Rejected','Withdrawn')"


def upgrade() -> None:
    op.drop_constraint(_STATE_CONSTRAINT, "submission", type_="check")
    op.create_check_constraint(_STATE_CONSTRAINT, "submission", _STATES_AFTER)

    op.create_table(
        "submission_transition",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("submission_id", sa.UUID(), nullable=False),
        sa.Column("from_state", sa.Text(), nullable=False),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(_FROM_STATE_SQL, name=op.f("ck_submission_transition_from_state")),
        sa.CheckConstraint(_TO_STATE_SQL, name=op.f("ck_submission_transition_to_state")),
        sa.CheckConstraint(
            "from_state <> to_state", name=op.f("ck_submission_transition_states_differ")
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0", name=op.f("ck_submission_transition_reason_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submission.id"],
            name=op.f("fk_submission_transition_submission_id_submission"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["app_user.id"], name=op.f("fk_submission_transition_actor_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_transition")),
    )
    op.create_index(
        "ix_submission_transition_submission_id_created_at",
        "submission_transition",
        ["submission_id", "created_at"],
    )

    op.execute(roles.GRANT_SUBMISSION_TRANSITION_SQL)
    op.execute(roles.REVOKE_SUBMISSION_TRANSITION_WRITE_SQL)


def downgrade() -> None:
    triage = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM submission WHERE state = 'Triage'"))
        .scalar_one()
    )
    if triage:
        raise RuntimeError(
            f"cannot downgrade: {triage} submission row(s) are in Triage, "
            "which revision 0032 does not allow"
        )
    op.drop_index(
        "ix_submission_transition_submission_id_created_at", table_name="submission_transition"
    )
    op.drop_table("submission_transition")
    op.drop_constraint(_STATE_CONSTRAINT, "submission", type_="check")
    op.create_check_constraint(_STATE_CONSTRAINT, "submission", _STATES_BEFORE)
