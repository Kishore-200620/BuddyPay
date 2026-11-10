
import logging
from decimal import Decimal, InvalidOperation
from datetime import date

from sqlalchemy.orm import Session, selectinload

from app.core.file_storage import (
    UPLOAD_DIR,
    create_upload_directory,
    generate_invoice_filename,
)
from app.models.invoice import Invoice, InvoiceStatus
from app.models.invoice_currency_group import InvoiceCurrencyGroup
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

        if amount < 0:
            raise ValueError("Amount cannot be negative")

        if amount > Decimal("9999999999.99"):
            raise ValueError("Amount exceeds the supported invoice precision")

        if amount.as_tuple().exponent < -2:
            raise ValueError("Amount cannot have more than two decimal places")

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


def _validate_currency(value: str | None) -> str | None:
    if value is None:
        return None

    currency = value.strip().upper()

    if len(currency) != 3 or not currency.isalpha():
        raise ValueError(
            f"Invalid currency code returned by AI: {currency}"
        )

    return currency


def _synchronize_currency_groups(
    invoice: Invoice,
    parsed_groups: list[dict],
) -> None:
    """
    Synchronize extracted currency groups without modifying
    or deleting groups that have payment history.

    A payment-linked group is immutable through invoice reprocessing.
    """

    existing_groups = {
        group.currency: group
        for group in invoice.currency_groups
    }

    incoming_groups = {
        group_data["currency"]: group_data
        for group_data in parsed_groups
    }

    # Validate every existing group before making any changes.
    for currency, existing_group in existing_groups.items():
        has_payment_history = bool(existing_group.payments)

        incoming_data = incoming_groups.get(currency)

        if has_payment_history:
            if incoming_data is None:
                raise ValueError(
                    f"Cannot remove {currency} currency group: "
                    "payment history exists."
                )

            existing_values = {
                "subtotal": existing_group.subtotal,
                "tax_amount": existing_group.tax_amount,
                "total_amount": existing_group.total_amount,
            }

            incoming_values = {
                "subtotal": incoming_data["subtotal"],
                "tax_amount": incoming_data["tax_amount"],
                "total_amount": incoming_data["total_amount"],
            }

            if existing_values != incoming_values:
                raise ValueError(
                    f"Cannot modify {currency} currency group: "
                    "payment history exists."
                )

    # Update or remove groups that have no payment history.
    for currency, existing_group in list(existing_groups.items()):
        incoming_data = incoming_groups.get(currency)

        if incoming_data is None:
            invoice.currency_groups.remove(existing_group)
            continue

        if existing_group.payments:
            # Preserve the original financial values.
            continue

        existing_group.subtotal = incoming_data["subtotal"]
        existing_group.tax_amount = incoming_data["tax_amount"]
        existing_group.total_amount = incoming_data["total_amount"]

    # Add newly discovered currency groups.
    for currency, group_data in incoming_groups.items():
        if currency not in existing_groups:
            invoice.currency_groups.append(
                InvoiceCurrencyGroup(**group_data)
            )

def _validate_financial_representation_transition(
    invoice: Invoice,
    has_incoming_groups: bool,
) -> None:
    has_existing_groups = bool(invoice.currency_groups)
    has_payment_history = bool(invoice.payments)

    if (
        has_payment_history
        and has_existing_groups != has_incoming_groups
    ):
        raise ValueError(
            "Cannot change invoice financial representation: "
            "payment history exists."
        )


def _validate_scalar_financial_history(
    invoice: Invoice,
    subtotal: Decimal | None,
    tax_amount: Decimal | None,
    total_amount: Decimal | None,
    currency: str,
) -> None:
    """
    Prevent invoice reprocessing from changing scalar financial
    information after a payment has been created.
    """

    if not invoice.payments:
        return

    existing_values = {
        "subtotal": invoice.subtotal,
        "tax_amount": invoice.tax_amount,
        "total_amount": invoice.total_amount,
        "currency": invoice.currency,
    }

    incoming_values = {
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total_amount": total_amount,
        "currency": currency,
    }

    if existing_values != incoming_values:
        raise ValueError(
            "Cannot modify invoice financial information: "
            "payment history exists."
        )


