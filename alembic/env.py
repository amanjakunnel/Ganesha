from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# allow importing project modules from repo root
sys.path.insert(0, os.path.abspath('.'))

config = context.config

# Load settings from the central settings module instead of raw DATABASE_URL
try:
    # Importing settings may read .env via pydantic-settings; this centralizes logic
    from packages.core.settings import settings as app_settings  # type: ignore

    url_obj = app_settings.sqlalchemy_url()
    # Set the SQLAlchemy URL for alembic using the constructed URL object
    config.set_main_option('sqlalchemy.url', url_obj.render_as_string(hide_password=False))
except Exception:
    # Fall back to previous behavior: load .env if available and infer a safe default
    POSTGRES_PASSWORD = os.environ.get('POSTGRES_PASSWORD', os.environ.get('PGPASSWORD', 'change_me'))
    DEFAULT_DB_URL = f"postgresql+psycopg://job_agent:{POSTGRES_PASSWORD}@localhost:5432/job_agent"

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    db_url = config.get_main_option('sqlalchemy.url') or DEFAULT_DB_URL
    config.set_main_option('sqlalchemy.url', db_url)

# Setup logging from config file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Attempt to import project's metadata for autogenerate support.
# If not present, leave target_metadata as None which is safe for an empty initial migration.
target_metadata = None
try:
    # common location for SQLAlchemy Base/metadata
    from packages.core.domain import models as domain_models  # type: ignore

    if hasattr(domain_models, 'Base'):
        target_metadata = getattr(domain_models, 'Base').metadata
    elif hasattr(domain_models, 'metadata'):
        target_metadata = getattr(domain_models, 'metadata')
except Exception:
    try:
        # fallback to packages.core.domain package exposing Base
        from packages.core.domain import __init__ as domain_pkg  # type: ignore

        if hasattr(domain_pkg, 'Base'):
            target_metadata = getattr(domain_pkg, 'Base').metadata
        elif hasattr(domain_pkg, 'metadata'):
            target_metadata = getattr(domain_pkg, 'metadata')
    except Exception:
        target_metadata = None


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
