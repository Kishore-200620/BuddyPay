
import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid
import pytest
from fastapi import HTTPException
from datetime import datetime, timedelta, timezone
from app.api.routes import paypal
from app.services import paypal_service
from app.models.payment import Payment, PaymentStatus
from sqlalchemy.sql import visitors, operators
from sqlalchemy.sql.elements import BinaryExpression

class FakeQuery:
    def __init__(self, payment):
        self.payment = payment
        self.filters = []

    def filter(self, *conditions, **kwargs):
        self.filters.extend(conditions)
        return self
    def with_for_update(self):
        return self
    def first(self):
        # Simulate the database filtering by order ID and buyer ID.
        if self.payment is None:
            return None

        return self.payment

class FakeDB:
    def __init__(self, payment):
        self.payment = payment
        self.committed = False
        self.commit_count = 0
        self.refreshed = False
        self.added_payment = None
        self.rolled_back = False
        self.claim_rowcount_override = None
        self.next_payment_id = 100
        self.claim_returning_override = None
        self.reservation_update_rowcount_override = None
    def query(self, model):
        if model.__name__ == "Invoice":
            return FakeQuery(self.get(model, None))

        return FakeQuery(self.payment)

    def execute(self, statement):
        # Simulate the INITIALIZING reservation recovery claim.
        if getattr(statement, "_returning", None):
            if self.claim_returning_override is not None:
                claimed_lease = self.claim_returning_override
            elif (
                self.payment is not None
                and self.payment.status == PaymentStatus.INITIALIZING
                and self.payment.updated_at is not None
                and self.payment.updated_at
                <= datetime.now(timezone.utc) - timedelta(minutes=2)
            ):
                claimed_lease = datetime.now(timezone.utc)
            else:
                claimed_lease = None

            if claimed_lease is not None:
                self.payment.updated_at = claimed_lease

            return SimpleNamespace(
                scalar_one_or_none=lambda: claimed_lease
            )

        # Read the values assigned by the SQLAlchemy UPDATE statement.
        statement_values = getattr(statement, "_values", {}) or {}

        values = {
            getattr(column, "key", str(column)): getattr(value, "value", value)
            for column, value in statement_values.items()
        }

                # Simulate conditional FAILED/CREATED reservation updates.
        if (
            self.payment is not None
            and self.payment.status == PaymentStatus.INITIALIZING
            and (
                "paypal_order_id" in values
                or values.get("status") == PaymentStatus.FAILED
            )
        ):
            # Inspect the SQL WHERE clause for the ownership lease condition.
            whereclause = statement.whereclause
            expected_lease = None

            if whereclause is not None:
                for node in visitors.iterate(whereclause):
                    if (
                        isinstance(node, BinaryExpression)
                        and getattr(node.left, "key", None) == "updated_at"
                        and node.operator is operators.eq
                    ):
                        expected_lease = getattr(node.right, "value", None)
                        break

            # Reject updates that do not contain an ownership condition.
            if expected_lease is None:
                return SimpleNamespace(rowcount=0)

            # Reject a stale worker whose lease no longer matches.
            if expected_lease != self.payment.updated_at:
                return SimpleNamespace(rowcount=0)

            # The current worker still owns the reservation.
            self.payment.status = values["status"]

            if "paypal_order_id" in values:
                self.payment.paypal_order_id = values["paypal_order_id"]

            if "updated_at" in values:
                self.payment.updated_at = values["updated_at"]

            return SimpleNamespace(rowcount=1)

        # Preserve existing payment-capture test behavior.
        if self.claim_rowcount_override is not None:
            return SimpleNamespace(
                rowcount=self.claim_rowcount_override
            )

        if (
            self.payment is not None
            and self.payment.status in (
                PaymentStatus.CREATED,
                PaymentStatus.APPROVED,
            )
        ):
            self.payment.status = PaymentStatus.CAPTURING
            return SimpleNamespace(rowcount=1)

        return SimpleNamespace(rowcount=0)

    def get(self, model, invoice_id):
        return None

    def add(self, payment):
        self.added_payment = payment

    def commit(self):
        self.committed = True
        self.commit_count += 1

        # Simulate PostgreSQL assigning a primary key on insertion.
        if self.added_payment is not None and self.added_payment.id is None:
            self.added_payment.id = self.next_payment_id
            self.next_payment_id += 1

            # Simulate the database assigning the initial updated_at timestamp.
            if self.added_payment.updated_at is None:
                self.added_payment.updated_at = datetime.now(timezone.utc)

            self.payment = self.added_payment

    def refresh(self, payment):
        self.refreshed = True

    def rollback(self):
        self.rolled_back = True

# ---------------------------------------------------------
# PayPal capture tests
# ---------------------------------------------------------

