
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class InvoiceCurrencyGroup(Base):
    __tablename__ = "invoice_currency_groups"

    id: Mapped[int] = mapped_column(primary_key=True)

    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
    )

    subtotal: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    tax_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    total_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    invoice = relationship(
        "Invoice",
        back_populates="currency_groups",
        foreign_keys=[invoice_id],
    )

    payments = relationship(
        "Payment",
        back_populates="currency_group",
        foreign_keys="Payment.currency_group_id",
    )

    __table_args__ = (
        UniqueConstraint(
            "invoice_id",
            "currency",
            name="uq_invoice_currency_group",
        ),
        Index(
            "ix_invoice_currency_groups_invoice_id",
            "invoice_id",
        ),
    )
