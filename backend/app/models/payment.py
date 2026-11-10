
import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    String,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    Index,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class PaymentStatus(str, enum.Enum):
    INITIALIZING = "initializing"
    CREATED = "created"
    APPROVED = "approved"
    PENDING = "pending"
    CAPTURING = "capturing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        
    )

    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="RESTRICT"),
        nullable=False,
        index=True
    )
    currency_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("invoice_currency_groups.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    buyer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True
    )

    provider_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True
    )

    paypal_order_id: Mapped[str | None] = mapped_column(
    String(100),
    unique=True,
    nullable=True,
    index=True,
)

    paypal_capture_id: Mapped[str | None] = mapped_column(
        String(100),
        unique=True,
        nullable=True
    )

    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False
    )

    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"),
        default=PaymentStatus.CREATED,
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    invoice = relationship(
        "Invoice",
        back_populates="payments",
        foreign_keys=[invoice_id]
    )
    currency_group = relationship(
        "InvoiceCurrencyGroup",
        back_populates="payments",
        foreign_keys=[currency_group_id],
    )
    buyer = relationship(
        "User",
        back_populates="buyer_payments",
        foreign_keys=[buyer_id]
    )

    provider = relationship(
        "User",
        back_populates="provider_payments",
        foreign_keys=[provider_id]
    )

    __table_args__ = (
        Index(
            "ix_payments_invoice_status",
            "invoice_id",
            "status",
        ),
        Index(
            "uq_payments_one_active_per_invoice",
            "invoice_id",
            unique=True,
            postgresql_where=text(
                "(currency_group_id IS NULL AND status = ANY (ARRAY["
                "'INITIALIZING'::payment_status, "
                "'CREATED'::payment_status, "
                "'APPROVED'::payment_status, "
                "'CAPTURING'::payment_status, "
                "'PENDING'::payment_status"
                "]))"
            ),
        ),
        Index(
            "uq_payments_one_active_per_currency_group",
            "currency_group_id",
            unique=True,
            postgresql_where=text(
                "(currency_group_id IS NOT NULL AND status = ANY (ARRAY["
                "'INITIALIZING'::payment_status, "
                "'CREATED'::payment_status, "
                "'APPROVED'::payment_status, "
                "'CAPTURING'::payment_status, "
                "'PENDING'::payment_status"
                "]))"
            ),
        ),
    )