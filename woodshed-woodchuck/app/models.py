from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WoodchuckProfile(Base):
    __tablename__ = "woodchuck_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Public account identifier used to sign in on another device.
    woodchuck_id: Mapped[str] = mapped_column(
        String(16),
        unique=True,
        index=True,
        nullable=False,
    )

    display_name: Mapped[str] = mapped_column(String(50), nullable=False)
    pin_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    instrument: Mapped[str] = mapped_column(String(50), nullable=False)
    level: Mapped[str] = mapped_column(String(50), nullable=False)
    goal: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )



class WoodchuckState(Base):
    __tablename__ = "woodchuck_states"

    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id"),
        primary_key=True,
    )

    state_json: Mapped[dict[str, object]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )

    revision: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

class TrustedVerifier(Base):
    __tablename__ = "trusted_verifiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        index=True,
        nullable=False,
    )

    display_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    pin_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class StudentVerifierConnection(Base):
    __tablename__ = "student_verifier_connections"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "verifier_id",
            name="uq_student_verifier_connection",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    profile_id: Mapped[int] = mapped_column(
        ForeignKey(
            "woodchuck_profiles.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    verifier_id: Mapped[int] = mapped_column(
        ForeignKey(
            "trusted_verifiers.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
    )

    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    organization_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    created_by_verifier_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "trusted_verifiers.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class StudentOrganizationMembership(Base):
    __tablename__ = "student_organization_memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "profile_id",
            name="uq_student_organization_membership",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    organization_id: Mapped[int] = mapped_column(
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    profile_id: Mapped[int] = mapped_column(
        ForeignKey(
            "woodchuck_profiles.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        nullable=False,
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class VerifierOrganizationMembership(Base):
    __tablename__ = "verifier_organization_memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "verifier_id",
            name="uq_verifier_organization_membership",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    organization_id: Mapped[int] = mapped_column(
        ForeignKey(
            "organizations.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    verifier_id: Mapped[int] = mapped_column(
        ForeignKey(
            "trusted_verifiers.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(50),
        default="member",
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        nullable=False,
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