def process_invoice_extraction(
    db: Session,
    invoice: Invoice
) -> Invoice:
    """
    Extract PDF text, use Groq to identify invoice fields,
    validate the returned values, and persist scalar or
    multi-currency financial information.
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

        # Preserve extracted text for auditing.
        invoice.extracted_text = extracted_text
        db.commit()

        # Step 2: Extract structured invoice information.
        result = extract_invoice_information(extracted_text)

        # Step 3: Validate dates.
        invoice_date = _parse_optional_date(result.invoice_date)
        due_date = _parse_optional_date(result.due_date)

        # Step 4: Validate scalar financial amounts.
        scalar_subtotal = _parse_optional_decimal(result.subtotal)
        scalar_tax = _parse_optional_decimal(result.tax_amount)
        scalar_total = _parse_optional_decimal(result.total_amount)
        scalar_currency = _validate_currency(result.currency)

        # Step 5: Validate independent currency groups.
        parsed_groups: list[dict] = []
        seen_currencies: set[str] = set()

        for group in result.currency_groups:
            group_currency = _validate_currency(group.currency)

            if group_currency is None:
                raise ValueError(
                    "Currency group is missing its currency code"
                )

            if group_currency in seen_currencies:
                raise ValueError(
                    f"Duplicate currency group returned: {group_currency}"
                )

            seen_currencies.add(group_currency)

            parsed_groups.append({
                "currency": group_currency,
                "subtotal": _parse_optional_decimal(group.subtotal),
                "tax_amount": _parse_optional_decimal(group.tax_amount),
                "total_amount": _parse_optional_decimal(group.total_amount),
            })

        # Step 6: Enforce mutually exclusive financial representations.
        # Acquire the invoice lock before validating and persisting
        # financial information. Load currency groups and their payments
        locked_invoice = (
            db.query(Invoice)
            .options(
    selectinload(Invoice.payments),
    selectinload(Invoice.currency_groups)
    .selectinload(InvoiceCurrencyGroup.payments),
)
            .filter(Invoice.id == invoice.id)
            .with_for_update()
            .populate_existing()
            .first()
        )

        if locked_invoice is None:
            raise ValueError("Invoice not found.")

        invoice = locked_invoice
        _validate_financial_representation_transition(
    invoice=invoice,
    has_incoming_groups=bool(parsed_groups),
)
        if parsed_groups:
            if any(
                value is not None
                for value in (
                    scalar_subtotal,
                    scalar_tax,
                    scalar_total,
                    scalar_currency,
                )
            ):
                raise ValueError(
                    "Mixed-currency extraction must not contain "
                    "scalar financial fields"
                )

            invoice_subtotal = None
            invoice_tax = None
            invoice_total = None

            # Preserve the database's non-null legacy currency column.
            # The API hides this value when currency groups exist.
            invoice_currency = invoice.currency

        else:
            invoice_subtotal = scalar_subtotal
            invoice_tax = scalar_tax
            invoice_total = scalar_total
            invoice_currency = scalar_currency or invoice.currency
        # Protect scalar invoice financial history.
        if not parsed_groups:
            _validate_scalar_financial_history(
                invoice=invoice,
                subtotal=invoice_subtotal,
                tax_amount=invoice_tax,
                total_amount=invoice_total,
                currency=invoice_currency,
            )
        # Step 7: Persist extraction results atomically.
        invoice.invoice_number = result.invoice_number
        invoice.seller_name = result.seller_name
        invoice.seller_email = result.seller_email
        invoice.invoice_date = invoice_date
        invoice.due_date = due_date

        invoice.subtotal = invoice_subtotal
        invoice.tax_amount = invoice_tax
        invoice.total_amount = invoice_total
        invoice.currency = invoice_currency
        def _validate_financial_representation_transition(
            invoice: Invoice,
            has_incoming_groups: bool,
        ) -> None:
            has_existing_groups = bool(invoice.currency_groups)
            has_payment_history = bool(invoice.payments)

            if (
                has_payment_history
                and has_existing_groups != has_incoming_groups
            ):
                raise ValueError(
                    "Cannot change invoice financial representation: "
                    "payment history exists."
                )
        def _validate_scalar_financial_history(
            invoice: Invoice,
            subtotal: Decimal | None,
            tax_amount: Decimal | None,
            total_amount: Decimal | None,
            currency: str,
        ) -> None:
            """
            Prevent invoice reprocessing from changing scalar financial
            information after a payment has been created.
            """

            if not invoice.payments:
                return

            existing_values = {
                "subtotal": invoice.subtotal,
                "tax_amount": invoice.tax_amount,
                "total_amount": invoice.total_amount,
                "currency": invoice.currency,
            }

            incoming_values = {
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "total_amount": total_amount,
                "currency": currency,
            }

            if existing_values != incoming_values:
                raise ValueError(
                    "Cannot modify invoice financial information: "
                    "payment history exists."
                )

        # Replace groups to avoid duplicates during retries.
        # Synchronize currency groups without damaging payment history.
        _synchronize_currency_groups(
            invoice=invoice,
            parsed_groups=parsed_groups,
        )

        # Step 8: Generate AI summary using the correct currency context.
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
                currency=(
                    invoice.currency
                    if not parsed_groups
                    else None
                ),
                due_date=(
                    invoice.due_date.isoformat()
                    if invoice.due_date
                    else None
                ),
                currency_groups=parsed_groups or None,
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

        failed_invoice = db.get(Invoice, invoice.id)

        if failed_invoice:
            failed_invoice.status = InvoiceStatus.FAILED
            db.commit()
            db.refresh(failed_invoice)
            return failed_invoice

        raise
