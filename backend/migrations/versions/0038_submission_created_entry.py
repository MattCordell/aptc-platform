"""submission created entry

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-15

FR-28, FR-29, NFR-08: `submission.created_entry_id`, the catalogue entry that approving a new test
created. See `nptc.db.models.submission` for the reasoning.

The column is nullable, and a check allows a value only on a new test, because an amendment already
names its entry in `entry_id`. No approval exists at revision 0037, so every stored row is null and
satisfies the check without a backfill. A partial unique index means one entry is the product of at
most one submission.

The grants gain one column. The app role's `UPDATE` stays limited to `state`, `updated_at`,
`row_version` and this column, so every content column is still fixed after insert. The grant is a
separate statement from the one in 0027 because that revision runs before the column exists.

The downgrade drops the column, which discards the link from each approved new test to its entry. The
entries stay. Dropping a column removes its grant.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "submission",
        sa.Column("created_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_submission_created_entry_id_catalogue_entry"),
        "submission",
        "catalogue_entry",
        ["created_entry_id"],
        ["id"],
    )
    op.create_index(
        op.f("ix_submission_created_entry_id"),
        "submission",
        ["created_entry_id"],
        unique=True,
        postgresql_where=sa.text("created_entry_id IS NOT NULL"),
    )
    op.create_check_constraint(
        op.f("ck_submission_created_entry_only_on_new_test"),
        "submission",
        "created_entry_id IS NULL OR kind = 'new_test'",
    )
    op.execute(roles.GRANT_SUBMISSION_CREATED_ENTRY_UPDATE_SQL)


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_submission_created_entry_only_on_new_test"), "submission", type_="check"
    )
    op.drop_index(op.f("ix_submission_created_entry_id"), table_name="submission")
    op.drop_constraint(
        op.f("fk_submission_created_entry_id_catalogue_entry"), "submission", type_="foreignkey"
    )
    op.drop_column("submission", "created_entry_id")