def test_declined_paypal_capture_marks_payment_failed(monkeypatch):
    payment = SimpleNamespace(
        id=42,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-DECLINED",
        paypal_capture_id=None,
        status=PaymentStatus.APPROVED,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_capture = AsyncMock(
        return_value={
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {"id": "TEST-CAPTURE", "status": "DECLINED"}
                        ]
                    }
                }
            ],
        }
    )
    monkeypatch.setattr(paypal, "capture_paypal_us_order", mocked_capture)
    mocked_retrieval = AsyncMock(
    side_effect=RuntimeError("PayPal status unavailable")
)
    monkeypatch.setattr(
        paypal,
        "get_paypal_us_order",
        mocked_retrieval,
    )
    response = asyncio.run(
        paypal.capture_invoice_payment_order(
            order_id="TEST-ORDER-DECLINED",
            current_user=current_user,
            db=db,
        )
    )

    assert response["status"] == "failed"
    assert response["paypal_status"] == "COMPLETED"
    assert payment.status == PaymentStatus.FAILED
    assert db.committed
    assert db.refreshed
    mocked_capture.assert_awaited_once_with("TEST-ORDER-DECLINED")


def test_successful_paypal_capture_marks_payment_completed(monkeypatch):
    payment = SimpleNamespace(
        id=43,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-SUCCESS",
        paypal_capture_id=None,
        status=PaymentStatus.APPROVED,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_capture = AsyncMock(
        return_value={
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "TEST-CAPTURE-SUCCESS",
                                "status": "COMPLETED",
                            }
                        ]
                    }
                }
            ],
        }
    )
    monkeypatch.setattr(paypal, "capture_paypal_us_order", mocked_capture)

    response = asyncio.run(
        paypal.capture_invoice_payment_order(
            order_id="TEST-ORDER-SUCCESS",
            current_user=current_user,
            db=db,
        )
    )

    assert response["status"] == "completed"
    assert response["paypal_capture_id"] == "TEST-CAPTURE-SUCCESS"
    assert payment.status == PaymentStatus.COMPLETED
    assert payment.paypal_capture_id == "TEST-CAPTURE-SUCCESS"
    assert db.committed
    assert db.refreshed
    mocked_capture.assert_awaited_once_with("TEST-ORDER-SUCCESS")


