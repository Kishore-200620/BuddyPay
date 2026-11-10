
from decimal import Decimal

import pytest

from app.models.invoice_currency_group import InvoiceCurrencyGroup
from app.models.payment import Payment, PaymentStatus
from app.services.invoice_service import (
    _synchronize_currency_groups,
    _validate_scalar_financial_history,
    _validate_financial_representation_transition,
)


def test_currency_groups_can_be_updated_without_payment_history(
    payment_context,
    db_session,
):
    invoice = (
        db_session.query(__import__(
            "app.models.invoice", fromlist=["Invoice"]
        ).Invoice)
        .filter_by(id=payment_context["invoice_id"])
        .first()
    )

    invoice.currency_groups.append(
        InvoiceCurrencyGroup(
            currency="INR",
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("1000.00"),
        )
    )
    db_session.commit()

    _synchronize_currency_groups(
        invoice,
        [{
            "currency": "INR",
            "subtotal": Decimal("1500.00"),
            "tax_amount": Decimal("0.00"),
            "total_amount": Decimal("1500.00"),
        }],
    )

    db_session.commit()

    assert len(invoice.currency_groups) == 1
    assert invoice.currency_groups[0].total_amount == Decimal("1500.00")


def test_currency_group_can_be_added_and_removed_without_payments(
    payment_context,
    db_session,
):
    from app.models.invoice import Invoice

    invoice = db_session.get(Invoice, payment_context["invoice_id"])

    _synchronize_currency_groups(
        invoice,
        [
            {
                "currency": "INR",
                "subtotal": Decimal("1000.00"),
                "tax_amount": None,
                "total_amount": Decimal("1000.00"),
            },
            {
                "currency": "USD",
                "subtotal": Decimal("20.00"),
                "tax_amount": None,
                "total_amount": Decimal("20.00"),
            },
        ],
    )

    db_session.commit()

    assert {group.currency for group in invoice.currency_groups} == {
        "INR", "USD"
    }

    _synchronize_currency_groups(
        invoice,
        [{
            "currency": "USD",
            "subtotal": Decimal("25.00"),
            "tax_amount": None,
            "total_amount": Decimal("25.00"),
        }],
    )

    db_session.commit()

    assert len(invoice.currency_groups) == 1
    assert invoice.currency_groups[0].currency == "USD"


@pytest.mark.parametrize(
    "status",
    [
        PaymentStatus.COMPLETED,
        PaymentStatus.FAILED,
        PaymentStatus.CANCELLED,
    ],
)
def test_payment_linked_currency_group_cannot_be_changed(
    payment_context,
    db_session,
    status,
):
    from app.models.invoice import Invoice

    invoice = db_session.get(Invoice, payment_context["invoice_id"])

    group = InvoiceCurrencyGroup(
        invoice_id=invoice.id,
        currency="INR",
        subtotal=Decimal("1000.00"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("1000.00"),
    )

    db_session.add(group)
    db_session.flush()

    payment = Payment(
        invoice_id=invoice.id,
        currency_group_id=group.id,
        buyer_id=invoice.buyer_id,
        provider_id=invoice.provider_id,
        amount=Decimal("1000.00"),
        currency="INR",
        status=status,
    )

    db_session.add(payment)
    db_session.commit()

    with pytest.raises(ValueError, match="payment history exists"):
        _synchronize_currency_groups(
            invoice,
            [{
                "currency": "INR",
                "subtotal": Decimal("2000.00"),
                "tax_amount": Decimal("0.00"),
                "total_amount": Decimal("2000.00"),
            }],
        )

    db_session.rollback()

    persisted_group = db_session.get(InvoiceCurrencyGroup, group.id)

    assert persisted_group is not None
    assert persisted_group.total_amount == Decimal("1000.00")


def test_payment_linked_currency_group_cannot_be_removed(
    payment_context,
    db_session,
):
    from app.models.invoice import Invoice

    invoice = db_session.get(Invoice, payment_context["invoice_id"])

    group = InvoiceCurrencyGroup(
    invoice_id=invoice.id,
    currency="INR",
    subtotal=Decimal("1000.00"),
    tax_amount=None,
    total_amount=Decimal("1000.00"),
)

    db_session.add(group)
    db_session.flush()

    db_session.add(
        Payment(
            invoice_id=invoice.id,
            currency_group_id=group.id,
            buyer_id=invoice.buyer_id,
            provider_id=invoice.provider_id,
            amount=Decimal("1000.00"),
            currency="INR",
            status=PaymentStatus.COMPLETED,
        )
    )

    db_session.commit()

    with pytest.raises(ValueError, match="payment history exists"):
        _synchronize_currency_groups(invoice, [])

    db_session.rollback()

    assert db_session.get(InvoiceCurrencyGroup, group.id) is not None


def test_scalar_invoice_financial_history_cannot_change(
    payment_context,
    db_session,
):
    from app.models.invoice import Invoice

    invoice = db_session.get(Invoice, payment_context["invoice_id"])

    db_session.add(
        Payment(
            invoice_id=invoice.id,
            buyer_id=invoice.buyer_id,
            provider_id=invoice.provider_id,
            amount=Decimal("100.00"),
            currency="USD",
            status=PaymentStatus.COMPLETED,
        )
    )

    db_session.commit()

    with pytest.raises(ValueError, match="payment history exists"):
        _validate_scalar_financial_history(
            invoice=invoice,
            subtotal=Decimal("700.00"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("700.00"),
            currency="USD",
        )


def test_financial_representation_cannot_change_after_payment(
    payment_context,
    db_session,
):
    from app.models.invoice import Invoice

    invoice = db_session.get(Invoice, payment_context["invoice_id"])

    db_session.add(
        Payment(
            invoice_id=invoice.id,
            buyer_id=invoice.buyer_id,
            provider_id=invoice.provider_id,
            amount=Decimal("100.00"),
            currency="USD",
            status=PaymentStatus.COMPLETED,
        )
    )

    db_session.commit()

    with pytest.raises(ValueError, match="payment history exists"):
        _validate_financial_representation_transition(
            invoice=invoice,
            has_incoming_groups=True,
        )
