
import enum
from datetime import datetime, timezone, date
from decimal import Decimal

from sqlalchemy import (
    String,
    DateTime,
    Date,
    Enum,
    ForeignKey,
    Numeric,
    Text,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class InvoiceStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )

    invoice_number: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True
    )

    buyer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Provider associated with this invoice.
    # Nullable to preserve existing invoice records.
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    seller_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    seller_email: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    invoice_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True
    )

    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True
    )

    subtotal: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True
    )

    tax_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True
    )

    total_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True
    )

    currency: Mapped[str] = mapped_column(
        String(3),
        default="USD",
        nullable=False
    )

    pdf_file_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False
    )

    extracted_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    ai_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status"),
        default=InvoiceStatus.UPLOADED,
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

    # Explicit foreign_keys prevent ambiguity between buyer_id and provider_id.
    buyer = relationship(
        "User",
        back_populates="invoices",
        foreign_keys=[buyer_id]
    )

    provider = relationship(
        "User",
        back_populates="provider_invoices",
        foreign_keys=[provider_id]
    )

    __table_args__ = (
        Index("ix_invoices_buyer_created", "buyer_id", "created_at"),
    )

    payments = relationship(
    "Payment",
    back_populates="invoice",
    foreign_keys="Payment.invoice_id"
)
    @property
    def payment_status(self) -> str | None:
        if not self.payments:
            return None

        statuses = [payment.status.value for payment in self.payments]

        if "completed" in statuses:
            return "completed"

        if "created" in statuses or "approved" in statuses:
            return "pending"

        return statuses[-1]