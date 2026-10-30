from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    verify_password,
    create_access_token
)
from app.schemas.auth import TokenResponse
from app.services.user_service import get_user_by_email


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)


@router.post(
    "/login",
    response_model=TokenResponse
)
def login(
    form_data: Annotated[
        OAuth2PasswordRequestForm,
        Depends()
    ],
    db: Session = Depends(get_db)
):

    user = get_user_by_email(
        db,
        form_data.username
    )

    if (
        not user
        or not user.hashed_password
        or not verify_password(
            form_data.password,
            user.hashed_password
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"}
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive"
        )

    token = create_access_token(user.id)

    return TokenResponse(
        access_token=token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )