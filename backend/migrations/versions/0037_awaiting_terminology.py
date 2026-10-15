"""awaiting terminology

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-15

FR-30, FR-31, NFR-08: the `Awaiting terminology` state and `submission_terminology_request`, the
record of what a reviewer asked the terminology authority for, one row for each request, so a submission
returned to `Under review` can wait again. See
`nptc.db.models.submission_terminology_request` for the reasoning.

The `state` check on `submission` and the two state checks on `submission_transition` are widened
together, so a transition row can never name a state the submission could not hold. Widening a check
cannot invalidate a stored row.

The grants live in this migration with the table. The columns that say what was asked for are left
out of the `UPDATE` grant, and `DELETE` and `TRUNCATE` are revoked, so a request is never rewritten or
removed, at the privilege level.

The downgrade refuses while a submission, or a recorded transition, names `Awaiting terminology`,
because the narrower check would fail on it and the migration would stop half-way.
`submission_transition` is insert-only, so a transition row cannot be removed to make the downgrade
pass. When it does run, it drops the request table, which discards every request.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from nptc.db import roles

# revision identifiers, used by Alembic.
revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATE_CONSTRAINT = op.f("ck_submission_state")
_FROM_STATE_CONSTRAINT = op.f("ck_submission_transition_from_state")
_TO_STATE_CONSTRAINT = op.f("ck_submission_transition_to_state")

_STATES_BEFORE = (
    "state IN ('Submitted','Triage','Under review','Ready for approval','Approved',"
    "'Published in release','Rejected','Withdrawn')"
)
_STATES_AFTER = (
    "state IN ('Submitted','Triage','Under review','Awaiting terminology','Ready for approval',"
    "'Approved','Published in release','Rejected','Withdrawn')"
)
_FROM_STATE_BEFORE = (
    "from_state IN ('Submitted','Triage','Under review','Ready for approval','Approved',"
    "'Published in release','Rejected','Withdrawn')"
)
_FROM_STATE_AFTER = (
    "from_state IN ('Submitted','Triage','Under review','Awaiting terminology',"
    "'Ready for approval','Approved','Published in release','Rejected','Withdrawn')"
)
_TO_STATE_BEFORE = (
    "to_state IN ('Submitted','Triage','Under review','Ready for approval','Approved',"
    "'Published in release','Rejected','Withdrawn')"
)
_TO_STATE_AFTER = (
    "to_state IN ('Submitted','Triage','Under review','Awaiting terminology',"
    "'Ready for approval','Approved','Published in release','Rejected','Withdrawn')"
)


def upgrade() -> None:
    op.drop_constraint(_STATE_CONSTRAINT, "submission", type_="check")
    op.create_check_constraint(_STATE_CONSTRAINT, "submission", _STATES_AFTER)
    op.drop_constraint(_FROM_STATE_CONSTRAINT, "submission_transition", type_="check")
    op.create_check_constraint(_FROM_STATE_CONSTRAINT, "submission_transition", _FROM_STATE_AFTER)
    op.drop_constraint(_TO_STATE_CONSTRAINT, "submission_transition", type_="check")
    op.create_check_constraint(_TO_STATE_CONSTRAINT, "submission_transition", _TO_STATE_AFTER)

    op.create_table(
        "submission_terminology_request",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("submission_id", sa.UUID(), nullable=False),
        sa.Column("proposed_fsn", sa.Text(), nullable=False),
        sa.Column("target_edition", sa.Text(), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("external_reference_system", sa.Text(), nullable=False),
        sa.Column("external_reference_ref", sa.Text(), nullable=False),
        sa.Column("raised_date", sa.Date(), nullable=False),
        sa.Column("expected_release", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(proposed_fsn)) > 0",
            name=op.f("ck_submission_terminology_request_proposed_fsn_not_blank"),
        ),
        sa.CheckConstraint(
            "target_edition IN ('au','int')",
            name=op.f("ck_submission_terminology_request_target_edition"),
        ),
        sa.CheckConstraint(
            "length(btrim(justification)) > 0",
            name=op.f("ck_submission_terminology_request_justification_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(external_reference_system)) > 0",
            name=op.f("ck_submission_terminology_request_external_reference_system_not_blank"),
        ),
        sa.CheckConstraint(
            "length(btrim(external_reference_ref)) > 0",
            name=op.f("ck_submission_terminology_request_external_reference_ref_not_blank"),
        ),
        sa.CheckConstraint(
            "expected_release IS NULL OR expected_release >= raised_date",
            name=op.f("ck_submission_terminology_request_expected_release_not_before_raised"),
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submission.id"],
            name=op.f("fk_submission_terminology_request_submission_id_submission"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submission_terminology_request")),
    )
    op.create_index(
        "ix_submission_terminology_request_submission_id_created_at",
        "submission_terminology_request",
        ["submission_id", "created_at"],
    )

    op.execute(roles.GRANT_SUBMISSION_TERMINOLOGY_REQUEST_SQL)
    op.execute(roles.GRANT_SUBMISSION_TERMINOLOGY_REQUEST_UPDATE_SQL)
    op.execute(roles.REVOKE_SUBMISSION_TERMINOLOGY_REQUEST_DELETE_SQL)


def downgrade() -> None:
    bind = op.get_bind()
    submissions = bind.execute(
        sa.text("SELECT count(*) FROM submission WHERE state = 'Awaiting terminology'")
    ).scalar_one()
    transitions = bind.execute(
        sa.text(
            "SELECT count(*) FROM submission_transition "
            "WHERE from_state = 'Awaiting terminology' OR to_state = 'Awaiting terminology'"
        )
    ).scalar_one()
    if submissions or transitions:
        raise RuntimeError(
            f"cannot downgrade: {submissions} submission row(s) and {transitions} transition "
            "row(s) name Awaiting terminology, which revision 0036 does not allow"
        )
    op.drop_table("submission_terminology_request")
    op.drop_constraint(_TO_STATE_CONSTRAINT, "submission_transition", type_="check")
    op.create_check_constraint(_TO_STATE_CONSTRAINT, "submission_transition", _TO_STATE_BEFORE)
    op.drop_constraint(_FROM_STATE_CONSTRAINT, "submission_transition", type_="check")
    op.create_check_constraint(_FROM_STATE_CONSTRAINT, "submission_transition", _FROM_STATE_BEFORE)
    op.drop_constraint(_STATE_CONSTRAINT, "submission", type_="check")
    op.create_check_constraint(_STATE_CONSTRAINT, "submission", _STATES_BEFORE)
