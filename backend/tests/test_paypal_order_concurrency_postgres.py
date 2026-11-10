import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session, sessionmaker

from app.api.routes import paypal
from app.models.user import User
from app.models.payment import Payment, PaymentStatus


def test_concurrent_invoice_order_creation_postgres(
    test_engine,
    payment_context,
    monkeypatch,
):
    """
    Two simultaneous requests for one invoice must create only
    one active payment reservation and one PayPal order.
    """

    monkeypatch.setattr(
        paypal.settings,
        "PAYPAL_US_CLIENT_ID",
        "test-client",
    )
    monkeypatch.setattr(
        paypal.settings,
        "PAYPAL_US_CLIENT_SECRET",
        "test-secret",
    )

    SessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    barrier = threading.Barrier(2)
    paypal_calls = []
    calls_lock = threading.Lock()

    async def fake_create_paypal_us_order(**kwargs):
        with calls_lock:
            paypal_calls.append(kwargs)

        return {
            "id": "ORDER-CONCURRENCY-TEST",
            "links": [
                {
                    "rel": "approve",
                    "href": "https://sandbox.paypal.com/checkoutnow?token=ORDER-CONCURRENCY-TEST",
                }
            ],
        }

    monkeypatch.setattr(
        paypal,
        "create_paypal_us_order",
        fake_create_paypal_us_order,
    )



    def make_request():
        db = SessionLocal()

        try:
            buyer = db.get(User, payment_context["buyer_id"])

            assert buyer is not None

            # Start both requests together before either acquires the invoice lock.
            barrier.wait(timeout=15)

            try:
                result = asyncio.run(
                    paypal.create_invoice_payment_order(
                        invoice_id=payment_context["invoice_id"],
                        current_user=buyer,
                        db=db,
                    )
                )

                return {
                    "type": "success",
                    "result": result,
                }

            except HTTPException as exc:
                return {
                    "type": "http_error",
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                }

        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_one = executor.submit(make_request)
        future_two = executor.submit(make_request)

        result_one = future_one.result(timeout=30)
        result_two = future_two.result(timeout=30)

    results = [result_one, result_two]

    successful = [
        result for result in results
        if result["type"] == "success"
    ]

    conflicts = [
        result for result in results
        if result["type"] == "http_error"
        and result["status_code"] == 409
    ]

    assert len(successful) == 1, results
    assert len(conflicts) == 1, results

    # PayPal must be called exactly once.
    assert len(paypal_calls) == 1, paypal_calls

    # Verify the actual persisted PostgreSQL records.
    with SessionLocal() as verification_db:
        payments = (
            verification_db.query(Payment)
            .filter(
                Payment.invoice_id == payment_context["invoice_id"],
                Payment.status.in_(
                    [
                        PaymentStatus.INITIALIZING,
                        PaymentStatus.CREATED,
                        PaymentStatus.APPROVED,
                        PaymentStatus.CAPTURING,
                        PaymentStatus.PENDING,
                    ]
                ),
            )
            .all()
        )

        assert len(payments) == 1

        payment = payments[0]

        assert payment.status == PaymentStatus.CREATED
        assert payment.paypal_order_id == "ORDER-CONCURRENCY-TEST"

    print("\nCONCURRENCY TEST RESULTS")
    print("Successful requests:", len(successful))
    print("HTTP 409 conflicts:", len(conflicts))
    print("PayPal order creation calls:", len(paypal_calls))
    print("Active payment reservations:", len(payments))
    print("Final payment status:", payment.status.value)
