"""Alembic environment.

The database URL is not in alembic.ini -- it comes from Settings, so there is
exactly one place a connection string is configured.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from job_searcher.config import settings
from job_searcher.db import models  # noqa: F401  -- registers tables on Base.metadata
from job_searcher.db.base import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _common_opts() -> dict:
    return {
        "target_metadata": target_metadata,
        # Without these two, autogenerate silently misses column type changes
        # and server-default changes -- the model and the schema drift apart
        # and nothing tells you.
        "compare_type": True,
        "compare_server_default": True,
        "include_schemas": False,
    }


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_common_opts(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, **_common_opts())
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