def test_paypal_service_error_returns_502(monkeypatch):
    payment = SimpleNamespace(
        id=44,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-ERROR",
        paypal_capture_id=None,
        status=PaymentStatus.APPROVED,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_capture = AsyncMock(side_effect=RuntimeError("PayPal unavailable"))
    monkeypatch.setattr(paypal, "capture_paypal_us_order", mocked_capture)
    
    mocked_retrieval = AsyncMock(
        side_effect=RuntimeError("PayPal status unavailable")
    )
    monkeypatch.setattr(
        paypal,
        "get_paypal_us_order",
        mocked_retrieval,
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.capture_invoice_payment_order(
                order_id="TEST-ORDER-ERROR",
                current_user=current_user,
                db=db,
            )
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == (
        "PayPal capture outcome is uncertain. "
        "Could not verify the payment status. "
        "Do not retry the capture until reconciled."
    )

    mocked_retrieval.assert_awaited_once_with("TEST-ORDER-ERROR")
    assert payment.status == PaymentStatus.CAPTURING
    assert db.committed
    mocked_capture.assert_awaited_once_with("TEST-ORDER-ERROR")
    

def test_paypal_value_error_returns_422(monkeypatch):
    payment = SimpleNamespace(
        id=45,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-INVALID",
        paypal_capture_id=None,
        status=PaymentStatus.APPROVED,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_capture = AsyncMock(side_effect=ValueError("Invalid PayPal order"))
    monkeypatch.setattr(paypal, "capture_paypal_us_order", mocked_capture)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.capture_invoice_payment_order(
                order_id="TEST-ORDER-INVALID",
                current_user=current_user,
                db=db,
            )
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "Invalid PayPal order"
    assert payment.status == PaymentStatus.APPROVED
    assert db.committed
    assert db.refreshed
    assert mocked_capture.await_count == 1
    mocked_capture.assert_awaited_once_with("TEST-ORDER-INVALID")


def test_payment_not_found_returns_404_without_calling_paypal(monkeypatch):
    db = FakeDB(payment=None)
    current_user = SimpleNamespace(id=7)

    mocked_capture = AsyncMock()
    monkeypatch.setattr(paypal, "capture_paypal_us_order", mocked_capture)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.capture_invoice_payment_order(
                order_id="UNKNOWN-ORDER",
                current_user=current_user,
                db=db,
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Payment not found."
    mocked_capture.assert_not_awaited()
    assert not db.committed


def test_completed_payment_returns_stored_details_without_recapture(monkeypatch):
    payment = SimpleNamespace(
        id=46,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-ALREADY-COMPLETED",
        paypal_capture_id="TEST-CAPTURE-STORED",
        status=PaymentStatus.COMPLETED,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_capture = AsyncMock()
    monkeypatch.setattr(paypal, "capture_paypal_us_order", mocked_capture)

    response = asyncio.run(
        paypal.capture_invoice_payment_order(
            order_id="TEST-ORDER-ALREADY-COMPLETED",
            current_user=current_user,
            db=db,
        )
    )

    assert response == {
        "payment_id": 46,
        "paypal_order_id": "TEST-ORDER-ALREADY-COMPLETED",
        "paypal_capture_id": "TEST-CAPTURE-STORED",
        "status": "completed",
    }
    mocked_capture.assert_not_awaited()
    assert not db.committed


@pytest.mark.parametrize(
    "terminal_status",
    [PaymentStatus.FAILED, PaymentStatus.CANCELLED],
)
def test_terminal_payment_cannot_be_captured(terminal_status, monkeypatch):
    payment = SimpleNamespace(
        id=47,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-TERMINAL",
        paypal_capture_id=None,
        status=terminal_status,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_capture = AsyncMock()
    monkeypatch.setattr(paypal, "capture_paypal_us_order", mocked_capture)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.capture_invoice_payment_order(
                order_id="TEST-ORDER-TERMINAL",
                current_user=current_user,
                db=db,
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == (
        f"Payment is {terminal_status.value} and cannot be captured."
    )
    mocked_capture.assert_not_awaited()
    assert not db.committed


# ---------------------------------------------------------
# PayPal invoice order-creation tests
# ---------------------------------------------------------

def test_create_invoice_order_rejects_existing_active_payment(monkeypatch):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    invoice = SimpleNamespace(
        id=123,
        buyer_id=7,
        provider_id=9,
        total_amount=Decimal("25.00"),
        currency="USD",
    )
    active_payment = SimpleNamespace(status=PaymentStatus.CREATED)
    db = FakeDB(active_payment)
    db.get = lambda model, invoice_id: invoice

    create_order_mock = AsyncMock()
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order_mock)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.create_invoice_payment_order(
                invoice_id=123,
                current_user=SimpleNamespace(id=7),
                db=db,
            )
        )

    assert exc_info.value.status_code == 409
    assert create_order_mock.await_count == 0
    assert db.committed is False

def test_create_invoice_order_rejects_existing_pending_payment(monkeypatch):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    invoice = SimpleNamespace(
        id=123,
        buyer_id=7,
        provider_id=9,
        total_amount=Decimal("25.00"),
        currency="USD",
    )

    pending_payment = SimpleNamespace(status=PaymentStatus.PENDING)
    db = FakeDB(pending_payment)
    db.get = lambda model, invoice_id: invoice

    create_order_mock = AsyncMock()
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order_mock)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.create_invoice_payment_order(
                invoice_id=123,
                current_user=SimpleNamespace(id=7),
                db=db,
            )
        )

    assert exc_info.value.status_code == 409
    assert create_order_mock.await_count == 0
    assert db.committed is False
def test_create_invoice_order_returns_404_for_missing_invoice(monkeypatch):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    db = FakeDB(Payment)
    db.get = lambda model, invoice_id: None

    create_order_mock = AsyncMock()
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order_mock)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.create_invoice_payment_order(
                invoice_id=123,
                current_user=SimpleNamespace(id=7),
                db=db,
            )
        )

    assert exc_info.value.status_code == 404
    assert create_order_mock.await_count == 0
    assert db.committed is False


@pytest.mark.parametrize("total_amount", ["0.00", "-10.00"])
def test_create_invoice_order_rejects_invalid_total(
    monkeypatch, total_amount
):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    invoice = SimpleNamespace(
        id=123,
        buyer_id=7,
        provider_id=9,
        total_amount=Decimal(total_amount),
        currency="USD",
    )
    db = FakeDB(None)
    db.get = lambda model, invoice_id: invoice

    create_order_mock = AsyncMock()
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order_mock)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.create_invoice_payment_order(
                invoice_id=123,
                current_user=SimpleNamespace(id=7),
                db=db,
            )
        )

    assert exc_info.value.status_code == 422
    assert create_order_mock.await_count == 0
    assert db.committed is False


def test_create_invoice_order_rejects_already_paid_invoice(monkeypatch):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    invoice = SimpleNamespace(
        id=123,
        buyer_id=7,
        provider_id=9,
        total_amount=Decimal("25.00"),
        currency="USD",
    )

    db = FakeDB(SimpleNamespace(status=PaymentStatus.COMPLETED))
    db.get = lambda model, invoice_id: invoice

    create_order_mock = AsyncMock()
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order_mock)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.create_invoice_payment_order(
                invoice_id=123,
                current_user=SimpleNamespace(id=7),
                db=db,
            )
        )

    assert exc_info.value.status_code == 409
    assert create_order_mock.await_count == 0
    assert db.committed is False


