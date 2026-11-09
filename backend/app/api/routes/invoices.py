import math
from typing import Annotated
from app.models.invoice import Invoice, InvoiceStatus
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session
from app.schemas.invoice import (
    InvoiceDetailResponse,
    InvoiceHistoryResponse,
    InvoiceResponse,
    ProviderInvoiceCreate,
)
from app.api.dependencies import require_role
from app.core.database import get_db
from app.core.file_storage import validate_pdf
from app.models.invoice import Invoice
from app.models.user import User, UserRole
from app.schemas.invoice import (
    InvoiceDetailResponse,
    InvoiceHistoryResponse,
    InvoiceResponse,
)
from app.services.invoice_service import (
    create_invoice_upload,
    process_invoice_extraction,
)

from app.schemas.invoice import InvoiceDetailResponse
router = APIRouter(
    prefix="/invoices",
    tags=["Invoices"]
)


@router.post(
    "/upload",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_invoice(
    file: Annotated[
        UploadFile,
        File(description="Invoice PDF (max 10 MB)")
    ],
    current_user: Annotated[
        User,
        Depends(require_role(UserRole.BUYER))
    ],
    db: Session = Depends(get_db),
):
    try:
        file_content = await validate_pdf(file)

        invoice = create_invoice_upload(
            db=db,
            buyer=current_user,
            file_content=file_content,
        )

        invoice = process_invoice_extraction(
            db=db,
            invoice=invoice,
        )

        return invoice

    finally:
        await file.close()

@router.get(
    "/",
    response_model=InvoiceHistoryResponse,
    status_code=status.HTTP_200_OK,
)
def get_invoice_history(
    current_user: Annotated[
        User,
        Depends(require_role(UserRole.BUYER))
    ],
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
):
    # Count only invoices belonging to the authenticated buyer.
    total = (
        db.query(Invoice)
        .filter(Invoice.buyer_id == current_user.id)
        .count()
    )

    # Retrieve the requested page, newest invoices first.
    invoices = (
        db.query(Invoice)
        .filter(Invoice.buyer_id == current_user.id)
        .order_by(
            Invoice.created_at.desc(),
            Invoice.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    total_pages = math.ceil(total / page_size) if total else 0

    return {
        "items": invoices,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }
@router.get(
    "/{invoice_id}",
    response_model=InvoiceDetailResponse,
    status_code=status.HTTP_200_OK,
)
def get_invoice_detail(
    invoice_id: int,
    current_user: Annotated[
        User,
        Depends(require_role(UserRole.BUYER))
    ],
    db: Session = Depends(get_db),
):
    invoice = (
        db.query(Invoice)
        .filter(
            Invoice.id == invoice_id,
            Invoice.buyer_id == current_user.id,
        )
        .first()
    )

    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    return invoice
@router.post(
    "/provider",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_provider_invoice(
    invoice_data: ProviderInvoiceCreate,
    current_user: Annotated[
        User,
        Depends(require_role(UserRole.PROVIDER))
    ],
    db: Session = Depends(get_db),
):
    buyer = (
        db.query(User)
        .filter(
            User.email == str(invoice_data.buyer_email).lower(),
            User.role == UserRole.BUYER,
            User.is_active.is_(True),
        )
        .first()
    )

    if buyer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active buyer not found.",
        )

    invoice = Invoice(
        buyer_id=buyer.id,
        provider_id=current_user.id,
        seller_name=current_user.full_name,
        seller_email=current_user.email,
        invoice_number=invoice_data.invoice_number.strip(),
        subtotal=invoice_data.total_amount,
        tax_amount=0,
        total_amount=invoice_data.total_amount,
        currency=invoice_data.currency.upper(),
        pdf_file_path="",
        status=InvoiceStatus.PROCESSED,
    )

    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    return invoice