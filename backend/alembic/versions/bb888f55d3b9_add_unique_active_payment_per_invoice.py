
"""add unique active payment per invoice

Revision ID: bb888f55d3b9
Revises: eaaee464f61c
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "bb888f55d3b9"
down_revision: Union[str, Sequence[str], None] = "eaaee464f61c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enforce one active payment per invoice."""

    op.create_index(
        "uq_payments_one_active_per_invoice",
        "payments",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('CREATED', 'APPROVED', 'CAPTURING', 'PENDING')"
        ),
    )


def downgrade() -> None:
    """Remove the active payment uniqueness constraint."""

    op.drop_index(
        "uq_payments_one_active_per_invoice",
        table_name="payments",
    )
