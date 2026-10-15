"""submission review states

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-15

FR-28, FR-29: the three review states, `Under review`, `Ready for approval` and `Approved`. The
`state` check on `submission` and the two state checks on `submission_transition` are widened
together, so a transition row can never name a state the submission could not hold.

Widening a check cannot invalidate a stored row. The downgrade refuses while a submission, or a
recorded transition, names one of the three states, because the narrower check would fail on it and
the migration would stop half-way. A submission that left a review state for `Rejected` or `Withdrawn`
still has a transition row that names it, and `submission_transition` is insert-only, so that row
cannot be removed to make the downgrade pass.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATE_CONSTRAINT = op.f("ck_submission_state")
_FROM_STATE_CONSTRAINT = op.f("ck_submission_transition_from_state")
_TO_STATE_CONSTRAINT = op.f("ck_submission_transition_to_state")

_STATES_BEFORE = "state IN ('Submitted','Triage','Published in release','Rejected','Withdrawn')"
_STATES_AFTER = (
    "state IN ('Submitted','Triage','Under review','Ready for approval','Approved',"
    "'Published in release','Rejected','Withdrawn')"
)
_FROM_STATE_BEFORE = (
    "from_state IN ('Submitted','Triage','Published in release','Rejected','Withdrawn')"
)
_FROM_STATE_AFTER = (
    "from_state IN ('Submitted','Triage','Under review','Ready for approval','Approved',"
    "'Published in release','Rejected','Withdrawn')"
)
_TO_STATE_BEFORE = (
    "to_state IN ('Submitted','Triage','Published in release','Rejected','Withdrawn')"
)
_TO_STATE_AFTER = (
    "to_state IN ('Submitted','Triage','Under review','Ready for approval','Approved',"
    "'Published in release','Rejected','Withdrawn')"
)


def upgrade() -> None:
    op.drop_constraint(_STATE_CONSTRAINT, "submission", type_="check")
    op.create_check_constraint(_STATE_CONSTRAINT, "submission", _STATES_AFTER)
    op.drop_constraint(_FROM_STATE_CONSTRAINT, "submission_transition", type_="check")
    op.create_check_constraint(_FROM_STATE_CONSTRAINT, "submission_transition", _FROM_STATE_AFTER)
    op.drop_constraint(_TO_STATE_CONSTRAINT, "submission_transition", type_="check")
    op.create_check_constraint(_TO_STATE_CONSTRAINT, "submission_transition", _TO_STATE_AFTER)


def downgrade() -> None:
    bind = op.get_bind()
    submissions = bind.execute(
        sa.text(
            "SELECT count(*) FROM submission "
            "WHERE state IN ('Under review','Ready for approval','Approved')"
        )
    ).scalar_one()
    transitions = bind.execute(
        sa.text(
            "SELECT count(*) FROM submission_transition "
            "WHERE from_state IN ('Under review','Ready for approval','Approved') "
            "OR to_state IN ('Under review','Ready for approval','Approved')"
        )
    ).scalar_one()
    if submissions or transitions:
        raise RuntimeError(
            f"cannot downgrade: {submissions} submission row(s) and {transitions} transition "
            "row(s) name Under review, Ready for approval or Approved, which revision 0034 "
            "does not allow"
        )
    op.drop_constraint(_TO_STATE_CONSTRAINT, "submission_transition", type_="check")
    op.create_check_constraint(_TO_STATE_CONSTRAINT, "submission_transition", _TO_STATE_BEFORE)
    op.drop_constraint(_FROM_STATE_CONSTRAINT, "submission_transition", type_="check")
    op.create_check_constraint(_FROM_STATE_CONSTRAINT, "submission_transition", _FROM_STATE_BEFORE)
    op.drop_constraint(_STATE_CONSTRAINT, "submission", type_="check")
    op.create_check_constraint(_STATE_CONSTRAINT, "submission", _STATES_BEFORE)
