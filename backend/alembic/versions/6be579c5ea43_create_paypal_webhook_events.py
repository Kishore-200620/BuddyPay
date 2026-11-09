
"""create paypal webhook events

Revision ID: 6be579c5ea43
Revises: 71da53f69fd3
Create Date: 2026-11-09 15:10:05.439104
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# Revision identifiers.
revision: str = "6be579c5ea43"
down_revision: Union[str, Sequence[str], None] = "71da53f69fd3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create PayPal webhook event persistence."""

    op.create_table(
        "paypal_webhook_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=150), nullable=False),
        sa.Column("resource_id", sa.String(length=100), nullable=True),
        sa.Column("order_id", sa.String(length=100), nullable=True),
        sa.Column("capture_id", sa.String(length=100), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "processing_status",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column("processing_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id"),
    )

    op.create_index(
        "ix_paypal_webhook_events_capture_id",
        "paypal_webhook_events",
        ["capture_id"],
        unique=False,
    )

    op.create_index(
        "ix_paypal_webhook_events_event_type",
        "paypal_webhook_events",
        ["event_type"],
        unique=False,
    )

    op.create_index(
        "ix_paypal_webhook_events_order_id",
        "paypal_webhook_events",
        ["order_id"],
        unique=False,
    )

    op.create_index(
        "ix_paypal_webhook_events_processing_status",
        "paypal_webhook_events",
        ["processing_status"],
        unique=False,
    )

    op.create_index(
        "ix_paypal_webhook_events_resource_id",
        "paypal_webhook_events",
        ["resource_id"],
        unique=False,
    )

    # IMPORTANT:
    # Do not modify uq_payments_one_active_per_invoice.
    # It is an existing payment safety constraint.


def downgrade() -> None:
    """Remove PayPal webhook event persistence."""

    op.drop_index(
        "ix_paypal_webhook_events_resource_id",
        table_name="paypal_webhook_events",
    )

    op.drop_index(
        "ix_paypal_webhook_events_processing_status",
        table_name="paypal_webhook_events",
    )

    op.drop_index(
        "ix_paypal_webhook_events_order_id",
        table_name="paypal_webhook_events",
    )

    op.drop_index(
        "ix_paypal_webhook_events_event_type",
        table_name="paypal_webhook_events",
    )

    op.drop_index(
        "ix_paypal_webhook_events_capture_id",
        table_name="paypal_webhook_events",
    )

    op.drop_table("paypal_webhook_events")