def test_create_invoice_order_saves_payment_and_returns_approval_url(monkeypatch):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    invoice = SimpleNamespace(
        id=123,
        buyer_id=7,
        provider_id=9,
        total_amount=Decimal("25.00"),
        currency="USD",
    )

    # No active or completed payment exists.
    db = FakeDB(None)
    db.get = lambda model, invoice_id: invoice

    create_order_mock = AsyncMock(
        return_value={
            "id": "PAYPAL-ORDER-123",
            "links": [
                {
                    "rel": "approve",
                    "href": "https://sandbox.paypal.com/approve/PAYPAL-ORDER-123",
                }
            ],
        }
    )
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order_mock)

    result = asyncio.run(
        paypal.create_invoice_payment_order(
            invoice_id=123,
            current_user=SimpleNamespace(id=7),
            db=db,
        )
    )

    assert result["paypal_order_id"] == "PAYPAL-ORDER-123"
    assert result["approval_url"] == (
        "https://sandbox.paypal.com/approve/PAYPAL-ORDER-123"
    )
    assert result["status"] == PaymentStatus.CREATED
    assert db.committed is True
    assert db.refreshed is True

    # Verify the payment record was saved correctly.
    assert db.added_payment is not None
    assert db.added_payment.paypal_order_id == "PAYPAL-ORDER-123"
    assert db.added_payment.amount == Decimal("25.00")
    assert db.added_payment.currency == "USD"
    assert db.rolled_back is False

    create_order_mock.assert_awaited_once_with(
    amount="25.00",
    currency_code="USD",
    reference_id="invoice-123-buyer-7",
    request_id=str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"buddypay-payment-{db.added_payment.id}",
        )
    ),
)


def test_buyer_cannot_capture_another_buyers_payment(monkeypatch):
    payment = SimpleNamespace(
        id=48,
        buyer_id=999,  # Belongs to a different buyer
        paypal_order_id="TEST-ORDER-OTHER-BUYER",
        paypal_capture_id=None,
        status=PaymentStatus.APPROVED,
    )
    db = FakeDB(None)
    current_user = SimpleNamespace(id=7)

    mocked_capture = AsyncMock()
    monkeypatch.setattr(paypal, "capture_paypal_us_order", mocked_capture)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.capture_invoice_payment_order(
                order_id="TEST-ORDER-OTHER-BUYER",
                current_user=current_user,
                db=db,
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Payment not found."
    mocked_capture.assert_not_awaited()
    assert not db.committed
def test_paypal_service_rejects_unsupported_currency_before_api_call(monkeypatch):
    mocked_token = AsyncMock()
    monkeypatch.setattr(
        paypal_service,
        "get_paypal_access_token",
        mocked_token,
    )

    with pytest.raises(ValueError) as exc_info:
        asyncio.run(
            paypal_service.create_paypal_us_order(
                amount="5000.00",
                currency_code="INR",
                reference_id="invoice-123-buyer-7",
            )
        )

    assert "INR" in str(exc_info.value)
    assert "not supported" in str(exc_info.value)
    mocked_token.assert_not_awaited()
@pytest.mark.parametrize(
    "currency_code",
    ["US", "USDD", "12A", "U$D", ""],
)
def test_paypal_service_rejects_invalid_currency_format(
    monkeypatch,
    currency_code,
):
    mocked_token = AsyncMock()
    monkeypatch.setattr(
        paypal_service,
        "get_paypal_access_token",
        mocked_token,
    )

    with pytest.raises(ValueError):
        asyncio.run(
            paypal_service.create_paypal_us_order(
                amount="25.00",
                currency_code=currency_code,
                reference_id="invoice-123-buyer-7",
            )
        )

    mocked_token.assert_not_awaited()
def test_paypal_service_normalizes_lowercase_currency(monkeypatch):
    mocked_token = AsyncMock(
        return_value={"access_token": "test-token"}
    )
    monkeypatch.setattr(
        paypal_service,
        "get_paypal_us_access_token",
        mocked_token,
    )

    # Stop before making an actual HTTP request.
    import httpx

    class MockResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "PAYPAL-ORDER-LOWERCASE"}
    class MockResponseWithToken:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "access_token": "test-token",
                "expires_in": 3600,
                "token_type": "Bearer",
            }
    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            pass

        async def post(self, url, **kwargs):
            if url.endswith("/v1/oauth2/token"):
                return MockResponseWithToken()

            assert kwargs["json"]["purchase_units"][0]["amount"]["currency_code"] == "USD"
            assert kwargs["headers"]["PayPal-Request-Id"] == "buddy-test-request-123"
            return MockResponse()
    monkeypatch.setattr(
        paypal_service.httpx,
        "AsyncClient",
        lambda **kwargs: MockClient(),
    )

    result = asyncio.run(
    paypal_service.create_paypal_us_order(
        amount="25.00",
        currency_code="usd",
        reference_id="invoice-123-buyer-7",
        request_id="buddy-test-request-123",
    )
)

    assert result["id"] == "PAYPAL-ORDER-LOWERCASE"
    mocked_token.assert_awaited_once()

