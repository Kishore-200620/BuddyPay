
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.user import User, UserRole


class FakeQuery:
    def __init__(self, result=None):
        self.result = result
        self.filters = []

    def filter(self, *conditions):
        self.filters.extend(conditions)
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.result


class FakeDB:
    def __init__(self, invoice=None, provider=None, payment=None):
        self.invoice = invoice
        self.provider = provider
        self.payment = payment
        self.committed = False
        self.rolled_back = False
        self.refreshed = False

    def query(self, model):
        if model is Invoice:
            return FakeQuery(self.invoice)

        if model is User:
            candidate = self.provider

            if candidate is None:
                return FakeQuery(None)

            invoice_email = (
                self.invoice.seller_email.strip().lower()
                if self.invoice and self.invoice.seller_email
                else ""
            )

            candidate_email = (
                candidate.email.strip().lower()
                if candidate.email
                else ""
            )

            eligible = (
                candidate.is_active is True
                and candidate.role == UserRole.PROVIDER
                and candidate_email == invoice_email
            )

            return FakeQuery(candidate if eligible else None)

        if model is Payment or model is Payment.id:
            return FakeQuery(self.payment)

        raise AssertionError(f"Unexpected model query: {model}")

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        self.refreshed = True

    def rollback(self):
        self.rolled_back = True


@pytest.fixture
def buyer():
    return User(
        id=7,
        full_name="Test Buyer",
        email="buyer@example.com",
        role=UserRole.BUYER,
        is_active=True,
    )


@pytest.fixture
def provider():
    return User(
        id=12,
        full_name="Test Provider",
        email="seller@example.com",
        role=UserRole.PROVIDER,
        is_active=True,
    )


def make_invoice(buyer_id=7, provider_id=None):
    return Invoice(
        id=123,
        buyer_id=buyer_id,
        provider_id=provider_id,
        invoice_number="INV-123",
        seller_name="Test Seller",
        seller_email=" SELLER@example.com ",
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("10.00"),
        total_amount=Decimal("110.00"),
        currency="USD",
        pdf_file_path="test.pdf",
        status=InvoiceStatus.PROCESSED,
        created_at=datetime(2026, 1, 1),
    )


@pytest.fixture
def api_client(buyer):
    state = {
        "db": FakeDB(invoice=make_invoice()),
        "user": buyer,
    }

    app.dependency_overrides[get_db] = lambda: state["db"]
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    with TestClient(app) as client:
        yield client, state

    app.dependency_overrides.clear()


def test_provider_match_finds_exact_email(api_client, provider):
    client, state = api_client
    state["db"].provider = provider

    response = client.get("/api/v1/invoices/123/provider-match")

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["provider"]["id"] == provider.id
    assert body["provider"]["email"] == provider.email


def test_provider_match_returns_no_match(api_client):
    client, _ = api_client

    response = client.get("/api/v1/invoices/123/provider-match")

    assert response.status_code == 200
    assert response.json()["matched"] is False
    assert response.json()["provider"] is None


@pytest.mark.parametrize(
    "candidate",
    [
        User(
            id=20,
            full_name="Inactive Provider",
            email="seller@example.com",
            role=UserRole.PROVIDER,
            is_active=False,
        ),
        User(
            id=21,
            full_name="Buyer Account",
            email="seller@example.com",
            role=UserRole.BUYER,
            is_active=True,
        ),
    ],
)
def test_provider_match_rejects_inactive_or_wrong_role(
    api_client,
    candidate,
):
    client, state = api_client
    state["db"].provider = candidate

    response = client.get("/api/v1/invoices/123/provider-match")

    assert response.status_code == 200
    assert response.json()["matched"] is False
    assert response.json()["provider"] is None

    response = client.get("/api/v1/invoices/123/provider-match")

    assert response.status_code == 200
    assert response.json()["matched"] is False


def test_provider_match_hides_another_buyers_invoice(api_client):
    client, state = api_client
    state["db"].invoice = None

    response = client.get("/api/v1/invoices/123/provider-match")

    assert response.status_code == 404


def test_confirmation_assigns_provider(api_client, provider):
    client, state = api_client
    state["db"].provider = provider

    response = client.post(
        "/api/v1/invoices/123/provider-match/confirm"
    )

    assert response.status_code == 200
    body = response.json()

    assert body["invoice_id"] == 123
    assert body["provider_id"] == provider.id
    assert body["provider_email"] == provider.email

    assert state["db"].invoice.provider_id == provider.id
    assert state["db"].committed is True
    assert state["db"].refreshed is True


def test_confirmation_rejects_existing_payment_history(
    api_client,
    provider,
):
    client, state = api_client
    state["db"].provider = provider
    state["db"].payment = SimpleNamespace(id=55)

    response = client.post(
        "/api/v1/invoices/123/provider-match/confirm"
    )

    assert response.status_code == 409
    assert state["db"].invoice.provider_id is None
    assert state["db"].committed is False


def test_confirmation_rejects_already_assigned_provider(
    api_client,
    provider,
):
    client, state = api_client
    state["db"].invoice.provider_id = provider.id

    response = client.post(
        "/api/v1/invoices/123/provider-match/confirm"
    )

    assert response.status_code == 409
    assert state["db"].committed is False
