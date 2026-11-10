import math
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.dependencies import require_role
from app.core.database import get_db
from app.models.payment import Payment, PaymentStatus
from app.models.user import User, UserRole

router = APIRouter(
    prefix="/payments",
    tags=["Payments"],
)


@router.get(
    "/history",
    status_code=status.HTTP_200_OK,
)
def get_payment_history(
    current_user: Annotated[
        User,
        Depends(require_role(UserRole.BUYER, UserRole.PROVIDER)),
    ],
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
):
    # Derive ownership from the authenticated user's role and ID.
    if current_user.role == UserRole.BUYER:
        ownership_filter = Payment.buyer_id == current_user.id
    else:
        ownership_filter = Payment.provider_id == current_user.id

    base_query = db.query(Payment).filter(ownership_filter)

    total = base_query.count()

    payments = (
        base_query
        .order_by(
            Payment.created_at.desc(),
            Payment.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    total_pages = math.ceil(total / page_size) if total else 0

    return {
        "items": payments,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }

@router.get(
    "/summary",
    status_code=status.HTTP_200_OK,
)
def get_payment_summary(
    current_user: Annotated[
        User,
        Depends(require_role(UserRole.BUYER, UserRole.PROVIDER)),
    ],
    db: Session = Depends(get_db),
):
    # Determine ownership from the authenticated user's role.
    if current_user.role == UserRole.BUYER:
        ownership_filter = Payment.buyer_id == current_user.id
    else:
        ownership_filter = Payment.provider_id == current_user.id

    # These states are considered pending/in progress.
    pending_statuses = {
        PaymentStatus.INITIALIZING,
        PaymentStatus.CREATED,
        PaymentStatus.APPROVED,
        PaymentStatus.PENDING,
        PaymentStatus.CAPTURING,
    }

    # Initialize every status so the response always has consistent keys.
    status_counts = {
        payment_status.value: 0
        for payment_status in PaymentStatus
    }

    # Group payment counts and amounts by currency and status.
    grouped_results = (
        db.query(
            Payment.currency,
            Payment.status,
            func.count(Payment.id),
            func.coalesce(func.sum(Payment.amount), 0),
        )
        .filter(ownership_filter)
        .group_by(Payment.currency, Payment.status)
        .all()
    )

    currency_totals = {}

    for currency, payment_status, count, amount in grouped_results:
        status_counts[payment_status.value] += count

        if currency not in currency_totals:
            currency_totals[currency] = {
                "currency": currency,
                "completed_amount": Decimal("0.00"),
                "pending_amount": Decimal("0.00"),
            }

        if payment_status == PaymentStatus.COMPLETED:
            currency_totals[currency]["completed_amount"] += amount

        elif payment_status in pending_statuses:
            currency_totals[currency]["pending_amount"] += amount

    total_payments = sum(status_counts.values())

    pending_count = sum(
        status_counts[payment_status.value]
        for payment_status in pending_statuses
    )

    completed_count = status_counts[PaymentStatus.COMPLETED.value]
    failed_count = status_counts[PaymentStatus.FAILED.value]
    cancelled_count = status_counts[PaymentStatus.CANCELLED.value]

    # Return monetary amounts as strings to preserve decimal precision.
    currencies = [
        {
            "currency": currency,
            "completed_amount": str(values["completed_amount"]),
            "pending_amount": str(values["pending_amount"]),
        }
        for currency, values in sorted(currency_totals.items())
    ]

    return {
        "total_payments": total_payments,
        "completed_count": completed_count,
        "pending_count": pending_count,
        "failed_count": failed_count,
        "cancelled_count": cancelled_count,
        "status_counts": status_counts,
        "currency_totals": currencies,
    }
@router.get(
    "/{payment_id}",
    status_code=status.HTTP_200_OK,
)
def get_payment_detail(
    payment_id: int,
    current_user: Annotated[
        User,
        Depends(require_role(UserRole.BUYER, UserRole.PROVIDER)),
    ],
    db: Session = Depends(get_db),
):
    # Enforce ownership based on the authenticated user's role.
    if current_user.role == UserRole.BUYER:
        ownership_filter = Payment.buyer_id == current_user.id
    else:
        ownership_filter = Payment.provider_id == current_user.id

    payment = (
        db.query(Payment)
        .filter(
            Payment.id == payment_id,
            ownership_filter,
        )
        .first()
    )

    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found",
        )

    return payment