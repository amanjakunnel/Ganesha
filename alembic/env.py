from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

from packages.core.domain.models import Base

# allow importing project modules from repo root
sys.path.insert(0, os.path.abspath('.'))

config = context.config

# Preserve an explicitly supplied Alembic URL (for tests and tooling).
# Otherwise, derive the normal local development URL from central settings.
configured_url = config.get_main_option("sqlalchemy.url")

if not configured_url or configured_url.startswith("driver://"):
    from packages.core.settings import settings as app_settings

    url_obj = app_settings.sqlalchemy_url()
    config.set_main_option(
        "sqlalchemy.url",
        url_obj.render_as_string(hide_password=False),
    )

# Setup logging from config file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}

    connectable = engine_from_config(
        configuration,
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        dialect_name = connection.dialect.name
        render_batch = dialect_name == 'sqlite'

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=render_batch,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
