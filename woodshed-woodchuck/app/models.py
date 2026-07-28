from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Date,
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


SEASON_TIMEZONE = "America/Chicago"


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


class TrustedVerifierInvitation(Base):
    __tablename__ = "trusted_verifier_invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    profile_id: Mapped[int] = mapped_column(
        ForeignKey(
            "woodchuck_profiles.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        index=True,
    )

    accepted_verifier_id: Mapped[int | None] = mapped_column(
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

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
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


class PracticeChart(Base):
    __tablename__ = "practice_charts"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    profile_id: Mapped[int] = mapped_column(
        ForeignKey(
            "woodchuck_profiles.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    practice_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    instrument: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    note: Mapped[str | None] = mapped_column(
        String(180),
        nullable=True,
    )

    practice_details: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        nullable=False,
    )

    source: Mapped[str] = mapped_column(
        String(30),
        default="p-book",
        nullable=False,
    )

    credits_awarded: Mapped[int] = mapped_column(
        Integer,
        default=0,
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


class PracticeChartVerification(Base):
    __tablename__ = "practice_chart_verifications"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    practice_chart_id: Mapped[int] = mapped_column(
        ForeignKey(
            "practice_charts.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    verifier_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "trusted_verifiers.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
        index=True,
    )

    response_note: Mapped[str | None] = mapped_column(
        String(300),
        nullable=True,
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    timezone: Mapped[str] = mapped_column(
        String(64), default=SEASON_TIMEZONE, nullable=False
    )
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Contest(Base):
    __tablename__ = "contests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    category_key: Mapped[str | None] = mapped_column(
        String(100), index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ContestWeek(Base):
    __tablename__ = "contest_weeks"
    __table_args__ = (
        UniqueConstraint(
            "season_id",
            "week_start",
            name="uq_contest_week_season_start",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"), index=True, nullable=False
    )
    week_start: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ContestResult(Base):
    __tablename__ = "contest_results"
    __table_args__ = (
        UniqueConstraint(
            "contest_week_id",
            "contest_id",
            "division",
            "subject_key",
            name="uq_contest_result_week_contest_division_subject",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contest_week_id: Mapped[int] = mapped_column(
        ForeignKey("contest_weeks.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    contest_id: Mapped[int] = mapped_column(
        ForeignKey("contests.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    division: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    subject_key: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    winner_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class RewardGrant(Base):
    __tablename__ = "reward_grants"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "source_key",
            "reward_type",
            name="uq_reward_grant_profile_source_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(String(150), index=True, nullable=False)
    reward_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    amount: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class CrownProgress(Base):
    __tablename__ = "crown_progress"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "category_key",
            name="uq_crown_progress_profile_category",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    category_key: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    wins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    earned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
