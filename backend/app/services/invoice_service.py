
import logging
from decimal import Decimal, InvalidOperation
from datetime import date

from sqlalchemy.orm import Session

from app.core.file_storage import (
    UPLOAD_DIR,
    create_upload_directory,
    generate_invoice_filename,
)
from app.models.invoice import Invoice, InvoiceStatus
from app.models.user import User
from app.services.pdf_extraction_service import extract_text_from_pdf
from app.services.invoice_extraction_service import (
    extract_invoice_information,
)
from app.services.invoice_summary_service import (
    generate_invoice_summary,
)

logger = logging.getLogger(__name__)


def create_invoice_upload(
    db: Session,
    buyer: User,
    file_content: bytes
) -> Invoice:

    create_upload_directory()

    filename = generate_invoice_filename()
    file_path = UPLOAD_DIR / filename

    try:
        file_path.write_bytes(file_content)

        invoice = Invoice(
            buyer_id=buyer.id,
            pdf_file_path=str(file_path),
            status=InvoiceStatus.UPLOADED,
        )

        db.add(invoice)
        db.commit()
        db.refresh(invoice)

        return invoice

    except Exception:
        db.rollback()

        if file_path.exists():
            file_path.unlink()

        raise


def _parse_optional_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None

    try:
        amount = Decimal(value)

        if not amount.is_finite():
            raise ValueError("Amount must be finite")

        return amount

    except (InvalidOperation, ValueError) as error:
        raise ValueError(
            f"Invalid financial amount returned by AI: {value}"
        ) from error


def _parse_optional_date(value: str | None) -> date | None:
    if value is None:
        return None

    try:
        return date.fromisoformat(value)

    except ValueError as error:
        raise ValueError(
            f"Invalid date returned by AI: {value}"
        ) from error


def process_invoice_extraction(
    db: Session,
    invoice: Invoice
) -> Invoice:
    """
    Extract PDF text, use Groq to identify invoice fields,
    validate the returned values, and persist them in PostgreSQL.
    """

    try:
        invoice.status = InvoiceStatus.PROCESSING
        db.commit()

        # Step 1: Extract text from the PDF.
        extracted_text = extract_text_from_pdf(
            invoice.pdf_file_path
        )

        if not extracted_text.strip():
            raise ValueError("No readable text found in the PDF.")

        # Preserve OCR/native extracted text for auditing.
        invoice.extracted_text = extracted_text
        db.commit()

        # Step 2: Extract structured invoice information using Groq.
        result = extract_invoice_information(extracted_text)

        # Step 3: Validate dates and financial amounts.
        invoice_date = _parse_optional_date(result.invoice_date)
        due_date = _parse_optional_date(result.due_date)

        subtotal = _parse_optional_decimal(result.subtotal)
        tax_amount = _parse_optional_decimal(result.tax_amount)
        total_amount = _parse_optional_decimal(result.total_amount)

        # Step 4: Validate currency format.
        currency = result.currency

        if currency is not None:
            currency = currency.strip().upper()

            if len(currency) != 3 or not currency.isalpha():
                raise ValueError(
                    f"Invalid currency code returned by AI: {currency}"
                )

        invoice.invoice_number = result.invoice_number
        invoice.seller_name = result.seller_name
        invoice.seller_email = result.seller_email
        invoice.invoice_date = invoice_date
        invoice.due_date = due_date
        invoice.subtotal = subtotal
        invoice.tax_amount = tax_amount
        invoice.total_amount = total_amount
        invoice.currency = currency or invoice.currency

        # Generate AI invoice summary.
        try:
            invoice.ai_summary = generate_invoice_summary(
                extracted_text=extracted_text,
                invoice_number=invoice.invoice_number,
                seller_name=invoice.seller_name,
                total_amount=(
                    str(invoice.total_amount)
                    if invoice.total_amount is not None
                    else None
                ),
                currency=invoice.currency,
                due_date=(
                    invoice.due_date.isoformat()
                    if invoice.due_date
                    else None
                ),
            )

        except Exception:
            logger.exception(
                "AI summary generation failed for invoice ID %s",
                invoice.id,
            )
            invoice.ai_summary = None

        invoice.status = InvoiceStatus.PROCESSED

        db.commit()
        db.refresh(invoice)

        logger.info(
            "Invoice extraction completed successfully for invoice ID %s",
            invoice.id
        )

        return invoice

    except Exception:
        db.rollback()

        logger.exception(
            "Invoice processing failed for invoice ID %s",
            invoice.id
        )

        # Preserve the original PDF and any previously committed OCR text.
        failed_invoice = db.get(Invoice, invoice.id)

        if failed_invoice:
            failed_invoice.status = InvoiceStatus.FAILED
            db.commit()
            db.refresh(failed_invoice)
            return failed_invoice

        raise
