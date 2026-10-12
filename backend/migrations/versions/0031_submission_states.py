"""submission closed states

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-13

FR-28, FR-42: the `state` check now also allows the three states that end a submission's life:
`Published in release`, `Rejected` and `Withdrawn`. The read routes treat them as closed, and their
tests insert such rows directly, because no write path reaches them yet.

Widening a check cannot invalidate a stored row, so there is no backfill. The downgrade refuses to
narrow the check while any row holds a widened state, because the new check would fail on it and
the migration would stop half-way.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = op.f("ck_submission_state")


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "submission", type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        "submission",
        "state IN ('Submitted','Published in release','Rejected','Withdrawn')",
    )


def downgrade() -> None:
    widened = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM submission WHERE state <> 'Submitted'"))
        .scalar_one()
    )
    if widened:
        raise RuntimeError(
            f"cannot downgrade: {widened} submission row(s) hold a state that revision 0030 "
            "does not allow"
        )
    op.drop_constraint(_CONSTRAINT, "submission", type_="check")
    op.create_check_constraint(_CONSTRAINT, "submission", "state IN ('Submitted')")
