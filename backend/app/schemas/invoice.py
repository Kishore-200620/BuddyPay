
from datetime import datetime, date
from decimal import Decimal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    AliasChoices,
)

from app.models.invoice import InvoiceStatus


class ProviderInvoiceCreate(BaseModel):
    buyer_email: EmailStr
    invoice_number: str = Field(min_length=1, max_length=100)
    total_amount: Decimal = Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
    )
    currency: str = Field(default="USD", min_length=3, max_length=3)


class InvoiceCurrencyGroupResponse(BaseModel):
    id: int
    currency: str
    subtotal: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None

    model_config = ConfigDict(from_attributes=True)


class InvoiceResponse(BaseModel):
    id: int
    invoice_number: str | None
    buyer_id: int
    provider_id: int | None

    seller_name: str | None
    seller_email: str | None

    invoice_date: date | None
    due_date: date | None

    subtotal: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None

    currency: str | None = Field(
        validation_alias=AliasChoices("display_currency", "currency")
    )

    currency_groups: list[InvoiceCurrencyGroupResponse] = Field(
        default_factory=list
    )

    status: InvoiceStatus
    created_at: datetime
    payment_status: str | None = None

    model_config = ConfigDict(from_attributes=True)


class InvoiceHistoryResponse(BaseModel):
    items: list[InvoiceResponse]
    total: int
    page: int
    page_size: int
    total_pages: int

    model_config = ConfigDict(from_attributes=True)


class InvoiceDetailResponse(InvoiceResponse):
    ai_summary: str | None = None
