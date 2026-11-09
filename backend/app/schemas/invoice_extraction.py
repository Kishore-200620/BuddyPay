
from pydantic import BaseModel, ConfigDict


class InvoiceExtraction(BaseModel):
    invoice_number: str | None
    seller_name: str | None
    seller_email: str | None
    invoice_date: str | None
    due_date: str | None
    subtotal: str | None
    tax_amount: str | None
    total_amount: str | None
    currency: str | None

    model_config = ConfigDict(extra="forbid")
