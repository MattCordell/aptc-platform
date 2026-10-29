"""submission working copy

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-15

FR-23, NFR-08: `submission_working_copy`, a reviewer's edited copy of a submission's content. See
`aptc.db.models.submission_working_copy` for the reasoning.

The grants live in this migration with the table. `submission_id` and `created_at` are left out of the
`UPDATE` grant and `DELETE` and `TRUNCATE` are revoked, so a working copy is never moved or removed, at
the privilege level. The grant on `submission` is unchanged: the original content stays immutable.

The downgrade drops the table, which discards every working copy.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from aptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "submission_working_copy",
        sa.Column("submission_id", sa.UUID(), nullable=False),
        sa.Column("preferred_term", sa.Text(), nullable=False),
        sa.Column(
            "synonyms",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("snomed_code", sa.Text(), nullable=True),
        sa.Column("snomed_fsn", sa.Text(), nullable=True),
        sa.Column(
            "property_values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(preferred_term)) > 0",
            name=op.f("ck_submission_working_copy_preferred_term_not_blank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(synonyms) = 'array'",
            name=op.f("ck_submission_working_copy_synonyms_is_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(property_values) = 'object'",
            name=op.f("ck_submission_working_copy_property_values_is_object"),
        ),
        sa.CheckConstraint(
            "aptc_sctid_is_valid(snomed_code)", name=op.f("ck_submission_working_copy_snomed_code")
        ),
        sa.CheckConstraint(
            "snomed_fsn IS NULL OR length(btrim(snomed_fsn)) > 0",
            name=op.f("ck_submission_working_copy_snomed_fsn_not_blank"),
        ),
        sa.CheckConstraint(
            "(snomed_code IS NULL) = (snomed_fsn IS NULL)",
            name=op.f("ck_submission_working_copy_snomed_code_with_fsn"),
        ),
        sa.CheckConstraint(
            "notes IS NULL OR length(btrim(notes)) > 0",
            name=op.f("ck_submission_working_copy_notes_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submission.id"],
            name=op.f("fk_submission_working_copy_submission_id_submission"),
        ),
        sa.PrimaryKeyConstraint("submission_id", name=op.f("pk_submission_working_copy")),
    )

    op.execute(roles.GRANT_SUBMISSION_WORKING_COPY_SQL)
    op.execute(roles.GRANT_SUBMISSION_WORKING_COPY_UPDATE_SQL)
    op.execute(roles.REVOKE_SUBMISSION_WORKING_COPY_DELETE_SQL)


def downgrade() -> None:
    op.drop_table("submission_working_copy")
