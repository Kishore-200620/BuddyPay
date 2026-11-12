
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import paypal
from app.models.invoice import Invoice
from app.models.invoice_currency_group import InvoiceCurrencyGroup
from app.models.payment import Payment, PaymentStatus
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.user import User

@pytest.fixture
def grouped_invoice(db_session, payment_context):
    invoice = db_session.get(Invoice, payment_context["invoice_id"])

    usd_group = InvoiceCurrencyGroup(
        invoice_id=invoice.id,
        currency="USD",
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("10.00"),
        total_amount=Decimal("110.00"),
    )
    inr_group = InvoiceCurrencyGroup(
        invoice_id=invoice.id,
        currency="INR",
        subtotal=Decimal("5000.00"),
        tax_amount=Decimal("500.00"),
        total_amount=Decimal("5500.00"),
    )

    db_session.add_all([usd_group, inr_group])
    db_session.commit()
    db_session.refresh(usd_group)
    db_session.refresh(inr_group)

    yield {
        "invoice": invoice,
        "usd_group": usd_group,
        "inr_group": inr_group,
        **payment_context,
    }

    # Payment records must be deleted before their currency groups.
    db_session.query(Payment).filter(
        Payment.invoice_id == invoice.id
    ).delete(synchronize_session=False)

    db_session.query(InvoiceCurrencyGroup).filter(
        InvoiceCurrencyGroup.invoice_id == invoice.id
    ).delete(synchronize_session=False)

    db_session.commit()


def test_grouped_invoice_requires_currency_group_id(
    db_session,
    grouped_invoice,
    monkeypatch,
):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    create_order = AsyncMock()
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.create_invoice_payment_order(
                invoice_id=grouped_invoice["invoice_id"],
                current_user=SimpleNamespace(
                    id=grouped_invoice["buyer_id"]
                ),
                db=db_session,
            )
        )

    assert exc_info.value.status_code == 422
    create_order.assert_not_awaited()


def test_grouped_invoice_rejects_group_from_another_invoice(
    db_session,
    grouped_invoice,
    payment_context,
    monkeypatch,
):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    create_order = AsyncMock()
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order)

    # Create a second invoice owned by the same buyer.
    other_invoice = Invoice(
        invoice_number=f"INV-OTHER-{payment_context['unique_id']}",
        buyer_id=payment_context["buyer_id"],
        provider_id=payment_context["provider_id"],
        seller_name="Other Provider",
        seller_email="other@example.test",
        subtotal=Decimal("20.00"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("20.00"),
        currency="USD",
        pdf_file_path="integration-tests/other-invoice.pdf",
        status="processed",
    )
    db_session.add(other_invoice)
    db_session.flush()

    foreign_group = InvoiceCurrencyGroup(
        invoice_id=other_invoice.id,
        currency="EUR",
        subtotal=Decimal("20.00"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("20.00"),
    )
    db_session.add(foreign_group)
    db_session.commit()

    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                paypal.create_invoice_payment_order(
                    invoice_id=grouped_invoice["invoice_id"],
                    current_user=SimpleNamespace(
                        id=grouped_invoice["buyer_id"]
                    ),
                    db=db_session,
                    currency_group_id=foreign_group.id,
                )
            )

        assert exc_info.value.status_code == 404
        create_order.assert_not_awaited()

    finally:
        db_session.delete(other_invoice)
        db_session.commit()


def test_grouped_checkout_uses_selected_group_amount_and_currency(
    db_session,
    grouped_invoice,
    monkeypatch,
):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    create_order = AsyncMock(
        return_value={
            "id": "GROUPED-ORDER-USD",
            "links": [
                {
                    "rel": "approve",
                    "href": "https://sandbox.paypal.com/approve/GROUPED-ORDER-USD",
                }
            ],
        }
    )
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order)

    result = asyncio.run(
        paypal.create_invoice_payment_order(
            invoice_id=grouped_invoice["invoice_id"],
            current_user=SimpleNamespace(
                id=grouped_invoice["buyer_id"]
            ),
            db=db_session,
            currency_group_id=grouped_invoice["usd_group"].id,
        )
    )

    create_order.assert_awaited_once()
    call_kwargs = create_order.await_args.kwargs

    assert call_kwargs["amount"] == "110.00"
    assert call_kwargs["currency_code"] == "USD"
    assert f"-group-{grouped_invoice['usd_group'].id}-" in call_kwargs["reference_id"]

    payment = db_session.query(Payment).filter(
        Payment.id == result["payment_id"]
    ).one()

    assert payment.invoice_id == grouped_invoice["invoice_id"]
    assert payment.currency_group_id == grouped_invoice["usd_group"].id
    assert payment.amount == Decimal("110.00")
    assert payment.currency == "USD"
    assert payment.status == PaymentStatus.CREATED

    assert result["currency_group_id"] == grouped_invoice["usd_group"].id
    assert result["paypal_order_id"] == "GROUPED-ORDER-USD"

