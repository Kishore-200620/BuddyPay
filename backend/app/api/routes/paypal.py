from decimal import Decimal
from typing import Annotated
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, status
from app.models.paypal_webhook_event import PayPalWebhookEvent
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
import uuid
from app.core.config import settings
from app.core.database import get_db
from app.api.dependencies import require_role
from app.models.invoice import Invoice
from app.models.payment import Payment, PaymentStatus
from app.models.user import User, UserRole
from app.services.paypal_service import (
    create_paypal_us_order,
    get_paypal_us_order,
    capture_paypal_us_order,
    verify_paypal_us_webhook_signature,
)

router = APIRouter(
    prefix="/paypal",
    tags=["PayPal"],
)












@router.post(
    "/invoices/{invoice_id}/orders",
    status_code=status.HTTP_201_CREATED,
)

async def create_invoice_payment_order(
    invoice_id: int,
    current_user: Annotated[
        User, Depends(require_role(UserRole.BUYER))
    ],
    db: Session = Depends(get_db),
):
    if not settings.PAYPAL_US_CLIENT_ID or not settings.PAYPAL_US_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="US PayPal Sandbox credentials are not configured.",
        )

    invoice = (
    db.query(Invoice)
    .filter(Invoice.id == invoice_id)
    .with_for_update()
    .first()
)

    if invoice is None or invoice.buyer_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found.",
        )

    if invoice.provider_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invoice does not have an assigned provider.",
        )

    if invoice.total_amount is None or invoice.total_amount <= Decimal("0"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invoice must have a valid positive total amount.",
        )

    existing_payment = (
        db.query(Payment)
        .filter(
            Payment.invoice_id == invoice.id,
            Payment.buyer_id == current_user.id,
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
        .first()
    )

    if existing_payment is not None:
        if existing_payment.status != PaymentStatus.INITIALIZING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An active payment attempt already exists for this invoice.",
            )

        
        # Recover only within the conservative PayPal idempotency window.
        now = datetime.now(timezone.utc)
        lease_cutoff = now - timedelta(minutes=2)
        recovery_cutoff = now - timedelta(hours=5)

        # Older reservations require reconciliation, not another create-order call.
        if existing_payment.created_at < recovery_cutoff:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This payment reservation has exceeded its automatic "
                    "recovery window. Manual payment reconciliation is required."
                ),
            )

        try:
            
            reservation_lease = db.execute(
                update(Payment)
                .where(
                    Payment.id == existing_payment.id,
                    Payment.status == PaymentStatus.INITIALIZING,
                    Payment.updated_at <= lease_cutoff,
                    Payment.created_at >= recovery_cutoff,
                )
                .values(updated_at=now)
                .returning(Payment.updated_at)
            ).scalar_one_or_none()

            db.commit()


        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not claim the existing payment reservation.",
            ) from exc

        if reservation_lease is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "A payment order is already being created for this invoice. "
                    "Please retry later."
                ),
            )

        payment = existing_payment
    else:
        completed_payment = (
            db.query(Payment)
            .filter(
                Payment.invoice_id == invoice.id,
                Payment.buyer_id == current_user.id,
                Payment.status == PaymentStatus.COMPLETED,
            )
            .first()
        )

        if completed_payment is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This invoice has already been paid.",
            )

        amount = Decimal(invoice.total_amount).quantize(
            Decimal("0.01")
        )
        currency = invoice.currency.upper()

        # Reserve the invoice BEFORE contacting PayPal.
        payment = Payment(
            invoice_id=invoice.id,
            buyer_id=current_user.id,
            provider_id=invoice.provider_id,
            paypal_order_id=None,
            amount=amount,
            currency=currency,
            status=PaymentStatus.INITIALIZING,
        )

        try:
            db.add(payment)
            db.commit()
            db.refresh(payment)

            # Capture the initial reservation's ownership token.
            reservation_lease = payment.updated_at

        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Another payment attempt is already being created for this invoice.",
            ) from exc

        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not reserve the invoice for payment.",
            ) from exc

    # Derive a stable 36-character PayPal idempotency key.
    # Every retry of this reservation uses exactly the same key.
    request_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"buddypay-payment-{payment.id}",
        )
    )

    try:
        order = await create_paypal_us_order(
            amount=f"{payment.amount:.2f}",
            currency_code=payment.currency,
            reference_id=f"invoice-{invoice.id}-buyer-{current_user.id}",
            request_id=request_id,
        )

    
    except ValueError as exc:
        # Only the current reservation owner may mark it FAILED.
        try:
            result = db.execute(
                update(Payment)
                .where(
                    Payment.id == payment.id,
                    Payment.status == PaymentStatus.INITIALIZING,
                    Payment.updated_at == reservation_lease,
                )
                .values(
                    status=PaymentStatus.FAILED,
                    updated_at=datetime.now(timezone.utc),
                )
                .execution_options(synchronize_session=False)
            )

            if result.rowcount != 1:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Payment reservation ownership changed. "
                        "The stale request cannot modify its status."
                    ),
                )

            db.commit()

        except HTTPException:
            raise
        except Exception as db_exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not safely update the payment reservation.",
            ) from db_exc

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


    except RuntimeError as exc:
        # The outcome may be ambiguous. Keep INITIALIZING so a retry
        # reuses the same PayPal idempotency key.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="PayPal order creation could not be confirmed. Retry this invoice payment to recover the existing attempt.",
        ) from exc

    order_id = order.get("id")

    if not order_id:
        # Do not release an uncertain reservation.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="PayPal did not return an order ID. The payment reservation has been retained.",
        )

    
    # Persist the PayPal order only if this worker still owns the reservation.
    try:
        result = db.execute(
            update(Payment)
            .where(
                Payment.id == payment.id,
                Payment.status == PaymentStatus.INITIALIZING,
                Payment.updated_at == reservation_lease,
            )
            .values(
                paypal_order_id=order_id,
                status=PaymentStatus.CREATED,
                updated_at=datetime.now(timezone.utc),
            )
            .execution_options(synchronize_session=False)
        )

        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Payment reservation ownership changed. "
                    "The stale request cannot save its PayPal order."
                ),
            )

        db.commit()
        db.refresh(payment)

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "PayPal order was created, but its local status could not "
                "be confirmed. Retry to recover the existing attempt."
            ),
        ) from exc


    approval_url = next(
        (
            link.get("href")
            for link in order.get("links", [])
            if link.get("rel") in ("payer-action", "approve")
        ),
        None,
    )

    return {
        "payment_id": payment.id,
        "invoice_id": payment.invoice_id,
        "paypal_order_id": payment.paypal_order_id,
        "amount": str(payment.amount),
        "currency": payment.currency,
        "status": payment.status.value,
        "approval_url": approval_url,
    }

