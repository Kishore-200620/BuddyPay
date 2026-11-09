"""add capturing payment status

Revision ID: eaaee464f61c
Revises: 98f219bffc06
Create Date: 2026-11-06 18:41:27.772628

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'eaaee464f61c'
down_revision: Union[str, Sequence[str], None] = '98f219bffc06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add CAPTURING to the payment_status enum."""
    op.execute(
        "ALTER TYPE payment_status ADD VALUE IF NOT EXISTS 'CAPTURING'"
    )


def downgrade() -> None:
    """Keep the enum value to avoid destructive changes to payment records."""
    pass
