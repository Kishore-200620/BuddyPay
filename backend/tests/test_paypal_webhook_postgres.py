
import asyncio
import json
import uuid

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest

from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api.routes import paypal
from app.models.payment import Payment, PaymentStatus
from app.models.paypal_webhook_event import PayPalWebhookEvent


def make_event(
    order_id,
    event_type="PAYMENT.CAPTURE.COMPLETED",
    amount="100.00",
    currency="USD",
    event_id=None,
):
    return {
        "id": event_id or f"WH-INT-{uuid.uuid4().hex}",
        "event_type": event_type,
        "resource": {
            "id": f"CAPTURE-INT-{uuid.uuid4().hex}",
            "amount": {
                "value": amount,
                "currency_code": currency,
            },
            "supplementary_data": {
                "related_ids": {
                    "order_id": order_id,
                }
            },
        },
    }


def make_request(event):
    body = json.dumps(event).encode("utf-8")

    headers = [
        (b"paypal-transmission-id", b"INTEGRATION-TRANSMISSION"),
        (b"paypal-transmission-time", b"2026-11-09T10:00:00Z"),
        (b"paypal-cert-url", b"https://example.com/cert"),
        (b"paypal-auth-algo", b"SHA256withRSA"),
        (b"paypal-transmission-sig", b"INTEGRATION-SIGNATURE"),
        (b"content-type", b"application/json"),
    ]

    scope = {
        "type": "http",
        "asgi": {
            "version": "3.0",
            "spec_version": "2.3",
        },
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/paypal/us/webhooks",
        "raw_path": b"/paypal/us/webhooks",
        "query_string": b"",
        "headers": headers,
        "server": ("testserver", 443),
        "client": ("127.0.0.1", 12345),
        "root_path": "",
    }

    async def receive():
        return {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }

    return Request(scope, receive)


def call_webhook(event, db):
    return asyncio.run(
        paypal.receive_paypal_us_webhook(
            request=make_request(event),
            db=db,
        )
    )


@pytest.fixture
def verified_signature(monkeypatch):
    async def always_verified(**kwargs):
        return True

    monkeypatch.setattr(
        paypal,
        "verify_paypal_us_webhook_signature",
        always_verified,
    )


def create_payment(db, context, order_id=None):
    unique_id = context["unique_id"]

    payment = Payment(
        invoice_id=context["invoice_id"],
        buyer_id=context["buyer_id"],
        provider_id=context["provider_id"],
        paypal_order_id=(
            order_id or f"ORDER-INT-{unique_id}"
        ),
        amount=Decimal("100.00"),
        currency="USD",
        status=PaymentStatus.CAPTURING,
    )

    db.add(payment)
    db.commit()
    db.refresh(payment)

    return payment


def test_postgres_webhook_completes_payment(
    db_session,
    payment_context,
    verified_signature,
):
    payment = create_payment(db_session, payment_context)

    event = make_event(payment.paypal_order_id)

    result = call_webhook(event, db_session)

    db_session.refresh(payment)

    saved_event = (
        db_session.query(PayPalWebhookEvent)
        .filter(
            PayPalWebhookEvent.event_id == event["id"]
        )
        .one()
    )

    assert result["processing_status"] == "PROCESSED"
    assert payment.status == PaymentStatus.COMPLETED
    assert payment.paypal_capture_id == event["resource"]["id"]

    assert saved_event.processing_status == "PROCESSED"
    assert saved_event.processing_attempts == 1
    assert saved_event.processed_at is not None


def test_postgres_duplicate_delivery_is_idempotent(
    db_session,
    payment_context,
    verified_signature,
):
    payment = create_payment(db_session, payment_context)

    event = make_event(payment.paypal_order_id)

    first_result = call_webhook(event, db_session)

    db_session.expire_all()

    second_result = call_webhook(event, db_session)

    saved_event = (
        db_session.query(PayPalWebhookEvent)
        .filter(
            PayPalWebhookEvent.event_id == event["id"]
        )
        .one()
    )

    saved_payment = db_session.get(Payment, payment.id)

    assert first_result["processing_status"] == "PROCESSED"

    assert second_result["duplicate"] is True
    assert second_result["processing_status"] == "PROCESSED"

    assert saved_event.processing_attempts == 1
    assert saved_payment.status == PaymentStatus.COMPLETED


def test_postgres_unmatched_event_can_be_retried(
    db_session,
    payment_context,
    verified_signature,
):
    order_id = f"ORDER-INT-{payment_context['unique_id']}"

    event = make_event(order_id)

    first_result = call_webhook(event, db_session)

    saved_event = (
        db_session.query(PayPalWebhookEvent)
        .filter(
            PayPalWebhookEvent.event_id == event["id"]
        )
        .one()
    )

    assert first_result["processing_status"] == "UNMATCHED"
    assert saved_event.processing_attempts == 1
    assert saved_event.processed_at is None

    payment = create_payment(
        db_session,
        payment_context,
        order_id=order_id,
    )

    second_result = call_webhook(event, db_session)

    db_session.refresh(payment)
    db_session.refresh(saved_event)

    assert second_result["processing_status"] == "PROCESSED"

    assert payment.status == PaymentStatus.COMPLETED
    assert saved_event.processing_attempts == 2
    assert saved_event.last_error is None
    assert saved_event.processed_at is not None


def test_postgres_ignored_event_is_not_reprocessed(
    db_session,
    payment_context,
    verified_signature,
):
    payment = create_payment(db_session, payment_context)

    event = make_event(
        payment.paypal_order_id,
        amount="500.00",
    )

    first_result = call_webhook(event, db_session)

    saved_event = (
        db_session.query(PayPalWebhookEvent)
        .filter(
            PayPalWebhookEvent.event_id == event["id"]
        )
        .one()
    )

    assert first_result["processing_status"] == "IGNORED"
    assert saved_event.processing_attempts == 1

    second_result = call_webhook(event, db_session)

    db_session.refresh(saved_event)
    db_session.refresh(payment)

    assert second_result["duplicate"] is True
    assert second_result["processing_status"] == "IGNORED"

    assert saved_event.processing_attempts == 1
    assert payment.status == PaymentStatus.CAPTURING


def test_postgres_concurrent_duplicate_delivery(
    test_engine,
    payment_context,
    verified_signature,
):
    SessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    setup_db = SessionLocal()

    try:
        payment = create_payment(setup_db, payment_context)
        payment_id = payment.id
        order_id = payment.paypal_order_id
    finally:
        setup_db.close()

    event = make_event(order_id)

    barrier = Barrier(2)

    def deliver():
        db = SessionLocal()

        try:
            barrier.wait(timeout=10)

            return call_webhook(event, db)

        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(deliver),
            executor.submit(deliver),
        ]

        results = [
            future.result(timeout=30)
            for future in futures
        ]

    check_db = SessionLocal()

    try:
        saved_event = (
            check_db.query(PayPalWebhookEvent)
            .filter(
                PayPalWebhookEvent.event_id == event["id"]
            )
            .one()
        )

        saved_payment = check_db.get(Payment, payment_id)

        assert saved_payment.status == PaymentStatus.COMPLETED
        assert saved_payment.paypal_capture_id == event["resource"]["id"]

        assert saved_event.processing_status == "PROCESSED"
        assert saved_event.processing_attempts == 1

        assert sum(
            result.get("duplicate") is True
            for result in results
        ) == 1

        assert len(results) == 2

    finally:
        check_db.close()