def test_pending_paypal_capture_marks_payment_pending(monkeypatch):
    payment = SimpleNamespace(
        id=49,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-PENDING",
        paypal_capture_id=None,
        status=PaymentStatus.APPROVED,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_capture = AsyncMock(
        return_value={
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "TEST-CAPTURE-PENDING",
                                "status": "PENDING",
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
        mocked_capture,
    )

    response = asyncio.run(
        paypal.capture_invoice_payment_order(
            order_id="TEST-ORDER-PENDING",
            current_user=current_user,
            db=db,
        )
    )

    assert response["status"] == "pending"
    assert payment.status == PaymentStatus.PENDING
    assert payment.paypal_capture_id == "TEST-CAPTURE-PENDING"
    assert db.committed
    assert db.refreshed
    mocked_capture.assert_awaited_once_with("TEST-ORDER-PENDING")



def test_pending_payment_reconciles_to_completed(monkeypatch):
    payment = SimpleNamespace(
        id=51,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-RECONCILE",
        paypal_capture_id="TEST-CAPTURE-RECONCILE",
        status=PaymentStatus.PENDING,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_retrieval = AsyncMock(
        return_value={
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "TEST-CAPTURE-RECONCILE",
                                "status": "COMPLETED",
                            }
                        ]
                    }
                }
            ],
        }
    )

    mocked_capture = AsyncMock()

    monkeypatch.setattr(
        paypal,
        "get_paypal_us_order",
        mocked_retrieval,
    )
    monkeypatch.setattr(
        paypal,
        "capture_paypal_us_order",
        mocked_capture,
    )

    response = asyncio.run(
        paypal.capture_invoice_payment_order(
            order_id="TEST-ORDER-RECONCILE",
            current_user=current_user,
            db=db,
        )
    )

    assert response["status"] == "completed"
    assert payment.status == PaymentStatus.COMPLETED
    assert payment.paypal_capture_id == "TEST-CAPTURE-RECONCILE"
    assert db.committed
    mocked_retrieval.assert_awaited_once_with("TEST-ORDER-RECONCILE")
    mocked_capture.assert_not_awaited()
def test_pending_payment_remains_pending_when_paypal_confirms_pending(monkeypatch):
    payment = SimpleNamespace(
        id=50,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-ALREADY-PENDING",
        paypal_capture_id="TEST-CAPTURE-PENDING",
        status=PaymentStatus.PENDING,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_retrieval = AsyncMock(
        return_value={
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "TEST-CAPTURE-PENDING",
                                "status": "PENDING",
                            }
                        ]
                    }
                }
            ],
        }
    )

    mocked_capture = AsyncMock()

    monkeypatch.setattr(
        paypal,
        "get_paypal_us_order",
        mocked_retrieval,
    )
    monkeypatch.setattr(
        paypal,
        "capture_paypal_us_order",
        mocked_capture,
    )

    response = asyncio.run(
        paypal.capture_invoice_payment_order(
            order_id="TEST-ORDER-ALREADY-PENDING",
            current_user=current_user,
            db=db,
        )
    )

    assert response["status"] == "pending"
    assert payment.status == PaymentStatus.PENDING
    assert payment.paypal_capture_id == "TEST-CAPTURE-PENDING"
    assert db.committed
    mocked_retrieval.assert_awaited_once_with("TEST-ORDER-ALREADY-PENDING")
    mocked_capture.assert_not_awaited()
def test_capture_error_reconciles_successful_paypal_capture(monkeypatch):
    payment = SimpleNamespace(
        id=52,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-UNCERTAIN",
        paypal_capture_id=None,
        status=PaymentStatus.APPROVED,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_capture = AsyncMock(
        side_effect=RuntimeError("Connection lost after capture")
    )

    mocked_retrieval = AsyncMock(
        return_value={
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "TEST-CAPTURE-UNCERTAIN",
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
        mocked_capture,
    )
    monkeypatch.setattr(
        paypal,
        "get_paypal_us_order",
        mocked_retrieval,
    )

    response = asyncio.run(
        paypal.capture_invoice_payment_order(
            order_id="TEST-ORDER-UNCERTAIN",
            current_user=current_user,
            db=db,
        )
    )

    assert response["status"] == "completed"
    assert payment.status == PaymentStatus.COMPLETED
    assert payment.paypal_capture_id == "TEST-CAPTURE-UNCERTAIN"
    assert db.committed
    mocked_capture.assert_awaited_once_with("TEST-ORDER-UNCERTAIN")
    mocked_retrieval.assert_awaited_once_with("TEST-ORDER-UNCERTAIN")
def test_capturing_payment_reconciles_to_completed(monkeypatch):
    payment = SimpleNamespace(
        id=53,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-CAPTURING",
        paypal_capture_id=None,
        status=PaymentStatus.CAPTURING,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_retrieval = AsyncMock(
        return_value={
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "TEST-CAPTURE-CAPTURING",
                                "status": "COMPLETED",
                            }
                        ]
                    }
                }
            ],
        }
    )

    mocked_capture = AsyncMock()

    monkeypatch.setattr(
        paypal,
        "get_paypal_us_order",
        mocked_retrieval,
    )
    monkeypatch.setattr(
        paypal,
        "capture_paypal_us_order",
        mocked_capture,
    )

    response = asyncio.run(
        paypal.capture_invoice_payment_order(
            order_id="TEST-ORDER-CAPTURING",
            current_user=current_user,
            db=db,
        )
    )

    assert response["status"] == "completed"
    assert payment.status == PaymentStatus.COMPLETED
    assert payment.paypal_capture_id == "TEST-CAPTURE-CAPTURING"
    assert db.committed
    assert db.refreshed

    mocked_retrieval.assert_awaited_once_with(
        "TEST-ORDER-CAPTURING"
    )
    mocked_capture.assert_not_awaited()
