from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Program(Base):
    __tablename__ = "programs"
    id: Mapped[str] = mapped_column(String(13), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    department: Mapped[str] = mapped_column(String(80), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    __table_args__ = (
        CheckConstraint("lifecycle_state IN ('active', 'inactive', 'retired')"),
    )


class Course(Base):
    __tablename__ = "courses"
    id: Mapped[str] = mapped_column(String(13), primary_key=True)
    program_id: Mapped[str] = mapped_column(ForeignKey("programs.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    course_type: Mapped[str] = mapped_column(String(16), nullable=False)
    demand_profile: Mapped[str] = mapped_column(String(16), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    __table_args__ = (
        CheckConstraint("credits BETWEEN 1 AND 6"),
        CheckConstraint("course_type IN ('core', 'elective')"),
        CheckConstraint("demand_profile IN ('popular', 'typical', 'low')"),
        CheckConstraint("lifecycle_state IN ('active', 'inactive', 'retired')"),
    )


class Prerequisite(Base):
    __tablename__ = "prerequisites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    prerequisite_course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id"), nullable=False, index=True
    )
    min_grade: Mapped[str] = mapped_column(String(2), nullable=False, default="C")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (
        UniqueConstraint("course_id", "prerequisite_course_id"),
        CheckConstraint("course_id != prerequisite_course_id"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from"),
    )


class StudentPlan(Base):
    __tablename__ = "student_plans"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    student_ref: Mapped[str] = mapped_column(String(16), nullable=False)
    program_id: Mapped[str] = mapped_column(ForeignKey("programs.id"), nullable=False, index=True)
    term: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    __table_args__ = (UniqueConstraint("student_ref", "term"),)


class RegistrationInterest(Base):
    __tablename__ = "registration_interests"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    student_plan_id: Mapped[str] = mapped_column(
        ForeignKey("student_plans.id"), nullable=False, index=True
    )
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    term: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint("student_plan_id", "course_id"),
        CheckConstraint("status IN ('registered', 'waitlisted', 'interested', 'withdrawn')"),
    )


class Faculty(Base):
    __tablename__ = "faculty"
    id: Mapped[str] = mapped_column(String(13), primary_key=True)
    program_id: Mapped[str] = mapped_column(ForeignKey("programs.id"), nullable=False, index=True)
    weekly_workload_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    __table_args__ = (
        CheckConstraint("weekly_workload_limit BETWEEN 1 AND 40"),
        CheckConstraint("lifecycle_state IN ('active', 'inactive', 'retired')"),
    )


class FacultySkill(Base):
    __tablename__ = "faculty_skills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    faculty_id: Mapped[str] = mapped_column(ForeignKey("faculty.id"), nullable=False, index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    proficiency: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (
        UniqueConstraint("faculty_id", "course_id"),
        CheckConstraint("proficiency BETWEEN 1 AND 5"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from"),
    )


class Availability(Base):
    __tablename__ = "availabilities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    faculty_id: Mapped[str] = mapped_column(ForeignKey("faculty.id"), nullable=False, index=True)
    term: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    slot_code: Mapped[str] = mapped_column(String(12), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date] = mapped_column(Date, nullable=False)
    __table_args__ = (
        UniqueConstraint("faculty_id", "term", "slot_code"),
        CheckConstraint("state IN ('available', 'unavailable', 'leave')"),
        CheckConstraint("effective_to >= effective_from"),
    )


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    building: Mapped[str] = mapped_column(String(40), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    __table_args__ = (
        CheckConstraint("capacity BETWEEN 1 AND 500"),
        CheckConstraint("lifecycle_state IN ('active', 'inactive', 'retired')"),
    )


class ForecastRun(Base):
    __tablename__ = "forecast_runs"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    target_term: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    window_terms: Mapped[str] = mapped_column(String(80), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    __table_args__ = (CheckConstraint("status IN ('running', 'completed', 'failed')"),)


class ForecastRecord(Base):
    __tablename__ = "forecasts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"), nullable=False, index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    historical_demand: Mapped[float] = mapped_column(Float, nullable=False)
    current_interest: Mapped[int] = mapped_column(Integer, nullable=False)
    forecast_enrollment: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (UniqueConstraint("run_id", "course_id"),)


class Section(Base):
    __tablename__ = "sections"
    id: Mapped[str] = mapped_column(String(18), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"), nullable=False, index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id"), nullable=False)
    term: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    slot_code: Mapped[str] = mapped_column(String(12), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False)
    __table_args__ = (
        CheckConstraint("capacity > 0"),
        CheckConstraint("lifecycle_state IN ('proposed', 'approved', 'cancelled')"),
    )


Index(
    "uq_approved_room_schedule",
    Section.room_id,
    Section.scheduled_at,
    unique=True,
    sqlite_where=Section.lifecycle_state == "approved",
    postgresql_where=Section.lifecycle_state == "approved",
)


class WorkloadAssignment(Base):
    __tablename__ = "workload_assignments"
    id: Mapped[str] = mapped_column(String(18), primary_key=True)
    section_id: Mapped[str] = mapped_column(ForeignKey("sections.id"), nullable=False, index=True)
    faculty_id: Mapped[str] = mapped_column(ForeignKey("faculty.id"), nullable=False, index=True)
    weekly_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint("section_id"),
        CheckConstraint("weekly_hours BETWEEN 1 AND 40"),
        CheckConstraint("lifecycle_state IN ('proposed', 'approved', 'cancelled')"),
    )


class UnmetDemand(Base):
    __tablename__ = "unmet_demand"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("forecast_runs.id"), nullable=False, index=True)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    unmet_seats: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[str] = mapped_column(String(240), nullable=False)
    __table_args__ = (CheckConstraint("unmet_seats > 0"),)


class StateEvent(Base):
    __tablename__ = "state_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("forecast_runs.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(16))
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    details: Mapped[str] = mapped_column(String(240), nullable=False, default="")


class GroundTruthCase(Base):
    __tablename__ = "ground_truth_cases"
    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    issue_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(24), nullable=False)
    related_entity_id: Mapped[str] = mapped_column(String(24), nullable=False)
    expected_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[str] = mapped_column(String(240), nullable=False)
    __table_args__ = (
        CheckConstraint(
            "issue_type IN ('faculty_outside_skillset', 'workload_exceeded', "
            "'section_capacity_too_low', 'prerequisite_order_violation', "
            "'duplicate_section_assignment')"
        ),
    )


Index("ix_interest_term_status", RegistrationInterest.term, RegistrationInterest.status)
Index("ix_availability_term_slot", Availability.term, Availability.slot_code)
