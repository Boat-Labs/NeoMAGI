import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import create_engine, pool, text

from alembic import context

# Load .env so DATABASE_* vars are available
load_dotenv()

# Alembic Config object
config = context.config

# Python logging from ini file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Build DB URL from environment
DB_HOST = os.getenv("DATABASE_HOST", "localhost")
DB_PORT = os.getenv("DATABASE_PORT", "5432")
DB_USER = os.getenv("DATABASE_USER", "postgres")
DB_PASSWORD = os.getenv("DATABASE_PASSWORD", "")
DB_NAME = os.getenv("DATABASE_NAME", "neomagi")
from src.constants import DB_SCHEMA  # noqa: E402

DATABASE_URL = f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Import metadata for autogenerate
from src.session.models import Base  # noqa: E402

target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    """Only include objects from the neomagi schema."""
    if type_ == "schema":
        return name == DB_SCHEMA
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=DB_SCHEMA,
        include_schemas=True,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        # Ensure schema exists
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=DB_SCHEMA,
            include_schemas=True,
            include_name=include_name,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
