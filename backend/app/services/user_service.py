from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.user import UserCreate
from app.core.security import hash_password


def create_user(db: Session, user_data: UserCreate) -> User:

    existing_user = db.scalar(
        select(User).where(
            User.email == str(user_data.email)
        )
    )

    if existing_user:
        raise ValueError("Email is already registered")

    new_user = User(
        full_name=user_data.full_name,
        email=str(user_data.email),
        role=user_data.role,
        hashed_password=hash_password(user_data.password)
    )

    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return new_user

    except IntegrityError:
        db.rollback()
        raise ValueError("Email is already registered")


def get_user_by_id(db: Session, user_id: int):
    return db.get(User, user_id)


def get_user_by_email(db: Session, email: str):
    return db.scalar(
        select(User).where(User.email == email)
    )