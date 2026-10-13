"""submission interest

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-13

FR-32, FR-34: a registered interest in a submission, one row per registration episode. See
`nptc.db.models.submission_interest` for the reasoning.

The grants live in this migration with the table. Only `revoked_at` is updatable and `DELETE` is
revoked, so "who was interested, and when, is never edited or removed" holds at the privilege level.
The downgrade drops the table, which discards every registered interest.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "submission_interest",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("submission_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("organisation", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name=op.f("ck_submission_interest_revoked_after_created"),
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submission.id"],
            name=op.f("fk_submission_interest_submission_id_submission"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["app_user.id"], name=op.f("fk_submission_interest_user_id_app_user")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_interest")),
    )
    op.create_index(
        "uq_submission_interest_live",
        "submission_interest",
        ["submission_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.execute(roles.GRANT_SUBMISSION_INTEREST_SQL)
    op.execute(roles.GRANT_SUBMISSION_INTEREST_UPDATE_SQL)
    op.execute(roles.REVOKE_SUBMISSION_INTEREST_DELETE_SQL)


def downgrade() -> None:
    op.drop_index("uq_submission_interest_live", table_name="submission_interest")
    op.drop_table("submission_interest")
