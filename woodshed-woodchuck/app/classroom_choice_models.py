"""Class work and retained versions; no student completion or activity records."""
from datetime import date, datetime
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class ClassroomChoiceAssignment(Base):
    __tablename__ = 'classroom_choice_assignments'
    __table_args__ = (
        ForeignKeyConstraint(['class_id', 'program_id'], ['classroom_classes.id', 'classroom_classes.program_id'], ondelete='RESTRICT'),
        UniqueConstraint('id', 'class_id', 'program_id', name='uq_classroom_choice_scope'),
        UniqueConstraint('class_id', 'request_key', name='uq_classroom_choice_request'),
        Index('ix_classroom_choice_week', 'class_id', 'week_start', 'id'),
        CheckConstraint('current_version > 0', name='ck_classroom_choice_version'),
        CheckConstraint('length(request_key) = 32', name='ck_classroom_choice_request'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_key: Mapped[str] = mapped_column(String(32), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)


class ClassroomChoiceVersion(Base):
    __tablename__ = 'classroom_choice_versions'
    __table_args__ = (
        ForeignKeyConstraint(['assignment_id', 'class_id', 'program_id'],
            ['classroom_choice_assignments.id', 'classroom_choice_assignments.class_id', 'classroom_choice_assignments.program_id'], ondelete='RESTRICT'),
        ForeignKeyConstraint(['author_assignment_id', 'class_id', 'program_id'],
            ['classroom_teaching_assignments.id', 'classroom_teaching_assignments.class_id', 'classroom_teaching_assignments.program_id'], ondelete='RESTRICT'),
        CheckConstraint('version > 0', name='ck_classroom_choice_history_version'),
        CheckConstraint("state IN ('active', 'cancelled')", name='ck_classroom_choice_state'),
    )
    assignment_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(Integer, nullable=False)
    class_id: Mapped[int] = mapped_column(Integer, nullable=False)
    author_assignment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    authored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(12), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
