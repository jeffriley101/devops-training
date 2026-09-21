"""Age declarations and protected correction provenance, separate from consent and membership."""
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Integer, ForeignKey, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

class AccountPrivacy(Base):
    __tablename__ = 'account_privacy_rules'
    __table_args__ = (CheckConstraint("age_band IN ('under13','13to17','adult','unknown')", name='ck_account_age_band'),)
    profile_id: Mapped[int] = mapped_column(ForeignKey('woodchuck_profiles.id'), primary_key=True)
    age_band: Mapped[str] = mapped_column(String(12), nullable=False)
    declared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    public_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_id: Mapped[int | None] = mapped_column(ForeignKey('child_consent_evidence.id'))
    private_plunge_best: Mapped[int] = mapped_column(Integer, default=0, server_default='0', nullable=False)


class AgeCorrection(Base):
    __tablename__ = 'account_age_corrections'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey('woodchuck_profiles.id'), nullable=False)
    previous_band: Mapped[str] = mapped_column(String(12), nullable=False)
    corrected_band: Mapped[str] = mapped_column(String(12), nullable=False)
    corrected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    case_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    wording_version: Mapped[str] = mapped_column(String(40), nullable=False)