def test_different_currency_groups_can_be_paid_independently(
    db_session,
    grouped_invoice,
    monkeypatch,
):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    create_order = AsyncMock(
        side_effect=[
            {
                "id": "GROUPED-ORDER-USD",
                "links": [{"rel": "approve", "href": "https://sandbox.paypal.com/approve/USD"}],
            },
            {
                "id": "GROUPED-ORDER-INR",
                "links": [{"rel": "approve", "href": "https://sandbox.paypal.com/approve/INR"}],
            },
        ]
    )
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order)

    buyer = SimpleNamespace(id=grouped_invoice["buyer_id"])
    invoice_id = grouped_invoice["invoice_id"]

    usd_result = asyncio.run(
        paypal.create_invoice_payment_order(
            invoice_id=invoice_id,
            current_user=buyer,
            db=db_session,
            currency_group_id=grouped_invoice["usd_group"].id,
        )
    )

    inr_result = asyncio.run(
        paypal.create_invoice_payment_order(
            invoice_id=invoice_id,
            current_user=buyer,
            db=db_session,
            currency_group_id=grouped_invoice["inr_group"].id,
        )
    )

    assert create_order.await_count == 2

    assert usd_result["currency_group_id"] == grouped_invoice["usd_group"].id
    assert usd_result["amount"] == "110.00"
    assert usd_result["currency"] == "USD"

    assert inr_result["currency_group_id"] == grouped_invoice["inr_group"].id
    assert inr_result["amount"] == "5500.00"
    assert inr_result["currency"] == "INR"

    payments = db_session.query(Payment).filter(
        Payment.invoice_id == invoice_id
    ).all()

    assert len(payments) == 2
    assert {
        payment.currency_group_id for payment in payments
    } == {
        grouped_invoice["usd_group"].id,
        grouped_invoice["inr_group"].id,
    }

def test_same_currency_group_rejects_duplicate_active_payment(
    db_session,
    grouped_invoice,
    monkeypatch,
):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    create_order = AsyncMock(
        return_value={
            "id": "GROUPED-ORDER-DUPLICATE",
            "links": [
                {
                    "rel": "approve",
                    "href": "https://sandbox.paypal.com/approve/DUPLICATE",
                }
            ],
        }
    )
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order)

    buyer = SimpleNamespace(id=grouped_invoice["buyer_id"])
    invoice_id = grouped_invoice["invoice_id"]
    group_id = grouped_invoice["usd_group"].id

    first_result = asyncio.run(
        paypal.create_invoice_payment_order(
            invoice_id=invoice_id,
            current_user=buyer,
            db=db_session,
            currency_group_id=group_id,
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.create_invoice_payment_order(
                invoice_id=invoice_id,
                current_user=buyer,
                db=db_session,
                currency_group_id=group_id,
            )
        )

    assert first_result["status"] == PaymentStatus.CREATED.value
    assert exc_info.value.status_code == 409
    assert create_order.await_count == 1

    payments = db_session.query(Payment).filter(
        Payment.invoice_id == invoice_id,
        Payment.currency_group_id == group_id,
    ).all()

    assert len(payments) == 1

def test_same_currency_group_rejects_already_completed_payment(
    db_session,
    grouped_invoice,
    monkeypatch,
):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    group_id = grouped_invoice["usd_group"].id
    invoice_id = grouped_invoice["invoice_id"]

    completed_payment = Payment(
        invoice_id=invoice_id,
        currency_group_id=group_id,
        buyer_id=grouped_invoice["buyer_id"],
        provider_id=grouped_invoice["provider_id"],
        paypal_order_id="GROUPED-ORDER-ALREADY-PAID",
        amount=Decimal("110.00"),
        currency="USD",
        status=PaymentStatus.COMPLETED,
    )

    db_session.add(completed_payment)
    db_session.commit()

    create_order = AsyncMock()
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.create_invoice_payment_order(
                invoice_id=invoice_id,
                current_user=SimpleNamespace(
                    id=grouped_invoice["buyer_id"]
                ),
                db=db_session,
                currency_group_id=group_id,
            )
        )

    assert exc_info.value.status_code == 409
    create_order.assert_not_awaited()