def test_capturing_payment_reconciles_to_pending(monkeypatch):
    payment = SimpleNamespace(
        id=54,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-CAPTURING-PENDING",
        paypal_capture_id=None,
        status=PaymentStatus.CAPTURING,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_retrieval = AsyncMock(
        return_value={
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "TEST-CAPTURE-PENDING",
                                "status": "PENDING",
                            }
                        ]
                    }
                }
            ],
        }
    )

    mocked_capture = AsyncMock()

    monkeypatch.setattr(
        paypal,
        "get_paypal_us_order",
        mocked_retrieval,
    )
    monkeypatch.setattr(
        paypal,
        "capture_paypal_us_order",
        mocked_capture,
    )

    response = asyncio.run(
        paypal.capture_invoice_payment_order(
            order_id="TEST-ORDER-CAPTURING-PENDING",
            current_user=current_user,
            db=db,
        )
    )

    assert response["status"] == "pending"
    assert payment.status == PaymentStatus.PENDING
    assert payment.paypal_capture_id == "TEST-CAPTURE-PENDING"
    assert db.committed
    assert db.refreshed

    mocked_retrieval.assert_awaited_once_with(
        "TEST-ORDER-CAPTURING-PENDING"
    )
    mocked_capture.assert_not_awaited()
def test_capturing_payment_reconciles_to_failed(monkeypatch):
    payment = SimpleNamespace(
        id=55,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-CAPTURING-FAILED",
        paypal_capture_id=None,
        status=PaymentStatus.CAPTURING,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_retrieval = AsyncMock(
        return_value={
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "TEST-CAPTURE-FAILED",
                                "status": "FAILED",
                            }
                        ]
                    }
                }
            ],
        }
    )

    mocked_capture = AsyncMock()

    monkeypatch.setattr(
        paypal,
        "get_paypal_us_order",
        mocked_retrieval,
    )
    monkeypatch.setattr(
        paypal,
        "capture_paypal_us_order",
        mocked_capture,
    )

    response = asyncio.run(
        paypal.capture_invoice_payment_order(
            order_id="TEST-ORDER-CAPTURING-FAILED",
            current_user=current_user,
            db=db,
        )
    )

    assert response["status"] == "failed"
    assert payment.status == PaymentStatus.FAILED
    assert payment.paypal_capture_id == "TEST-CAPTURE-FAILED"
    assert db.committed
    assert db.refreshed

    mocked_retrieval.assert_awaited_once_with(
        "TEST-ORDER-CAPTURING-FAILED"
    )
    mocked_capture.assert_not_awaited()