@router.post("/invoices/orders/{order_id}/capture")
async def capture_invoice_payment_order(
    order_id: str,
    current_user: Annotated[
        User, Depends(require_role(UserRole.BUYER))
    ],
    db: Session = Depends(get_db),
):
    payment = (
        db.query(Payment)
        .filter(
            Payment.paypal_order_id == order_id,
            Payment.buyer_id == current_user.id,
        )
        .first()
    )

    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found.",
        )

    # Idempotent return for completed payments.
    if payment.status == PaymentStatus.COMPLETED:
        return {
            "payment_id": payment.id,
            "paypal_order_id": payment.paypal_order_id,
            "paypal_capture_id": payment.paypal_capture_id,
            "status": payment.status.value,
        }
        # Pending payments must not be captured again.
       # Reconcile pending payments with PayPal without capturing again.
        # Reconcile interrupted capture attempts with PayPal.
    if payment.status == PaymentStatus.CAPTURING:
        try:
            paypal_order = await get_paypal_us_order(order_id)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Payment capture is still being reconciled. "
                    "Could not verify the PayPal status. "
                    "Do not retry the capture."
                ),
            ) from exc

        purchase_units = paypal_order.get("purchase_units") or []
        payments_data = (
            purchase_units[0].get("payments") or {}
            if purchase_units
            else {}
        )
        captures = payments_data.get("captures") or []
        capture = captures[0] if captures else {}

        capture_id = capture.get("id")
        capture_status = capture.get("status")

        if not capture_id or capture_status not in (
            "COMPLETED",
            "PENDING",
            "DECLINED",
            "FAILED",
        ):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "PayPal has not confirmed the capture outcome. "
                    "Payment remains in reconciliation. "
                    "Do not retry the capture."
                ),
            )

        payment.paypal_capture_id = capture_id

        if capture_status == "COMPLETED":
            payment.status = PaymentStatus.COMPLETED
        elif capture_status == "PENDING":
            payment.status = PaymentStatus.PENDING
        else:
            payment.status = PaymentStatus.FAILED

        try:
            db.commit()
            db.refresh(payment)
        except Exception as db_error:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "PayPal status was retrieved, but the local payment "
                    "record could not be updated. Payment requires reconciliation."
                ),
            ) from db_error

        return {
            "payment_id": payment.id,
            "paypal_order_id": payment.paypal_order_id,
            "paypal_capture_id": payment.paypal_capture_id,
            "status": payment.status.value,
            "paypal_status": paypal_order.get("status"),
            "message": "Interrupted capture reconciled with PayPal.",
        }
    if payment.status == PaymentStatus.PENDING:
        try:
            paypal_order = await get_paypal_us_order(order_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not verify PayPal payment status.",
            ) from exc

        purchase_units = paypal_order.get("purchase_units") or []
        payments_data = (
            purchase_units[0].get("payments") or {}
            if purchase_units
            else {}
        )
        captures = payments_data.get("captures") or []
        capture = captures[0] if captures else {}

        capture_id = capture.get("id")
        capture_status = capture.get("status")

        if capture_id and capture_status in ("COMPLETED", "PENDING", "DECLINED", "FAILED"):
            payment.paypal_capture_id = capture_id

            if capture_status == "COMPLETED":
                payment.status = PaymentStatus.COMPLETED
            elif capture_status == "PENDING":
                payment.status = PaymentStatus.PENDING
            else:
                payment.status = PaymentStatus.FAILED

            try:
                db.commit()
                db.refresh(payment)
            except Exception as exc:
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        "PayPal status was retrieved, but the local payment "
                        "record could not be updated. Do not retry the capture."
                    ),
                ) from exc

        return {
            "payment_id": payment.id,
            "paypal_order_id": payment.paypal_order_id,
            "paypal_capture_id": payment.paypal_capture_id,
            "status": payment.status.value,
            "paypal_status": paypal_order.get("status"),
            "message": (
                "Payment status was checked with PayPal."
                if capture_status
                else "PayPal has not returned a confirmed capture status."
            ),
        }
    # Terminal states must not be captured again.
    if payment.status in (PaymentStatus.FAILED, PaymentStatus.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payment is {payment.status.value} and cannot be captured.",
        )

    if payment.status not in (PaymentStatus.CREATED, PaymentStatus.APPROVED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Payment is not in a capturable state.",
        )
        # Atomically claim the payment before contacting PayPal.
    # Only one concurrent request can change an eligible payment
    # to CAPTURING.
    try:
        claim_result = db.execute(
            update(Payment)
            .where(
                Payment.id == payment.id,
                Payment.buyer_id == current_user.id,
                Payment.status.in_(
                    [
                        PaymentStatus.CREATED,
                        PaymentStatus.APPROVED,
                    ]
                ),
            )
            .values(status=PaymentStatus.CAPTURING)
            .execution_options(synchronize_session=False)
        )

        if claim_result.rowcount != 1:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Payment capture is already in progress "
                    "or the payment state has changed. "
                    "Retry to check its status."
                ),
            )

        # Commit the capture claim before contacting PayPal.
        db.commit()
        db.refresh(payment)

    except HTTPException:
        raise

    except Exception as db_error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Could not record the capture attempt. "
                "PayPal capture was not requested."
            ),
        ) from db_error

    
    try:
        result = await capture_paypal_us_order(order_id)
    except ValueError as exc:
        # Local validation failed before the PayPal capture request.
        # Restore APPROVED because no capture was initiated.
        payment.status = PaymentStatus.APPROVED

        try:
            db.commit()
            db.refresh(payment)
        except Exception as db_error:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "PayPal capture was not initiated, but the local "
                    "payment state could not be restored."
                ),
            ) from db_error

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except RuntimeError as capture_error:
        # The capture outcome is uncertain. Check PayPal before returning.
        try:
            paypal_order = await get_paypal_us_order(order_id)
        except (ValueError, RuntimeError) as verify_error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "PayPal capture outcome is uncertain. "
                    "Could not verify the payment status. "
                    "Do not retry the capture until reconciled."
                ),
            ) from verify_error

        purchase_units = paypal_order.get("purchase_units") or []
        payments_data = (
            purchase_units[0].get("payments") or {}
            if purchase_units
            else {}
        )
        captures = payments_data.get("captures") or []
        capture = captures[0] if captures else {}

        capture_id = capture.get("id")
        capture_status = capture.get("status")

        if not capture_id or capture_status not in (
            "COMPLETED",
            "PENDING",
            "DECLINED",
            "FAILED",
        ):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "PayPal capture outcome is uncertain. "
                    "No confirmed capture status was found. "
                    "Do not retry the capture until reconciled."
                ),
            ) from capture_error

        payment.paypal_capture_id = capture_id

        if capture_status == "COMPLETED":
            payment.status = PaymentStatus.COMPLETED
        elif capture_status == "PENDING":
            payment.status = PaymentStatus.PENDING
        else:
            payment.status = PaymentStatus.FAILED

        try:
            db.commit()
            db.refresh(payment)
        except Exception as db_error:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "PayPal capture was found, but the local payment "
                    "record could not be updated. Do not retry the capture."
                ),
            ) from db_error

        return {
            "payment_id": payment.id,
            "paypal_order_id": payment.paypal_order_id,
            "paypal_capture_id": payment.paypal_capture_id,
            "status": payment.status.value,
            "paypal_status": paypal_order.get("status"),
            "message": "Payment status reconciled with PayPal.",
        }

    purchase_units = result.get("purchase_units") or []
    payments_data = (
        purchase_units[0].get("payments") or {}
        if purchase_units
        else {}
    )
    captures = payments_data.get("captures") or []
    capture = captures[0] if captures else {}

    capture_id = capture.get("id")
    capture_status = capture.get("status")

    if capture_status == "COMPLETED" and capture_id:
        payment.paypal_capture_id = capture_id
        payment.status = PaymentStatus.COMPLETED

    elif capture_status == "PENDING" and capture_id:
        payment.paypal_capture_id = capture_id
        payment.status = PaymentStatus.PENDING

    elif capture_status in ("DECLINED", "FAILED"):
        payment.paypal_capture_id = capture_id
        payment.status = PaymentStatus.FAILED

    else:
        # Keep CAPTURING when PayPal has not confirmed a final outcome.
        return {
            "payment_id": payment.id,
            "paypal_order_id": payment.paypal_order_id,
            "paypal_capture_id": payment.paypal_capture_id,
            "status": payment.status.value,
            "paypal_status": result.get("status"),
            "message": "PayPal capture outcome requires reconciliation.",
        }

    try:
        db.commit()
        db.refresh(payment)
    except Exception as db_error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "PayPal returned a capture result, but the local payment "
                "record could not be updated. Payment remains subject "
                "to reconciliation. Do not retry the capture."
            ),
        ) from db_error

    return {
        "payment_id": payment.id,
        "paypal_order_id": payment.paypal_order_id,
        "paypal_capture_id": payment.paypal_capture_id,
        "status": payment.status.value,
        "paypal_status": result.get("status"),
    }
