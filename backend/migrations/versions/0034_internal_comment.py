"""internal comment

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-15

FR-95, NFR-08: `internal_comment`, staff-only comments on a submission or a catalogue entry. See
`nptc.db.models.internal_comment` for the reasoning.

The grants live in this migration with the table. `body`, `author_id` and `created_at` are left out of
the `UPDATE` grant, and `DELETE` and `TRUNCATE` are revoked, so a comment is never edited or removed,
at the privilege level. Only the three `withdrawn_*` columns can change.

The downgrade drops the table, which discards every comment.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "internal_comment",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("submission_id", sa.UUID(), nullable=True),
        sa.Column("entry_id", sa.UUID(), nullable=True),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_by", sa.UUID(), nullable=True),
        sa.Column("withdrawn_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(submission_id IS NULL) <> (entry_id IS NULL)",
            name=op.f("ck_internal_comment_exactly_one_target"),
        ),
        sa.CheckConstraint(
            "body ~ '[^[:space:]]'", name=op.f("ck_internal_comment_body_not_blank")
        ),
        sa.CheckConstraint(
            "(withdrawn_at IS NULL) = (withdrawn_by IS NULL) "
            "AND (withdrawn_at IS NULL) = (withdrawn_reason IS NULL)",
            name=op.f("ck_internal_comment_withdrawal_complete"),
        ),
        sa.CheckConstraint(
            "withdrawn_reason IS NULL OR withdrawn_reason ~ '[^[:space:]]'",
            name=op.f("ck_internal_comment_withdrawn_reason_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submission.id"],
            name=op.f("fk_internal_comment_submission_id_submission"),
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["catalogue_entry.id"],
            name=op.f("fk_internal_comment_entry_id_catalogue_entry"),
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["app_user.id"], name=op.f("fk_internal_comment_author_id_app_user")
        ),
        sa.ForeignKeyConstraint(
            ["withdrawn_by"],
            ["app_user.id"],
            name=op.f("fk_internal_comment_withdrawn_by_app_user"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_internal_comment")),
    )
    op.create_index(
        "ix_internal_comment_submission_id_created_at",
        "internal_comment",
        ["submission_id", "created_at"],
    )
    op.create_index(
        "ix_internal_comment_entry_id_created_at", "internal_comment", ["entry_id", "created_at"]
    )

    op.execute(roles.GRANT_INTERNAL_COMMENT_SQL)
    op.execute(roles.GRANT_INTERNAL_COMMENT_UPDATE_SQL)
    op.execute(roles.REVOKE_INTERNAL_COMMENT_DELETE_SQL)


def downgrade() -> None:
    op.drop_index("ix_internal_comment_entry_id_created_at", table_name="internal_comment")
    op.drop_index("ix_internal_comment_submission_id_created_at", table_name="internal_comment")
    op.drop_table("internal_comment")
