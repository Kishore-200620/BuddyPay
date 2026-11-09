
"""Add initializing payment reservation.

Revision ID: 71da53f69fd3
Revises: bb888f55d3b9
"""

from alembic import op
import sqlalchemy as sa


revision = "71da53f69fd3"
down_revision = "bb888f55d3b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL enum values must be committed before they are used.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE payment_status "
            "ADD VALUE IF NOT EXISTS 'INITIALIZING'"
        )

    # Allow a reservation to exist before PayPal returns an order ID.
    op.alter_column(
        "payments",
        "paypal_order_id",
        existing_type=sa.String(length=100),
        nullable=True,
    )

    # Include INITIALIZING in the one-active-payment constraint.
    op.drop_index(
        "uq_payments_one_active_per_invoice",
        table_name="payments",
    )

    op.create_index(
        "uq_payments_one_active_per_invoice",
        "payments",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN "
            "('INITIALIZING', 'CREATED', 'APPROVED', "
            "'CAPTURING', 'PENDING')"
        ),
    )


def downgrade() -> None:
    # Retain the enum value to avoid destructive enum changes.
    op.drop_index(
        "uq_payments_one_active_per_invoice",
        table_name="payments",
    )

    op.create_index(
        "uq_payments_one_active_per_invoice",
        "payments",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN "
            "('CREATED', 'APPROVED', 'CAPTURING', 'PENDING')"
        ),
    )

    op.alter_column(
        "payments",
        "paypal_order_id",
        existing_type=sa.String(length=100),
        nullable=False,
    )
