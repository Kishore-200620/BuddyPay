
"""add pending payment status

Revision ID: 98f219bffc06
Revises: 2b15c879e2f0
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "98f219bffc06"
down_revision: Union[str, Sequence[str], None] = "2b15c879e2f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add PENDING to the existing payment_status enum."""
    op.execute(
        "ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'PENDING'"
    )


def downgrade() -> None:
    """Keep the enum value to avoid destructive changes to payment records."""
    pass
