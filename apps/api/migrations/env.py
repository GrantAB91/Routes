"""Alembic environment.

Two Contour-specific behaviours live here:

* the URL always comes from ``CONTOUR_DATABASE_URL`` so migrations cannot be
  pointed at a different database from the application;
* PostGIS's own managed objects are excluded from autogenerate, otherwise every
  revision tries to drop ``spatial_ref_sys`` and the indexes GeoAlchemy2 creates
  for geometry columns.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from contour_api.config import get_settings
from contour_api.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", str(get_settings().database_url))

target_metadata = Base.metadata

# Tables PostGIS manages itself.
POSTGIS_TABLES = {"spatial_ref_sys", "geography_columns", "geometry_columns", "raster_columns"}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    if type_ == "table" and name in POSTGIS_TABLES:
        return False
    # GeoAlchemy2 creates a spatial index for every geometry column as a side
    # effect of the column type itself. Letting autogenerate emit them as well
    # produces a duplicate CREATE INDEX and the migration fails half-applied.
    # GeoAlchemy2 names them "idx_<table>_<column>"; Contour's own indexes use
    # the "ix_" prefix from NAMING_CONVENTION, so the prefix separates them.
    if type_ == "index" and name is not None and name.startswith("idx_"):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=include_object,
        compare_type=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
