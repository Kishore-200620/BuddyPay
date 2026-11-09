import asyncio
import io
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.main import app
from app.core.database import get_db
from app.models.invoice import Invoice, InvoiceStatus
from app.models.user import User, UserRole


class FakeDB:
    def __init__(self):
        self.added = None
        self.committed = False
        self.refreshed = False
        self.rolled_back = False

    def add(self, obj):
        self.added = obj

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        obj.id = 123
        obj.created_at = datetime(2026, 1, 1)

    def rollback(self):
        self.rolled_back = True


@pytest.fixture
def buyer():
    return User(id=7, role=UserRole.BUYER, is_active=True)


@pytest.fixture
def client(buyer):
    db = FakeDB()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: buyer

    with TestClient(app) as test_client:
        yield test_client, db

    app.dependency_overrides.clear()


def test_upload_invoice_success(client, buyer):
    test_client, db = client

    extracted_invoice = Invoice(
        id=123,
        buyer_id=buyer.id,
        provider_id=None,
        invoice_number="INV-001",
        seller_name="Example Seller",
        seller_email="seller@example.com",
        invoice_date=None,
        due_date=None,
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("10.00"),
        total_amount=Decimal("110.00"),
        currency="USD",
        status=InvoiceStatus.PROCESSED,
        created_at=datetime(2026, 1, 1),
    )

    with (
        patch("app.api.routes.invoices.validate_pdf", return_value=b"%PDF-1.7 test"),
        patch(
            "app.api.routes.invoices.create_invoice_upload",
            return_value=extracted_invoice,
        ) as create_mock,
        patch(
            "app.api.routes.invoices.process_invoice_extraction",
            return_value=extracted_invoice,
        ) as process_mock,
    ):
        response = test_client.post(
            "/api/v1/invoices/upload",
            files={"file": ("invoice.pdf", b"%PDF-1.7 test", "application/pdf")},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] == 123
    assert body["invoice_number"] == "INV-001"
    assert body["status"] == "processed"
    create_mock.assert_called_once_with(
        db=db,
        buyer=buyer,
        file_content=b"%PDF-1.7 test",
    )
    process_mock.assert_called_once_with(db=db, invoice=extracted_invoice)


def test_upload_invoice_rejects_invalid_pdf(client):
    test_client, _ = client

    with patch(
        "app.api.routes.invoices.validate_pdf",
        side_effect=HTTPException(status_code=400, detail="Invalid PDF file"),
    ):
        response = test_client.post(
            "/api/v1/invoices/upload",
            files={"file": ("invoice.pdf", b"not a pdf", "application/pdf")},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid PDF file"


def test_upload_invoice_extraction_failure_returns_failed_invoice(client, buyer):
    test_client, _ = client

    failed_invoice = Invoice(
        id=124,
        buyer_id=buyer.id,
        provider_id=None,
        invoice_number=None,
        seller_name=None,
        seller_email=None,
        invoice_date=None,
        due_date=None,
        subtotal=None,
        tax_amount=None,
        total_amount=None,
        currency="USD",
        status=InvoiceStatus.FAILED,
        created_at=datetime(2026, 1, 1),
    )

    with (
        patch("app.api.routes.invoices.validate_pdf", return_value=b"%PDF-1.7 test"),
        patch(
            "app.api.routes.invoices.create_invoice_upload",
            return_value=failed_invoice,
        ),
        patch(
            "app.api.routes.invoices.process_invoice_extraction",
            return_value=failed_invoice,
        ),
    ):
        response = test_client.post(
            "/api/v1/invoices/upload",
            files={"file": ("invoice.pdf", b"%PDF-1.7 test", "application/pdf")},
        )

    assert response.status_code == 201
    assert response.json()["status"] == "failed"


def test_upload_invoice_closes_file_when_validation_fails(client):
    test_client, _ = client

    with patch(
        "app.api.routes.invoices.validate_pdf",
        side_effect=HTTPException(status_code=400, detail="Invalid PDF file"),
    ):
        response = test_client.post(
            "/api/v1/invoices/upload",
            files={"file": ("invoice.pdf", b"invalid", "application/pdf")},
        )

    assert response.status_code == 400


def make_invoice(invoice_id, buyer_id, invoice_number, created_at):
    return Invoice(
        id=invoice_id,
        buyer_id=buyer_id,
        provider_id=None,
        invoice_number=invoice_number,
        seller_name="Example Seller",
        seller_email="seller@example.com",
        invoice_date=None,
        due_date=None,
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("10.00"),
        total_amount=Decimal("110.00"),
        currency="USD",
        status=InvoiceStatus.PROCESSED,
        created_at=created_at,
        ai_summary=f"Summary for {invoice_number}",
    )


def configure_query(db, invoices, total):
    query = MagicMock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.count.return_value = total
    query.all.return_value = invoices
    query.first.return_value = invoices[0] if invoices else None
    db.query = MagicMock(return_value=query)
    return query


def test_get_invoice_history_returns_paginated_buyer_invoices(client, buyer):
    test_client, db = client
    invoices = [
        make_invoice(2, buyer.id, "INV-002", datetime(2026, 1, 2)),
        make_invoice(1, buyer.id, "INV-001", datetime(2026, 1, 1)),
    ]
    query = configure_query(db, invoices, total=2)

    response = test_client.get("/api/v1/invoices/?page=1&page_size=10")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["page"] == 1
    assert body["page_size"] == 10
    assert body["total_pages"] == 1
    assert [item["invoice_number"] for item in body["items"]] == [
        "INV-002",
        "INV-001",
    ]
    assert query.offset.call_args.args == (0,)
    assert query.limit.call_args.args == (10,)


def test_get_invoice_history_returns_empty_page_when_no_invoices(client):
    test_client, db = client
    configure_query(db, [], total=0)

    response = test_client.get("/api/v1/invoices/")

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 10,
        "total_pages": 0,
    }


def test_get_invoice_detail_returns_invoice_and_summary(client, buyer):
    test_client, db = client
    invoice = make_invoice(123, buyer.id, "INV-123", datetime(2026, 1, 1))
    configure_query(db, [invoice], total=1)

    response = test_client.get("/api/v1/invoices/123")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 123
    assert body["invoice_number"] == "INV-123"
    assert body["ai_summary"] == "Summary for INV-123"


def test_get_invoice_detail_returns_404_for_missing_or_other_buyer_invoice(client):
    test_client, db = client
    configure_query(db, [], total=0)

    response = test_client.get("/api/v1/invoices/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Invoice not found"
def test_get_invoice_detail_returns_404_for_another_buyers_invoice(client, buyer):
    test_client, db = client

    other_buyer_invoice = make_invoice(
        456,
        buyer_id=999,
        invoice_number="INV-OTHER",
        created_at=datetime(2026, 1, 1),
    )
    configure_query(db, [], total=0)

    response = test_client.get("/api/v1/invoices/456")

    assert response.status_code == 404
    assert response.json()["detail"] == "Invoice not found"


def test_get_invoice_history_only_returns_current_buyers_invoices(client, buyer):
    test_client, db = client

    own_invoice = make_invoice(
        123,
        buyer_id=buyer.id,
        invoice_number="INV-OWN",
        created_at=datetime(2026, 1, 1),
    )
    configure_query(db, [own_invoice], total=1)

    response = test_client.get("/api/v1/invoices/")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [item["invoice_number"] for item in body["items"]] == ["INV-OWN"]
@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
    ],
)
def test_get_invoice_history_rejects_invalid_pagination(client, params):
    test_client, _ = client

    response = test_client.get("/api/v1/invoices/", params=params)

    assert response.status_code == 422