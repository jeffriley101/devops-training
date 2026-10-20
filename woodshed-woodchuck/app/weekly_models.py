"""Durable funded Wheel-play storage; no runtime operations or reward grants."""
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

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
