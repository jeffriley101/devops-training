from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Float,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


SEASON_TIMEZONE = "America/Chicago"


class WoodchuckProfile(Base):
    __tablename__ = "woodchuck_profiles"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'deleted')", name="ck_woodchuck_profile_status"
        ),
    )

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
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", nullable=False,
        index=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    retired_woodchuck_id_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    session_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    deletion_failed_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    deletion_last_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    display_name_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    level_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

class OwnedItemCopy(Base):
    __tablename__ = "owned_item_copies"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "acquisition_key",
            name="uq_owned_item_copy_profile_acquisition",
        ),
        CheckConstraint(
            "acquisition_source IN ('store', 'mum')",
            name="ck_owned_item_copy_source",
        ),
        CheckConstraint(
            "(acquisition_source = 'store' AND purchase_price > 0) OR "
            "(acquisition_source = 'mum' AND purchase_price IS NULL)",
            name="ck_owned_item_copy_price_source",
        ),
        CheckConstraint(
            "(placement_x IS NULL AND placement_y IS NULL) OR "
            "(placement_x IS NOT NULL AND placement_y IS NOT NULL)",
            name="ck_owned_item_copy_placement_pair",
        ),
        CheckConstraint(
            "placement_x IS NULL OR (placement_x >= 0 AND placement_x <= 1)",
            name="ck_owned_item_copy_placement_x",
        ),
        CheckConstraint(
            "placement_y IS NULL OR (placement_y >= 0 AND placement_y <= 1)",
            name="ck_owned_item_copy_placement_y",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    acquisition_source: Mapped[str] = mapped_column(String(20), nullable=False)
    acquisition_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    purchase_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    placement_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    placement_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class CrownAward(Base):
    __tablename__ = "crown_awards"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "source_key",
            name="uq_crown_award_profile_source",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    source_key: Mapped[str] = mapped_column(String(150), nullable=False)
    earned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class RewardInventoryPlacement(Base):
    __tablename__ = "reward_inventory_placements"
    __table_args__ = (
        UniqueConstraint(
            "crown_award_id",
            name="uq_reward_inventory_placement_crown_award",
        ),
        CheckConstraint(
            "placement_x >= 0 AND placement_x <= 1",
            name="ck_reward_inventory_placement_x",
        ),
        CheckConstraint(
            "placement_y >= 0 AND placement_y <= 1",
            name="ck_reward_inventory_placement_y",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    crown_award_id: Mapped[int] = mapped_column(
        ForeignKey("crown_awards.id", ondelete="CASCADE"),
        nullable=False,
    )
    placement_x: Mapped[float] = mapped_column(Float, nullable=False)
    placement_y: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
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

    last_email_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_email_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)

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
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "submission_key",
            name="uq_practice_chart_profile_submission",
        ),
    )

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

    submission_key: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    include_contests: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )

    include_team_contests: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )

    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ordinary_email_preset_id: Mapped[int | None] = mapped_column(
        ForeignKey("practice_email_presets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ordinary_email_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ordinary_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ordinary_email_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)

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

    last_email_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_email_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class PracticeEmailPreset(Base):
    __tablename__ = "practice_email_presets"
    __table_args__ = (
        UniqueConstraint("profile_id", "email", name="uq_practice_email_preset_profile_email"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class CampPointAward(Base):
    __tablename__ = "camp_point_awards"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "duplicate_key",
            name="uq_camp_point_award_profile_key",
        ),
        CheckConstraint("points_awarded > 0", name="ck_camp_point_award_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    points_awarded: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    duplicate_key: Mapped[str] = mapped_column(String(100), nullable=False)
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class PlungePointAward(Base):
    __tablename__ = "plunge_point_awards"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "event_key",
            name="uq_plunge_point_award_profile_event",
        ),
        CheckConstraint(
            "event_type IN ('dandelion', 'carrot', 'instrument', 'band_complete')",
            name="ck_plunge_point_award_event_type",
        ),
        CheckConstraint(
            "points_scored > 0",
            name="ck_plunge_point_award_points_positive",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    points_scored: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class DailyTriviaAttempt(Base):
    __tablename__ = "daily_trivia_attempts"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "activity_date",
            name="uq_daily_trivia_attempt_profile_date",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activity_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    selected_answer: Mapped[str] = mapped_column(String(200), nullable=False)
    correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class QuestCompletion(Base):
    __tablename__ = "quest_completions"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "activity_date",
            name="uq_quest_completion_profile_date",
        ),
        CheckConstraint("logged_minutes > 0", name="ck_quest_completion_minutes_positive"),
        CheckConstraint("reward_amount > 0", name="ck_quest_completion_reward_positive"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activity_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    quest_id: Mapped[str] = mapped_column(String(100), nullable=False)
    logged_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class Season(Base):
    __tablename__ = "seasons"
    __table_args__ = (
        CheckConstraint(
            "status IN ('planned', 'active', 'closed')",
            name="ck_season_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64),
        default=SEASON_TIMEZONE,
        server_default=SEASON_TIMEZONE,
        nullable=False,
    )
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("season_id", "normalized_name", name="uq_team_season_name"),
        UniqueConstraint("season_id", "emblem_key", name="uq_team_season_emblem"),
        UniqueConstraint("season_id", "creator_profile_id", name="uq_team_season_creator"),
        CheckConstraint(
            "moderation_status IN ('active', 'under_review', 'hidden')",
            name="ck_team_moderation_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(30), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    emblem_key: Mapped[str] = mapped_column(String(50), nullable=False)
    creator_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    moderation_status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", nullable=False,
        index=True,
    )
    moderation_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class TeamReport(Base):
    __tablename__ = "team_reports"
    __table_args__ = (
        CheckConstraint(
            "category IN ('inappropriate_name', 'inappropriate_emblem', "
            "'impersonation', 'other')",
            name="ck_team_report_category",
        ),
        CheckConstraint(
            "status IN ('unresolved', 'dismissed', 'actioned')",
            name="ck_team_report_status",
        ),
        Index(
            "uq_team_report_unresolved_reporter_team",
            "reporter_profile_id", "team_id", unique=True,
            sqlite_where=text("status = 'unresolved'"),
            postgresql_where=text("status = 'unresolved'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reporter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    details: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="unresolved", server_default="unresolved",
        nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class TeamMembership(Base):
    __tablename__ = "team_memberships"
    __table_args__ = (
        Index(
            "uq_team_membership_active_profile_season", "profile_id", "season_id",
            unique=True, sqlite_where=text("ended_at IS NULL"),
            postgresql_where=text("ended_at IS NULL"),
        ),
        Index("ix_team_membership_season_team", "season_id", "team_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    selected_week_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class TeamWeekMembershipSnapshot(Base):
    __tablename__ = "team_week_membership_snapshots"
    __table_args__ = (
        UniqueConstraint("contest_week_id", "profile_id", name="uq_team_week_snapshot_profile"),
        Index("ix_team_week_snapshot_week_team", "contest_week_id", "team_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contest_week_id: Mapped[int] = mapped_column(ForeignKey("contest_weeks.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True)
    membership_id: Mapped[int | None] = mapped_column(ForeignKey("team_memberships.id", ondelete="SET NULL"), nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class Contest(Base):
    __tablename__ = "contests"
    __table_args__ = (
        CheckConstraint(
            "metric_type IN ('practice_minutes', 'points')",
            name="ck_contest_metric_type",
        ),
        CheckConstraint(
            "subject_type IN ('student', 'instrument', 'team')",
            name="ck_contest_subject_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    crown_category: Mapped[str | None] = mapped_column(
        String(100), index=True, nullable=True
    )
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
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
        CheckConstraint(
            "status IN ('open', 'pending', 'finalized')",
            name="ck_contest_week_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_id: Mapped[int] = mapped_column(
        ForeignKey("seasons.id", ondelete="CASCADE"), index=True, nullable=False
    )
    week_start: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    week_end: Mapped[date] = mapped_column(Date, nullable=False)
    verification_deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finalize_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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
        CheckConstraint(
            "division IN ('open', 'verified')",
            name="ck_contest_result_division",
        ),
        CheckConstraint(
            "subject_type IN ('student', 'instrument', 'team')",
            name="ck_contest_result_subject_type",
        ),
        CheckConstraint(
            "medal IN ('gold', 'silver', 'bronze')",
            name="ck_contest_result_medal",
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
    subject_type: Mapped[str] = mapped_column(String(30), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    instrument: Mapped[str | None] = mapped_column(String(50), nullable=True)
    team_id: Mapped[int | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), index=True, nullable=True
    )
    active_member_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    display_name_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    medal: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
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
    contest_result_id: Mapped[int | None] = mapped_column(
        ForeignKey("contest_results.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    source_key: Mapped[str] = mapped_column(String(150), index=True, nullable=False)
    reward_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    category_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
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
    qualifying_wins: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    crown_earned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