def test_capturing_payment_remains_capturing_when_paypal_unavailable(
    monkeypatch,
):
    payment = SimpleNamespace(
        id=56,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-CAPTURING-UNKNOWN",
        paypal_capture_id=None,
        status=PaymentStatus.CAPTURING,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    mocked_retrieval = AsyncMock(
        side_effect=RuntimeError("PayPal unavailable")
    )
    mocked_capture = AsyncMock()

    monkeypatch.setattr(
        paypal,
        "get_paypal_us_order",
        mocked_retrieval,
    )
    monkeypatch.setattr(
        paypal,
        "capture_paypal_us_order",
        mocked_capture,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.capture_invoice_payment_order(
                order_id="TEST-ORDER-CAPTURING-UNKNOWN",
                current_user=current_user,
                db=db,
            )
        )

    assert exc_info.value.status_code == 502
    assert payment.status == PaymentStatus.CAPTURING
    assert not db.committed

    mocked_retrieval.assert_awaited_once_with(
        "TEST-ORDER-CAPTURING-UNKNOWN"
    )
    mocked_capture.assert_not_awaited()
def test_capture_is_not_requested_when_initial_commit_fails(monkeypatch):
    payment = SimpleNamespace(
        id=57,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-DB-FAILURE",
        paypal_capture_id=None,
        status=PaymentStatus.APPROVED,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    def failing_commit():
        raise RuntimeError("Database unavailable")

    db.commit = failing_commit

    mocked_capture = AsyncMock()

    monkeypatch.setattr(
        paypal,
        "capture_paypal_us_order",
        mocked_capture,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.capture_invoice_payment_order(
                order_id="TEST-ORDER-DB-FAILURE",
                current_user=current_user,
                db=db,
            )
        )

    assert exc_info.value.status_code == 503
    assert payment.status == PaymentStatus.CAPTURING
    assert db.rolled_back
    mocked_capture.assert_not_awaited()
def test_successful_paypal_capture_handles_final_db_commit_failure(
    monkeypatch,
):
    payment = SimpleNamespace(
        id=58,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-FINAL-DB-FAILURE",
        paypal_capture_id=None,
        status=PaymentStatus.APPROVED,
    )
    db = FakeDB(payment)
    current_user = SimpleNamespace(id=7)

    commit_count = 0

    def fail_second_commit():
        nonlocal commit_count
        commit_count += 1

        if commit_count == 2:
            raise RuntimeError("Database commit failed")

        db.committed = True

    db.commit = fail_second_commit
    def restore_after_rollback():
        db.rolled_back = True
        payment.status = PaymentStatus.CAPTURING
        payment.paypal_capture_id = None

    db.rollback = restore_after_rollback
    mocked_capture = AsyncMock(
        return_value={
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": "TEST-CAPTURE-FINAL-DB-FAILURE",
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
        mocked_capture,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.capture_invoice_payment_order(
                order_id="TEST-ORDER-FINAL-DB-FAILURE",
                current_user=current_user,
                db=db,
            )
        )

    assert exc_info.value.status_code == 503
    assert commit_count == 2
    assert db.rolled_back
    assert payment.status == PaymentStatus.CAPTURING

    mocked_capture.assert_awaited_once_with(
        "TEST-ORDER-FINAL-DB-FAILURE"
    )
def test_capture_rejects_when_another_request_claimed_payment(
    monkeypatch,
):
    payment = SimpleNamespace(
        id=59,
        buyer_id=7,
        paypal_order_id="TEST-ORDER-CLAIM-LOST",
        paypal_capture_id=None,
        status=PaymentStatus.APPROVED,
    )

    db = FakeDB(payment)

    # Simulate another request winning the atomic database update.
    db.claim_rowcount_override = 0

    current_user = SimpleNamespace(id=7)

    mocked_capture = AsyncMock()
    monkeypatch.setattr(
        paypal,
        "capture_paypal_us_order",
        mocked_capture,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.capture_invoice_payment_order(
                order_id="TEST-ORDER-CLAIM-LOST",
                current_user=current_user,
                db=db,
            )
        )

    assert exc_info.value.status_code == 409
    assert "already in progress" in exc_info.value.detail
    assert db.rolled_back
    assert not db.committed

    # The losing request must never contact PayPal.
    mocked_capture.assert_not_awaited()

def test_create_invoice_order_recovers_expired_initializing_payment(monkeypatch):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    payment = SimpleNamespace(
        id=501,
        invoice_id=123,
        buyer_id=7,
        provider_id=9,
        paypal_order_id=None,
        amount=Decimal("25.00"),
        currency="USD",
        status=PaymentStatus.INITIALIZING,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=3),
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=3),
    )

    invoice = SimpleNamespace(
        id=123,
        buyer_id=7,
        provider_id=9,
        total_amount=Decimal("25.00"),
        currency="USD",
    )

    db = FakeDB(payment)
    db.get = lambda model, invoice_id: invoice

    create_order_mock = AsyncMock(
        return_value={
            "id": "PAYPAL-RECOVERED-501",
            "links": [
                {
                    "rel": "approve",
                    "href": "https://sandbox.paypal.com/approve/501",
                }
            ],
        }
    )
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order_mock)

    result = asyncio.run(
        paypal.create_invoice_payment_order(
            invoice_id=123,
            current_user=SimpleNamespace(id=7),
            db=db,
        )
    )

    expected_request_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "buddypay-payment-501",
        )
    )

    assert result["paypal_order_id"] == "PAYPAL-RECOVERED-501"
    assert result["status"] == PaymentStatus.CREATED
    assert payment.id == 501
    assert payment.status == PaymentStatus.CREATED
    assert payment.paypal_order_id == "PAYPAL-RECOVERED-501"

    create_order_mock.assert_awaited_once_with(
        amount="25.00",
        currency_code="USD",
        reference_id="invoice-123-buyer-7",
        request_id=expected_request_id,
    )


