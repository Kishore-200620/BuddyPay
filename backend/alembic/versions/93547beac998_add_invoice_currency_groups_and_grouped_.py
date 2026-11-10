
"""add invoice currency groups and grouped payments

Revision ID: 93547beac998
Revises: 6be579c5ea43
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "93547beac998"
down_revision: Union[str, Sequence[str], None] = "6be579c5ea43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ACTIVE_PAYMENT_STATUSES = (
    "'INITIALIZING'::payment_status, "
    "'CREATED'::payment_status, "
    "'APPROVED'::payment_status, "
    "'CAPTURING'::payment_status, "
    "'PENDING'::payment_status"
)


def upgrade() -> None:
    # 1. Create one financial group per invoice currency.
    op.create_table(
        "invoice_currency_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=True),
        sa.Column("tax_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invoice_id",
            "currency",
            name="uq_invoice_currency_group",
        ),
    )

    op.create_index(
        "ix_invoice_currency_groups_invoice_id",
        "invoice_currency_groups",
        ["invoice_id"],
        unique=False,
    )

    # 2. Add an optional currency-group reference to existing payments.
    # Existing payment records remain NULL and retain legacy behavior.
    op.add_column(
        "payments",
        sa.Column("currency_group_id", sa.Integer(), nullable=True),
    )

    op.create_foreign_key(
        "fk_payments_currency_group_id_invoice_currency_groups",
        "payments",
        "invoice_currency_groups",
        ["currency_group_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_index(
        "ix_payments_currency_group_id",
        "payments",
        ["currency_group_id"],
        unique=False,
    )

    # 3. Replace the old invoice-wide active-payment restriction.
    op.drop_index(
        "uq_payments_one_active_per_invoice",
        table_name="payments",
    )

    # Legacy payments: one active payment per invoice.
    op.create_index(
        "uq_payments_one_active_per_invoice",
        "payments",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text(
            "(currency_group_id IS NULL AND status = ANY (ARRAY["
            + ACTIVE_PAYMENT_STATUSES
            + "]))"
        ),
    )

    # Grouped payments: one active payment per currency group.
    op.create_index(
        "uq_payments_one_active_per_currency_group",
        "payments",
        ["currency_group_id"],
        unique=True,
        postgresql_where=sa.text(
            "(currency_group_id IS NOT NULL AND status = ANY (ARRAY["
            + ACTIVE_PAYMENT_STATUSES
            + "]))"
        ),
    )


def downgrade() -> None:
    # Do not silently destroy currency-group payment associations.
    bind = op.get_bind()

    has_grouped_payments = bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM payments "
            "WHERE currency_group_id IS NOT NULL"
            ")"
        )
    ).scalar()

    if has_grouped_payments:
        raise RuntimeError(
            "Cannot downgrade: grouped payments exist. "
            "Preserve or explicitly migrate those payment records first."
        )

    op.drop_index(
        "uq_payments_one_active_per_currency_group",
        table_name="payments",
    )

    op.drop_index(
        "uq_payments_one_active_per_invoice",
        table_name="payments",
    )

    op.drop_index(
        "ix_payments_currency_group_id",
        table_name="payments",
    )

    op.drop_constraint(
        "fk_payments_currency_group_id_invoice_currency_groups",
        "payments",
        type_="foreignkey",
    )

    op.drop_column("payments", "currency_group_id")

    op.drop_index(
        "ix_invoice_currency_groups_invoice_id",
        table_name="invoice_currency_groups",
    )

    op.drop_table("invoice_currency_groups")

    # Restore the original invoice-wide active-payment restriction.
    op.create_index(
        "uq_payments_one_active_per_invoice",
        "payments",
        ["invoice_id"],
        unique=True,
        postgresql_where=sa.text(
            "(status = ANY (ARRAY["
            + ACTIVE_PAYMENT_STATUSES
            + "]))"
        ),
    )
