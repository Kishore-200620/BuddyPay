
from pydantic import BaseModel, ConfigDict, Field, field_validator


class InvoiceCurrencyGroupExtraction(BaseModel):
    currency: str
    subtotal: str | None
    tax_amount: str | None
    total_amount: str | None

    model_config = ConfigDict(extra="forbid")

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        value = value.strip().upper()

        if len(value) != 3 or not value.isalpha():
            raise ValueError("Currency must be a three-letter code")

        return value


class InvoiceExtraction(BaseModel):
    invoice_number: str | None
    seller_name: str | None
    seller_email: str | None
    invoice_date: str | None
    due_date: str | None

    # Existing single-currency fields.
    subtotal: str | None
    tax_amount: str | None
    total_amount: str | None
    currency: str | None

    # New independent currency breakdown.
    currency_groups: list[InvoiceCurrencyGroupExtraction] = Field(
        default_factory=list
    )

    model_config = ConfigDict(extra="forbid")
