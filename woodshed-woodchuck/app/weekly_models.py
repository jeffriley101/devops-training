"""Durable Weekly storage; issuance never settles rewards."""
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, PrimaryKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates

from .db import Base


class WeeklyWheelPlay(Base):
    __tablename__ = "weekly_wheel_plays"
    __table_args__ = (
        UniqueConstraint("profile_id", "request_key", name="uq_weekly_wheel_request"),
        UniqueConstraint("profile_id", "funded_day", "daily_ordinal", name="uq_weekly_wheel_day_slot"),
        CheckConstraint("daily_ordinal BETWEEN 1 AND 9", name="ck_weekly_wheel_daily_ordinal"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"), index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    funded_day: Mapped[date] = mapped_column(Date)
    funded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    daily_ordinal: Mapped[int] = mapped_column(Integer)
    outcomes: Mapped[list] = mapped_column(JSON)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WeeklyPlan(Base):
    __tablename__ = "weekly_plans"
    __table_args__ = (
        UniqueConstraint("profile_id", "week_start", name="uq_weekly_plan_profile_week"),
        CheckConstraint("focus IN ('Build daily consistency', 'Improve tone and control', "
                        "'Learn songs faster', 'Strengthen improvisation')", name="ck_weekly_plan_focus"),
        CheckConstraint("issued_game_mode IN ('Beginner', 'Challenge', 'Performance')",
                        name="ck_weekly_plan_game_mode"),
        CheckConstraint(
            "(issued_game_mode = 'Beginner' AND logging_days_target = 3 AND practice_minutes_target = 30 "
            "AND homework_minutes_target = 10 AND thirds_target = 3) OR "
            "(issued_game_mode = 'Challenge' AND logging_days_target = 4 AND practice_minutes_target = 60 "
            "AND homework_minutes_target = 15 AND thirds_target = 7) OR "
            "(issued_game_mode = 'Performance' AND logging_days_target = 5 AND practice_minutes_target = 90 "
            "AND homework_minutes_target = 20 AND thirds_target = 15)", name="ck_weekly_plan_mode_targets"),
        CheckConstraint("length(trim(generator_version)) > 0", name="ck_weekly_plan_generator"),
        CheckConstraint("recent_results_cutoff <= issued_at", name="ck_weekly_plan_cutoff"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"))
    week_start: Mapped[date] = mapped_column(Date)
    focus: Mapped[str] = mapped_column(String(64))
    issued_game_mode: Mapped[str] = mapped_column(String(20))
    generator_version: Mapped[str] = mapped_column(String(40))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recent_results_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recent_results: Mapped[dict] = mapped_column(JSON(none_as_null=True))
    logging_days_target: Mapped[int] = mapped_column(Integer)
    practice_minutes_target: Mapped[int] = mapped_column(Integer)
    homework_minutes_target: Mapped[int] = mapped_column(Integer)
    thirds_target: Mapped[int] = mapped_column(Integer)

    @validates("week_start")
    def monday_only(self, key, value):
        # Routes derive this date on the server. Avoid a dialect-specific SQL
        # weekday CHECK; raw SQL remains subject to the account/week UNIQUE.
        if type(value) is not date or value.weekday() != 0:
            raise ValueError("Weekly plans must start on Monday.")
        return value


class WeeklyInstrumentPlan(Base):
    """Storage for the later explicit-start flow; B2a has no allocating route."""
    __tablename__ = "weekly_instrument_plans"
    __table_args__ = (
        CheckConstraint("slot_no IN (1, 2)", name="ck_weekly_instrument_slot"),
        UniqueConstraint("plan_id", "instrument", name="uq_weekly_plan_instrument"),
        CheckConstraint("length(trim(instrument)) > 0", name="ck_weekly_instrument_nonempty"),
    )
    plan_id: Mapped[int] = mapped_column(ForeignKey("weekly_plans.id", ondelete="CASCADE"), primary_key=True)
    slot_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument: Mapped[str] = mapped_column(String(50))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    homework_spec: Mapped[dict] = mapped_column(JSON(none_as_null=True))


class WeeklyTaskClaim(Base):
    """Only the two non-currency Caper acknowledgements; jars use RewardGrant."""
    __tablename__ = "weekly_task_claims"
    __table_args__ = (
        PrimaryKeyConstraint("plan_id", "lane", name="pk_weekly_task_claim"),
        CheckConstraint("lane IN ('board', 'thirds')", name="ck_weekly_task_claim_lane"),
    )
    plan_id: Mapped[int] = mapped_column(ForeignKey("weekly_plans.id", ondelete="CASCADE"))
    lane: Mapped[str] = mapped_column(String(10))
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WeeklyNewsletterPresentation(Base):
    """One automatic presentation attempt, not a read receipt or entitlement."""
    __tablename__ = "weekly_newsletter_presentations"
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"), primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, primary_key=True)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