def test_create_invoice_order_rejects_fresh_initializing_payment(monkeypatch):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    payment = SimpleNamespace(
        id=502,
        status=PaymentStatus.INITIALIZING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    invoice = SimpleNamespace(
        id=123,
        buyer_id=7,
        provider_id=9,
        total_amount=Decimal("25.00"),
        currency="USD",
    )

    db = FakeDB(payment)
    db.get = lambda model, invoice_id: invoice

    create_order_mock = AsyncMock()
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order_mock)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.create_invoice_payment_order(
                invoice_id=123,
                current_user=SimpleNamespace(id=7),
                db=db,
            )
        )

    assert exc_info.value.status_code == 409
    create_order_mock.assert_not_awaited()

def test_create_invoice_order_rejects_reservation_older_than_five_hours(
    monkeypatch,
):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    old_time = datetime.now(timezone.utc) - timedelta(hours=6)

    payment = SimpleNamespace(
        id=503,
        invoice_id=123,
        buyer_id=7,
        provider_id=9,
        paypal_order_id=None,
        amount=Decimal("25.00"),
        currency="USD",
        status=PaymentStatus.INITIALIZING,
        created_at=old_time,
        updated_at=old_time,
    )

    invoice = SimpleNamespace(
        id=123,
        buyer_id=7,
        provider_id=9,
        total_amount=Decimal("25.00"),
        currency="USD",
    )

    db = FakeDB(payment)
    db.get = lambda model, invoice_id: invoice

    create_order_mock = AsyncMock()
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order_mock)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.create_invoice_payment_order(
                invoice_id=123,
                current_user=SimpleNamespace(id=7),
                db=db,
            )
        )

    assert exc_info.value.status_code == 409
    assert "exceeded its automatic recovery window" in exc_info.value.detail
    create_order_mock.assert_not_awaited()
    assert db.committed is False
def test_create_invoice_order_rejects_stale_worker_created_update(monkeypatch):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    invoice = SimpleNamespace(
        id=123,
        buyer_id=7,
        provider_id=9,
        total_amount=Decimal("25.00"),
        currency="USD",
    )

    db = FakeDB(None)
    db.get = lambda model, invoice_id: invoice

    # Simulate another worker taking ownership before this update.
    db.reservation_update_rowcount_override = 0

    newer_lease = datetime.now(timezone.utc) + timedelta(seconds=1)

    def create_order_after_recovery(**kwargs):
        # Simulate another worker acquiring the reservation.
        db.added_payment.updated_at = newer_lease

        return {
            "id": "PAYPAL-STALE-ORDER",
            "links": [
                {
                    "rel": "approve",
                    "href": "https://sandbox.paypal.com/approve/PAYPAL-STALE-ORDER",
                }
            ],
        }

    create_order_mock = AsyncMock(
        side_effect=create_order_after_recovery
    )
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order_mock)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.create_invoice_payment_order(
                invoice_id=123,
                current_user=SimpleNamespace(id=7),
                db=db,
            )
        )

    assert exc_info.value.status_code == 409
    assert "ownership changed" in exc_info.value.detail.lower()

    # The stale worker must not overwrite the reservation.
    assert db.added_payment is not None
    assert db.added_payment.status == PaymentStatus.INITIALIZING
    assert db.added_payment.paypal_order_id is None

    assert db.rolled_back
    create_order_mock.assert_awaited_once()
    assert db.added_payment.updated_at == newer_lease
def test_create_invoice_order_rejects_stale_worker_failed_update(monkeypatch):
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_ID", "test-client")
    monkeypatch.setattr(paypal.settings, "PAYPAL_US_CLIENT_SECRET", "test-secret")

    invoice = SimpleNamespace(
        id=123,
        buyer_id=7,
        provider_id=9,
        total_amount=Decimal("25.00"),
        currency="USD",
    )

    db = FakeDB(None)
    db.get = lambda model, invoice_id: invoice

    

    newer_lease = datetime.now(timezone.utc) + timedelta(seconds=1)

    def fail_after_recovery(**kwargs):
        # Simulate another worker acquiring the reservation.
        db.added_payment.updated_at = newer_lease

        raise ValueError("Invalid PayPal order request")

    create_order_mock = AsyncMock(
        side_effect=fail_after_recovery
    )
    
    monkeypatch.setattr(paypal, "create_paypal_us_order", create_order_mock)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            paypal.create_invoice_payment_order(
                invoice_id=123,
                current_user=SimpleNamespace(id=7),
                db=db,
            )
        )

    assert exc_info.value.status_code == 409
    assert "ownership changed" in exc_info.value.detail.lower()

    # The stale worker must not mark the reservation FAILED.
    assert db.added_payment is not None
    assert db.added_payment.status == PaymentStatus.INITIALIZING
    assert db.added_payment.paypal_order_id is None

    assert db.rolled_back
    create_order_mock.assert_awaited_once()
    assert db.added_payment.updated_at == newer_lease