@router.post("/us/webhooks")
async def receive_paypal_us_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Receive and safely reconcile verified PayPal capture events."""

    # --------------------------------------------------
    # 1. Parse and validate the webhook payload
    # --------------------------------------------------

    try:
        webhook_event = await request.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid webhook JSON payload.",
        ) from exc

    if not isinstance(webhook_event, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid webhook event structure.",
        )

    event_id = webhook_event.get("id")
    event_type = webhook_event.get("event_type")
    resource = webhook_event.get("resource")

    if (
        not isinstance(event_id, str)
        or not event_id
        or len(event_id) > 100
        or not isinstance(event_type, str)
        or not event_type
        or len(event_type) > 150
        or not isinstance(resource, dict)
    ):
        raise HTTPException(
            status_code=400,
            detail="Incomplete or invalid PayPal webhook event.",
        )

    # --------------------------------------------------
    # 2. Verify PayPal signature
    # --------------------------------------------------

    try:
        verified = await verify_paypal_us_webhook_signature(
            transmission_id=request.headers.get(
                "paypal-transmission-id"
            ),
            transmission_time=request.headers.get(
                "paypal-transmission-time"
            ),
            cert_url=request.headers.get(
                "paypal-cert-url"
            ),
            auth_algo=request.headers.get(
                "paypal-auth-algo"
            ),
            transmission_sig=request.headers.get(
                "paypal-transmission-sig"
            ),
            webhook_event=webhook_event,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid PayPal transmission.",
        ) from exc

    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="PayPal signature verification unavailable.",
        ) from exc

    if not verified:
        raise HTTPException(
            status_code=400,
            detail="PayPal signature verification failed.",
        )

            # --------------------------------------------------
    # 3. Check and lock existing webhook event
    # --------------------------------------------------

    existing_event = (
        db.query(PayPalWebhookEvent)
        .filter(
            PayPalWebhookEvent.event_id == event_id
        )
        .with_for_update()
        .first()
    )

    if existing_event is not None:
        existing_status = existing_event.processing_status
        existing_error = getattr(existing_event, "last_error", None)

        # A previously unmatched event may be retried.
        is_legacy_unmatched = (
            existing_status == "IGNORED"
            and existing_error == "No matching BuddyPay payment."
        )

        retryable_statuses = {
            "RECEIVED",
            "UNMATCHED",
            "RETRYABLE",
        }

        if (
            existing_status in {"PROCESSED", "IGNORED"}
            and not is_legacy_unmatched
        ):
            return {
                "received": True,
                "duplicate": True,
                "event_id": event_id,
                "processing_status": existing_status,
            }

        if (
            existing_status not in retryable_statuses
            and not is_legacy_unmatched
        ):
            return {
                "received": True,
                "duplicate": True,
                "event_id": event_id,
                "processing_status": existing_status,
            }

    # --------------------------------------------------
    # 4. Extract resource identifiers
    # --------------------------------------------------

    resource_id = resource.get("id")

    supplementary_data = resource.get("supplementary_data") or {}

    if not isinstance(supplementary_data, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid PayPal supplementary data.",
        )

    related_ids = supplementary_data.get("related_ids") or {}

    if not isinstance(related_ids, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid PayPal related identifiers.",
        )

    order_id = related_ids.get("order_id")

    if not isinstance(resource_id, str) or not resource_id:
        raise HTTPException(
            status_code=400,
            detail="Missing PayPal resource ID.",
        )

    # Only capture events are reconciled in this implementation.
    event_status_mapping = {
        "PAYMENT.CAPTURE.COMPLETED": PaymentStatus.COMPLETED,
        "PAYMENT.CAPTURE.PENDING": PaymentStatus.PENDING,
        "PAYMENT.CAPTURE.DENIED": PaymentStatus.FAILED,
    }

    target_status = event_status_mapping.get(event_type)

    is_capture_event = event_type in event_status_mapping

    capture_id = resource_id if is_capture_event else None

    if is_capture_event and (
        not isinstance(order_id, str) or not order_id
    ):
        raise HTTPException(
            status_code=400,
            detail="Capture event is missing its PayPal order ID.",
        )


    # --------------------------------------------------
    # 5. Create or reuse the webhook event record
    # --------------------------------------------------

    if existing_event is not None:
        # Reuse the same database row for a retry.
        webhook_record = existing_event

        webhook_record.event_type = event_type
        webhook_record.resource_id = resource_id
        webhook_record.order_id = (
            order_id if isinstance(order_id, str) else None
        )
        webhook_record.capture_id = capture_id
        webhook_record.payload = webhook_event

        webhook_record.processing_attempts = (
            int(getattr(webhook_record, "processing_attempts", 0) or 0)
            + 1
        )

        webhook_record.processing_status = "RECEIVED"
        webhook_record.last_error = None
        webhook_record.processed_at = None

    else:
        webhook_record = PayPalWebhookEvent(
            event_id=event_id,
            event_type=event_type,
            resource_id=resource_id,
            order_id=order_id if isinstance(order_id, str) else None,
            capture_id=capture_id,
            payload=webhook_event,
            processing_status="RECEIVED",
            processing_attempts=1,
        )

        db.add(webhook_record)
    # --------------------------------------------------
    # 6. Common safe commit for ignored events
    # --------------------------------------------------

    def commit_ignored(reason: str):
        webhook_record.processing_status = "IGNORED"
        webhook_record.last_error = reason
        webhook_record.processed_at = datetime.now(timezone.utc)

        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="Could not persist webhook event.",
            ) from exc

        return {
            "received": True,
            "event_id": event_id,
            "processing_status": "IGNORED",
            "reason": reason,
        }
    def commit_unmatched(reason: str):
        webhook_record.processing_status = "UNMATCHED"
        webhook_record.last_error = reason
        webhook_record.processed_at = None

        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="Could not persist unmatched webhook event.",
            ) from exc

        return {
            "received": True,
            "event_id": event_id,
            "processing_status": "UNMATCHED",
            "reason": reason,
        }

    def commit_retryable(reason: str):
        webhook_record.processing_status = "RETRYABLE"
        webhook_record.last_error = reason
        webhook_record.processed_at = None

        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="Could not persist retryable webhook event.",
            ) from exc

        return {
            "received": True,
            "event_id": event_id,
            "processing_status": "RETRYABLE",
            "reason": reason,
        }
    # --------------------------------------------------
    # 7. Ignore unsupported event types safely
    # --------------------------------------------------

    if target_status is None:
        return commit_ignored("Unsupported event type.")

    # --------------------------------------------------
    # 8. Find and lock the matching payment
    # --------------------------------------------------

    payment = (
        db.query(Payment)
        .filter(
            Payment.paypal_order_id == order_id
        )
        .with_for_update()
        .first()
    )

    if payment is None:
        return commit_unmatched(
            "No matching BuddyPay payment."
        )

    # --------------------------------------------------
    # 9. Validate capture identity
    # --------------------------------------------------

    if (
        payment.paypal_capture_id is not None
        and payment.paypal_capture_id != capture_id
    ):
        return commit_ignored("PayPal capture ID mismatch.")

    # --------------------------------------------------
    # 10. Validate amount and currency
    # --------------------------------------------------

    amount_data = resource.get("amount")

    if not isinstance(amount_data, dict):
        return commit_ignored("Capture amount is missing.")

    amount_value = amount_data.get("value")
    currency_code = amount_data.get("currency_code")

    if (
        isinstance(amount_value, bool)
        or not isinstance(amount_value, (str, int, float, Decimal))
        or not isinstance(currency_code, str)
        or not currency_code
    ):
        return commit_ignored("Invalid capture amount or currency.")

    try:
        received_amount = Decimal(str(amount_value))
    except (ValueError, TypeError, ArithmeticError):
        return commit_ignored("Invalid capture amount.")

    if (
        not received_amount.is_finite()
        or received_amount <= 0
        or received_amount != payment.amount
        or currency_code.upper() != payment.currency.upper()
    ):
        return commit_ignored("Capture amount or currency mismatch.")

    # --------------------------------------------------
    # 11. Apply protected payment-state transitions
    # --------------------------------------------------

    current_status = payment.status

    if current_status == PaymentStatus.COMPLETED:
        # A completed payment must never be downgraded.
        if target_status == PaymentStatus.COMPLETED:
            webhook_record.processing_status = "PROCESSED"
        else:
            webhook_record.processing_status = "IGNORED"

    elif current_status in (
        PaymentStatus.CAPTURING,
        PaymentStatus.PENDING,
    ):
        payment.status = target_status
        payment.paypal_capture_id = capture_id

        webhook_record.processing_status = "PROCESSED"

    elif current_status == PaymentStatus.FAILED:
        # A late event cannot reverse a failed payment.
        if target_status == PaymentStatus.FAILED:
            webhook_record.processing_status = "PROCESSED"
        else:
            webhook_record.processing_status = "IGNORED"

    elif current_status in (
        PaymentStatus.INITIALIZING,
        PaymentStatus.CREATED,
        PaymentStatus.APPROVED,
    ):
        return commit_retryable(
            f"Payment is currently {current_status.value}; "
            "webhook processing must be retried."
        )

    else:
        # CANCELLED and any other unsupported terminal state
        # must not be changed by this webhook handler.
        webhook_record.processing_status = "IGNORED"
        webhook_record.last_error = (
            f"Webhook cannot update payment in state "
            f"{current_status.value}."
        )

    webhook_record.processed_at = datetime.now(timezone.utc)

    # --------------------------------------------------
    # 12. Commit event and payment atomically
    # --------------------------------------------------

    try:
        db.commit()

    except IntegrityError as exc:
        db.rollback()

        # A concurrent delivery might have inserted the
        # same unique event ID first.
        try:
            duplicate = (
                db.query(PayPalWebhookEvent)
                .filter(
                    PayPalWebhookEvent.event_id == event_id
                )
                .first()
            )
        except Exception:
            db.rollback()
            raise HTTPException(
                status_code=503,
                detail="Could not verify webhook persistence.",
            ) from exc

        if duplicate is not None:
            return {
                "received": True,
                "duplicate": True,
                "event_id": event_id,
                "processing_status": duplicate.processing_status,
            }

        raise HTTPException(
            status_code=503,
            detail="Webhook database integrity error.",
        ) from exc

    except Exception as exc:
        db.rollback()

        raise HTTPException(
            status_code=503,
            detail="Could not persist webhook processing result.",
        ) from exc

    return {
        "received": True,
        "event_id": event_id,
        "processing_status": webhook_record.processing_status,
        "payment_id": payment.id,
        "payment_status": payment.status.value,
    }