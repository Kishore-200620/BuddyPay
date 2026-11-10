
import sys
from pathlib import Path
from logging.config import fileConfig

from alembic import context

# Add the backend directory to Python's import path.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# Import the existing database configuration.
from app.core.database import Base, engine
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
import os

migration_url = os.getenv("ALEMBIC_DATABASE_URL")

if migration_url:
    engine = create_engine(
        make_url(migration_url),
        pool_pre_ping=True,
    )
# Import models so SQLAlchemy registers their tables.
from app.models import user, invoice, payment, paypal_webhook_event


config = context.config

# Configure logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Make existing SQLAlchemy models available to Alembic.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without creating a database connection."""

    context.configure(
        url=engine.url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations using the existing SQLAlchemy engine."""

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
