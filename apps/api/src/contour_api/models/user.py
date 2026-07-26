"""User and organisation models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamped, UUIDPrimaryKey


class Organisation(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "organisation"

    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    members: Mapped[list[User]] = relationship(back_populates="organisation")


class User(Base, UUIDPrimaryKey, Timestamped):
    """An account.

    ``password_hash`` holds an Argon2id hash; no reversible credential is ever
    stored. ``deletion_requested_at`` drives the full-deletion path required by
    §20.4 — deletion is a recorded request rather than an immediate destructive
    write, so it can be audited and completed asynchronously across routes,
    imports, exports and object storage.
    """

    __tablename__ = "app_user"
    __table_args__ = (
        UniqueConstraint("email_normalised", name="uq_app_user_email"),
        Index("ix_app_user_organisation", "organisation_id"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    # Case-folded copy used for lookup and uniqueness; the display form is kept
    # as the user typed it.
    email_normalised: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str | None] = mapped_column(Text)

    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organisation.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_operator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organisation: Mapped[Organisation | None] = relationship(back_populates="members")
    connected_accounts: Mapped[list[ConnectedAccount]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ConnectedAccount(Base, UUIDPrimaryKey, Timestamped):
    """An external platform account the user authorised (§15.8, §20.2-20.4).

    Tokens are encrypted at rest with ``CONTOUR_TOKEN_ENCRYPTION_KEY`` and are
    never logged. Only scopes the user actually granted are recorded, and
    disconnection revokes upstream before the row is removed.
    """

    __tablename__ = "connected_account"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_connected_account_provider"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    external_user_id: Mapped[str | None] = mapped_column(String(255))
    access_token_encrypted: Mapped[bytes | None] = mapped_column()
    refresh_token_encrypted: Mapped[bytes | None] = mapped_column()
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_scopes: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="connected_accounts")
