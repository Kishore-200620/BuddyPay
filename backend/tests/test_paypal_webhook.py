import asyncio
import json

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes import paypal
from app.models.payment import PaymentStatus
from app.models.paypal_webhook_event import PayPalWebhookEvent


# ---------------------------------------------------------
# Fake database
# ---------------------------------------------------------
# ---------------------------------------------------------
# Fake database
# ---------------------------------------------------------

class FakeWebhookQuery:

    def __init__(self, db, model):
        self.db = db
        self.model = model
        self.conditions = []

    def filter(self, *conditions, **kwargs):
        self.conditions.extend(conditions)
        return self

    def with_for_update(self):
        return self

    def first(self):
        if self.model == PayPalWebhookEvent:
            for event in self.db.events:
                matches = True

                for condition in self.conditions:
                    column = getattr(condition, "left", None)
                    value = getattr(condition, "right", None)

                    column_name = getattr(column, "key", None)
                    expected_value = getattr(value, "value", None)

                    if column_name == "event_id":
                        if event.event_id != expected_value:
                            matches = False

                if matches:
                    return event

            return None

        if self.model == paypal.Payment:
            return self.db.payment

        return None

class FakeWebhookDB:

    def __init__(self, payment=None, existing_events=None):
        self.payment = payment
        self.events = existing_events or []
        self.added_event = None
        self.search_event_id = None

        self.commit_count = 0
        self.rollback_count = 0
        self.fail_commit = False

        self.original_payment_status = (
            payment.status if payment is not None else None
        )
        self.original_capture_id = (
            payment.paypal_capture_id if payment is not None else None
        )

    def query(self, model):
        return FakeWebhookQuery(self, model)

    def add(self, obj):
        if isinstance(obj, PayPalWebhookEvent):
            self.added_event = obj
            self.search_event_id = obj.event_id

    def commit(self):
        self.commit_count += 1

        if self.fail_commit:
            raise RuntimeError("Simulated database failure")

        if self.added_event is not None:
            self.events.append(self.added_event)
            self.added_event = None

        if self.payment is not None:
            self.original_payment_status = self.payment.status
            self.original_capture_id = self.payment.paypal_capture_id

    def rollback(self):
        self.rollback_count += 1

        if self.payment is not None:
            self.payment.status = self.original_payment_status
            self.payment.paypal_capture_id = self.original_capture_id

        self.added_event = None
        self.search_event_id = None

    def refresh(self, obj):
        pass

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def make_payment(status=PaymentStatus.CAPTURING):
    return SimpleNamespace(
        id=42,
        paypal_order_id="TEST-ORDER-123",
        paypal_capture_id=None,
        amount=Decimal("100.00"),
        currency="USD",
        status=status,
    )


def make_event(
    event_type="PAYMENT.CAPTURE.COMPLETED",
    event_id="WH-TEST-001",
    order_id="TEST-ORDER-123",
    capture_id="TEST-CAPTURE-123",
    amount="100.00",
    currency="USD",
):
    return {
        "id": event_id,
        "event_type": event_type,
        "resource": {
            "id": capture_id,
            "amount": {
                "value": amount,
                "currency_code": currency,
            },
            "supplementary_data": {
                "related_ids": {
                    "order_id": order_id,
                },
            },
        },
    }


