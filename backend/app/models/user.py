
import enum
from datetime import datetime, timezone

from sqlalchemy import String, DateTime, Enum, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserRole(str, enum.Enum):
    BUYER = "buyer"
    PROVIDER = "provider"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        primary_key=True,
        index=True
    )

    full_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False
    )

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"),
        nullable=False
    )

    hashed_password: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Invoices where this user is the buyer.
    invoices = relationship(
        "Invoice",
        back_populates="buyer",
        foreign_keys="Invoice.buyer_id",
        cascade="all, delete-orphan"
    )

    # Invoices where this user is the provider.
    provider_invoices = relationship(
        "Invoice",
        back_populates="provider",
        foreign_keys="Invoice.provider_id"
    )
    buyer_payments = relationship(
        "Payment",
        back_populates="buyer",
        foreign_keys="Payment.buyer_id"
    )

    provider_payments = relationship(
        "Payment",
        back_populates="provider",
        foreign_keys="Payment.provider_id"
    )