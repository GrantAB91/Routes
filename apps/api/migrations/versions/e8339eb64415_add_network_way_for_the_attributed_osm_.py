"""Add network_way for the attributed OSM cycling network

Holds the imported network itself, as distinct from route_segment, which holds
pieces of particular routes. Valhalla returns OSM way identifiers alongside its
geometry, and those join to network_way.osm_way_id, which is how a generated
route acquires surface, access and road class from Contour's own import rather
than from whatever the engine happened to report.

Every enum here already exists — all five are shared with segment_attribute —
so the columns declare create_type=False. Left to itself, create_table emits a
CREATE TYPE per enum column and the migration fails on the first one.

Revision ID: e8339eb64415
Revises: 47a3e7324555
Create Date: 2026-07-27 06:36:47.090164
"""
from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'e8339eb64415'
down_revision: str | None = '47a3e7324555'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def _knowledge_status() -> postgresql.ENUM:
    return _existing_enum("knowledge_status", "KNOWN", "UNKNOWN", "NOT_APPLICABLE", "CONFLICTING")


def upgrade() -> None:
    op.create_table(
        'network_way',
        sa.Column('osm_way_id', sa.BigInteger(), nullable=False),
        sa.Column('source_id', sa.UUID(), nullable=True),
        sa.Column('import_id', sa.UUID(), nullable=True),
        sa.Column(
            'geom',
            geoalchemy2.types.Geometry(
                geometry_type='LINESTRING',
                srid=4326,
                dimension=2,
                from_text='ST_GeomFromEWKT',
                name='geometry',
                nullable=False,
            ),
            nullable=False,
        ),
        sa.Column('length_m', sa.Float(), nullable=True),
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('surface_tag_raw', sa.String(length=80), nullable=True),
        sa.Column(
            'surface_family',
            _existing_enum('surface_family', 'PAVED', 'UNPAVED', 'UNKNOWN'),
            nullable=False,
        ),
        sa.Column('surface_status', _knowledge_status(), nullable=False),
        sa.Column('smoothness_tag_raw', sa.String(length=80), nullable=True),
        sa.Column('tracktype_tag_raw', sa.String(length=40), nullable=True),
        sa.Column(
            'bicycle_access',
            _existing_enum(
                'bicycle_access',
                'YES',
                'NO',
                'DESIGNATED',
                'PERMISSIVE',
                'DESTINATION',
                'PRIVATE',
                'CUSTOMERS',
                'DISMOUNT',
                'UNKNOWN',
            ),
            nullable=False,
        ),
        sa.Column('bicycle_access_status', _knowledge_status(), nullable=False),
        sa.Column('bicycle_access_raw', sa.String(length=120), nullable=True),
        sa.Column('oneway_bicycle', sa.Boolean(), nullable=True),
        sa.Column('oneway_status', _knowledge_status(), nullable=False),
        sa.Column('access_conditional_raw', sa.String(length=255), nullable=True),
        sa.Column('seasonal_raw', sa.String(length=120), nullable=True),
        sa.Column(
            'cycle_lane',
            _existing_enum('cycle_lane_kind', 'ABSENT', 'SHARED', 'DEDICATED', 'SEPARATED'),
            nullable=True,
        ),
        sa.Column('cycle_lane_status', _knowledge_status(), nullable=False),
        sa.Column('cycle_lane_raw', sa.String(length=120), nullable=True),
        sa.Column(
            'road_class',
            _existing_enum(
                'road_class',
                'MOTORWAY',
                'TRUNK',
                'PRIMARY',
                'SECONDARY',
                'TERTIARY',
                'UNCLASSIFIED',
                'RESIDENTIAL',
                'SERVICE',
                'LIVING_STREET',
                'TRACK',
                'PATH',
                'CYCLEWAY',
                'FOOTWAY',
                'BRIDLEWAY',
                'STEPS',
                'FERRY',
                'UNKNOWN',
            ),
            nullable=False,
        ),
        sa.Column('road_class_status', _knowledge_status(), nullable=False),
        sa.Column('speed_limit_kph', sa.Integer(), nullable=True),
        sa.Column('speed_limit_status', _knowledge_status(), nullable=False),
        sa.Column('width_m', sa.Float(), nullable=True),
        sa.Column('lit', sa.Boolean(), nullable=True),
        sa.Column('shoulder', sa.Boolean(), nullable=True),
        sa.Column('is_bridge', sa.Boolean(), nullable=True),
        sa.Column('is_tunnel', sa.Boolean(), nullable=True),
        sa.Column('is_ferry', sa.Boolean(), nullable=False),
        sa.Column('is_ford', sa.Boolean(), nullable=True),
        sa.Column('has_steps', sa.Boolean(), nullable=True),
        sa.Column('barrier_raw', sa.String(length=80), nullable=True),
        sa.Column('construction_raw', sa.String(length=80), nullable=True),
        sa.Column('network_refs', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('source_date', sa.Date(), nullable=True),
        sa.Column('last_import_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attribute_completeness', sa.Float(), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['import_id'],
            ['source_import.id'],
            name=op.f('fk_network_way_import_id_source_import'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['source_id'],
            ['route_source.id'],
            name=op.f('fk_network_way_source_id_route_source'),
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_network_way')),
        sa.UniqueConstraint('osm_way_id', 'import_id', name='uq_network_way_import'),
    )
    op.create_index('ix_network_way_osm_id', 'network_way', ['osm_way_id'], unique=False)
    op.create_index('ix_network_way_road_class', 'network_way', ['road_class'], unique=False)
    # Postgres does not index a foreign key automatically, and replacing an
    # import deletes several hundred thousand rows by import_id.
    op.create_index('ix_network_way_import', 'network_way', ['import_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_network_way_import', table_name='network_way')
    op.drop_index('ix_network_way_road_class', table_name='network_way')
    op.drop_index('ix_network_way_osm_id', table_name='network_way')
    op.drop_table('network_way')
    # The five enum types are deliberately left in place: every one of them is
    # also used by segment_attribute, so dropping any would break that table.