def make_request(event):
    body = json.dumps(event).encode("utf-8")

    headers = [
        (b"paypal-transmission-id", b"TEST-TRANSMISSION"),
        (b"paypal-transmission-time", b"2026-11-09T10:00:00Z"),
        (b"paypal-cert-url", b"https://example.com/cert"),
        (b"paypal-auth-algo", b"SHA256withRSA"),
        (b"paypal-transmission-sig", b"TEST-SIGNATURE"),
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


def mock_signature(monkeypatch, result=True):
    verifier = AsyncMock(return_value=result)

    monkeypatch.setattr(
        paypal,
        "verify_paypal_us_webhook_signature",
        verifier,
    )

    return verifier


# ---------------------------------------------------------
# 1. Invalid signature
# ---------------------------------------------------------

def test_webhook_rejects_invalid_signature(monkeypatch):
    db = FakeWebhookDB(make_payment())
    mock_signature(monkeypatch, result=False)

    with pytest.raises(HTTPException) as exc:
        call_webhook(make_event(), db)

    assert exc.value.status_code == 400
    assert db.commit_count == 0
    assert db.events == []


# ---------------------------------------------------------
# 2. Missing transmission headers
# ---------------------------------------------------------

def test_webhook_rejects_missing_headers(monkeypatch):
    db = FakeWebhookDB(make_payment())

    verifier = AsyncMock(
        side_effect=ValueError("Missing headers")
    )

    monkeypatch.setattr(
        paypal,
        "verify_paypal_us_webhook_signature",
        verifier,
    )

    with pytest.raises(HTTPException) as exc:
        call_webhook(make_event(), db)

    assert exc.value.status_code == 400
    assert db.commit_count == 0


# ---------------------------------------------------------
# 3. Successful completed capture
# ---------------------------------------------------------

def test_completed_webhook_updates_payment(monkeypatch):
    payment = make_payment(PaymentStatus.CAPTURING)
    db = FakeWebhookDB(payment)

    mock_signature(monkeypatch)

    result = call_webhook(make_event(), db)

    assert payment.status == PaymentStatus.COMPLETED
    assert payment.paypal_capture_id == "TEST-CAPTURE-123"

    assert result["processing_status"] == "PROCESSED"
    assert result["payment_status"] == "completed"

    assert len(db.events) == 1
    assert db.commit_count == 1


# ---------------------------------------------------------
# 4. Pending capture
# ---------------------------------------------------------

def test_pending_webhook_updates_payment(monkeypatch):
    payment = make_payment(PaymentStatus.CAPTURING)
    db = FakeWebhookDB(payment)

    mock_signature(monkeypatch)

    event = make_event(
        event_type="PAYMENT.CAPTURE.PENDING"
    )

    result = call_webhook(event, db)

    assert payment.status == PaymentStatus.PENDING
    assert result["payment_status"] == "pending"
    assert result["processing_status"] == "PROCESSED"


# ---------------------------------------------------------
# 5. Denied capture
# ---------------------------------------------------------

def test_denied_webhook_updates_payment(monkeypatch):
    payment = make_payment(PaymentStatus.PENDING)
    db = FakeWebhookDB(payment)

    mock_signature(monkeypatch)

    event = make_event(
        event_type="PAYMENT.CAPTURE.DENIED"
    )

    result = call_webhook(event, db)

    assert payment.status == PaymentStatus.FAILED
    assert result["payment_status"] == "failed"
    assert result["processing_status"] == "PROCESSED"


# ---------------------------------------------------------
# 6. Completed payment cannot be downgraded
# ---------------------------------------------------------

def test_late_pending_event_does_not_downgrade_completed_payment(
    monkeypatch,
):
    payment = make_payment(PaymentStatus.COMPLETED)
    payment.paypal_capture_id = "TEST-CAPTURE-123"

    db = FakeWebhookDB(payment)

    mock_signature(monkeypatch)

    event = make_event(
        event_type="PAYMENT.CAPTURE.PENDING"
    )

    result = call_webhook(event, db)

    assert payment.status == PaymentStatus.COMPLETED
    assert result["payment_status"] == "completed"
    assert result["processing_status"] == "IGNORED"


# ---------------------------------------------------------
# 7. Unknown order
# ---------------------------------------------------------

def test_unmatched_webhook_can_be_reprocessed_when_payment_appears(
    monkeypatch,
):
    db = FakeWebhookDB(payment=None)
    mock_signature(monkeypatch)

    event = make_event()

    # First delivery: the payment does not exist yet.
    first_result = call_webhook(event, db)

    assert first_result["processing_status"] == "UNMATCHED"
    assert first_result["reason"] == "No matching BuddyPay payment."
    assert len(db.events) == 1

    webhook_record = db.events[0]

    assert webhook_record.processing_attempts == 1
    assert webhook_record.processed_at is None

    # Simulate the payment becoming available later.
    payment = make_payment(PaymentStatus.CAPTURING)
    db.payment = payment

    # Second delivery: same event ID, now matching payment exists.
    second_result = call_webhook(event, db)

    assert payment.status == PaymentStatus.COMPLETED
    assert payment.paypal_capture_id == "TEST-CAPTURE-123"

    assert second_result["processing_status"] == "PROCESSED"
    assert second_result["payment_status"] == "completed"

    # The same event record must be reused.
    assert len(db.events) == 1
    assert webhook_record.processing_attempts == 2
    assert webhook_record.last_error is None
    assert webhook_record.processed_at is not None

def test_amount_mismatch_does_not_update_payment(monkeypatch):
    payment = make_payment(PaymentStatus.CAPTURING)
    db = FakeWebhookDB(payment)

    mock_signature(monkeypatch)

    event = make_event(amount="500.00")

    result = call_webhook(event, db)

    assert payment.status == PaymentStatus.CAPTURING
    assert result["processing_status"] == "IGNORED"
    assert result["reason"] == "Capture amount or currency mismatch."


# ---------------------------------------------------------
# 9. Currency mismatch
# ---------------------------------------------------------

def test_currency_mismatch_does_not_update_payment(monkeypatch):
    payment = make_payment(PaymentStatus.CAPTURING)
    db = FakeWebhookDB(payment)

    mock_signature(monkeypatch)

    event = make_event(currency="EUR")

    result = call_webhook(event, db)

    assert payment.status == PaymentStatus.CAPTURING
    assert result["processing_status"] == "IGNORED"


# ---------------------------------------------------------
# 10. Duplicate event
# ---------------------------------------------------------

def test_duplicate_webhook_event_is_not_processed_again(
    monkeypatch,
):
    payment = make_payment(PaymentStatus.COMPLETED)
    payment.paypal_capture_id = "TEST-CAPTURE-123"

    existing_event = SimpleNamespace(
        event_id="WH-TEST-001",
        processing_status="PROCESSED",
    )

    db = FakeWebhookDB(
        payment,
        existing_events=[existing_event],
    )

    mock_signature(monkeypatch)

    result = call_webhook(make_event(), db)

    assert result["duplicate"] is True
    assert result["processing_status"] == "PROCESSED"
    assert db.commit_count == 0


# ---------------------------------------------------------
# 11. Database failure
# ---------------------------------------------------------

def test_webhook_database_failure_rolls_back(monkeypatch):
    payment = make_payment(PaymentStatus.CAPTURING)
    db = FakeWebhookDB(payment)
    db.fail_commit = True

    mock_signature(monkeypatch)

    with pytest.raises(HTTPException) as exc:
        call_webhook(make_event(), db)

    assert exc.value.status_code == 503
    assert db.rollback_count == 1
    assert payment.status == PaymentStatus.CAPTURING
def test_permanently_ignored_webhook_is_not_reprocessed(
    monkeypatch,
):
    payment = make_payment(PaymentStatus.CAPTURING)
    db = FakeWebhookDB(payment)

    mock_signature(monkeypatch)

    event = make_event(amount="500.00")

    # First delivery: amount mismatch.
    first_result = call_webhook(event, db)

    assert first_result["processing_status"] == "IGNORED"
    assert payment.status == PaymentStatus.CAPTURING
    assert len(db.events) == 1

    webhook_record = db.events[0]
    assert webhook_record.processing_attempts == 1

    initial_commit_count = db.commit_count

    # Second delivery: must not process the permanently ignored event.
    second_result = call_webhook(event, db)

    assert second_result["duplicate"] is True
    assert second_result["processing_status"] == "IGNORED"

    assert payment.status == PaymentStatus.CAPTURING
    assert len(db.events) == 1
    assert webhook_record.processing_attempts == 1
    assert db.commit_count == initial_commit_count