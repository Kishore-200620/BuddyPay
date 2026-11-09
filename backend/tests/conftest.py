
import uuid

import pytest

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base

# Import all models before create_all so their tables and relationships
# are registered in Base.metadata.
from app.models.user import User
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.paypal_webhook_event import PayPalWebhookEvent


TEST_DATABASE_NAME = "buddypay_test_db"


@pytest.fixture(scope="session")
def test_engine():
    """Create an engine that can ONLY target the dedicated test database."""

    database_url = make_url(settings.DATABASE_URL)

    if not database_url.drivername.startswith("postgresql"):
        pytest.fail("PostgreSQL is required for webhook integration tests.")

    if database_url.database != TEST_DATABASE_NAME:
        database_url = database_url.set(database=TEST_DATABASE_NAME)

    if database_url.database != TEST_DATABASE_NAME:
        pytest.fail("Unsafe integration-test database configuration.")

    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        echo=False,
    )

    # Create missing tables without dropping or recreating existing tables.
    Base.metadata.create_all(bind=engine)

    yield engine

    engine.dispose()


@pytest.fixture
def db_session(test_engine):
    """Provide a real PostgreSQL session for one test."""

    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    db = TestingSessionLocal()

    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def payment_context(db_session):
    """
    Create an isolated buyer, provider and invoice.

    Payment is created separately in tests so unmatched webhook
    scenarios can create it later.
    """

    unique_id = uuid.uuid4().hex

    buyer = User(
        full_name="Webhook Integration Buyer",
        email=f"webhook-integration-buyer-{unique_id}@example.test",
        role="buyer",
        is_active=True,
    )

    provider = User(
        full_name="Webhook Integration Provider",
        email=f"webhook-integration-provider-{unique_id}@example.test",
        role="provider",
        is_active=True,
    )

    db_session.add_all([buyer, provider])
    db_session.flush()

    invoice = Invoice(
        invoice_number=f"INV-INT-{unique_id}",
        buyer_id=buyer.id,
        provider_id=provider.id,
        seller_name="Integration Test Provider",
        seller_email=provider.email,
        subtotal="100.00",
        tax_amount="0.00",
        total_amount="100.00",
        currency="USD",
        pdf_file_path="integration-tests/test-invoice.pdf",
        status="processed",
    )

    db_session.add(invoice)
    db_session.commit()

    context = {
        "buyer_id": buyer.id,
        "provider_id": provider.id,
        "invoice_id": invoice.id,
        "unique_id": unique_id,
    }

    yield context

    # Remove only records created by this integration-test fixture.
    db_session.query(PayPalWebhookEvent).filter(
        PayPalWebhookEvent.event_id.like("WH-INT-%")
    ).delete(synchronize_session=False)

    db_session.query(Payment).filter(
    Payment.invoice_id == context["invoice_id"]
    ).delete(synchronize_session=False)

    db_session.query(Invoice).filter(
        Invoice.id == context["invoice_id"]
    ).delete(synchronize_session=False)

    db_session.query(User).filter(
        User.id.in_([
            context["buyer_id"],
            context["provider_id"],
        ])
    ).delete(synchronize_session=False)

    db_session.commit()
