"""C001 emergency controls and short-lived protection metadata."""
from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class AbuseEvent(Base):
    __tablename__ = 'c001_abuse_events'
    __table_args__ = (
        Index('ix_c001_abuse_network_kind_time', 'network_key', 'kind', 'created_at'),
        Index('ix_c001_abuse_browser_kind_time', 'browser_key', 'kind', 'created_at'),
        Index('ix_c001_abuse_expiry', 'created_at'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    network_key: Mapped[str | None] = mapped_column(String(64))
    browser_key: Mapped[str | None] = mapped_column(String(64))
    # Temporary correlation for rapid creation/deletion detection, not a FK or audit.
    profile_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')


class ActivationControl(Base):
    __tablename__ = 'c001_activation_control'
    __table_args__ = (
        CheckConstraint('id = 1', name='ck_c001_control_singleton'),
        CheckConstraint('emergency_ceiling > 0', name='ck_c001_emergency_ceiling'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default='true')
    emergency_ceiling: Mapped[int] = mapped_column(Integer, nullable=False, default=1000, server_default='1000')