def test_capturing_one_currency_group_does_not_complete_entire_invoice(
    db_session,
    grouped_invoice,
    monkeypatch,
):
    invoice_id = grouped_invoice["invoice_id"]
    usd_group_id = grouped_invoice["usd_group"].id
    inr_group_id = grouped_invoice["inr_group"].id
    buyer = SimpleNamespace(id=grouped_invoice["buyer_id"])
    order_id = "GROUPED-CAPTURE-USD"

    payment = Payment(
        invoice_id=invoice_id,
        currency_group_id=usd_group_id,
        buyer_id=grouped_invoice["buyer_id"],
        provider_id=grouped_invoice["provider_id"],
        paypal_order_id=order_id,
        amount=Decimal("110.00"),
        currency="USD",
        status=PaymentStatus.CREATED,
    )

    db_session.add(payment)
    db_session.commit()
    db_session.refresh(payment)

    capture_paypal = AsyncMock(
        return_value={
            "id": order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "CAPTURE-GROUPED-USD",
                                "status": "COMPLETED",
                            }
                        ]
                    }
                }
            ],
        }
    )
    monkeypatch.setattr(
        paypal,
        "capture_paypal_us_order",
        capture_paypal,
    )

    result = asyncio.run(
        paypal.capture_invoice_payment_order(
            order_id=order_id,
            current_user=buyer,
            db=db_session,
        )
    )

    capture_paypal.assert_awaited_once_with(order_id)

    assert result["status"] == PaymentStatus.COMPLETED.value
    assert result["paypal_capture_id"] == "CAPTURE-GROUPED-USD"

    # Reload persisted records rather than relying on in-memory objects.
    db_session.expire_all()

    persisted_payment = db_session.query(Payment).filter(
        Payment.paypal_order_id == order_id
    ).one()

    persisted_invoice = db_session.query(Invoice).filter(
        Invoice.id == invoice_id
    ).one()

    groups = {
        group.currency: group
        for group in persisted_invoice.currency_groups
    }

    assert persisted_payment.status == PaymentStatus.COMPLETED
    assert persisted_payment.currency_group_id == usd_group_id
    assert persisted_payment.paypal_capture_id == "CAPTURE-GROUPED-USD"

    assert groups["USD"].payment_status == "completed"
    assert groups["INR"].payment_status == "unpaid"

    assert persisted_invoice.payment_status == "partially_paid"
    buyer_user = db_session.get(
        User,
        grouped_invoice["buyer_id"],
    )

    def override_get_db():
        yield db_session

    previous_get_db = app.dependency_overrides.get(get_db)
    previous_get_current_user = app.dependency_overrides.get(
        get_current_user
    )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: buyer_user

    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/invoices/")

        assert response.status_code == 200

        body = response.json()

        invoice_data = next(
            item
            for item in body["items"]
            if item["id"] == invoice_id
        )

        api_groups = {
            group["currency"]: group
            for group in invoice_data["currency_groups"]
        }

        assert api_groups["USD"]["payment_status"] == "completed"
        assert api_groups["INR"]["payment_status"] == "unpaid"
        assert invoice_data["payment_status"] == "partially_paid"

    finally:
        if previous_get_db is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_get_db

        if previous_get_current_user is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = (
                previous_get_current_user
            )