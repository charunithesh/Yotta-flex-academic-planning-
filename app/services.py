from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Availability,
    Course,
    Faculty,
    FacultySkill,
    ForecastRecord,
    ForecastRun,
    Program,
    Prerequisite,
    RegistrationInterest,
    Room,
    Section,
    StateEvent,
    StudentPlan,
    UnmetDemand,
    WorkloadAssignment,
)

ACTIVE_INTEREST_STATUSES = ("registered", "waitlisted", "interested")
HISTORICAL_STATUSES = ("registered", "waitlisted")
SEASON_ORDER = {"Spring": 1, "Summer": 2, "Fall": 3}


class WorkflowError(ValueError):
    """A user-correctable error in the academic planning workflow."""


def _term_key(term: str) -> tuple[int, int]:
    try:
        year, season = term.split("-", maxsplit=1)
        return int(year), SEASON_ORDER[season]
    except (ValueError, KeyError) as exc:
        raise WorkflowError(f"Invalid term {term!r}; use YYYY-Spring, YYYY-Summer, or YYYY-Fall.") from exc


def _term_date(term: str) -> date:
    year, season = term.split("-", maxsplit=1)
    month_day = {"Spring": (1, 12), "Summer": (5, 11), "Fall": (8, 24)}
    try:
        month, day = month_day[season]
    except KeyError as exc:
        raise WorkflowError(f"Unsupported academic term {term!r}.") from exc
    return date(int(year), month, day)


def _next_identifier(session: Session, column: Any, prefix: str) -> int:
    existing = session.scalars(select(column)).all()
    numbers = []
    for identifier in existing:
        if identifier and identifier.startswith(prefix):
            try:
                numbers.append(int(identifier.rsplit("-", maxsplit=1)[1]))
            except ValueError:
                continue
    return max(numbers, default=0) + 1


def _insert_event(
    session: Session,
    run_id: str,
    entity_type: str,
    entity_id: str,
    event_type: str,
    to_state: str,
    details: str,
    occurred_at: datetime,
    from_state: str | None = None,
) -> None:
    session.add(
        StateEvent(
            run_id=run_id,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            from_state=from_state,
            to_state=to_state,
            occurred_at=occurred_at,
            details=details,
        )
    )


def _prerequisite_depths(session: Session, courses: list[Course], target: date) -> dict[str, int]:
    rows = session.scalars(
        select(Prerequisite).where(
            Prerequisite.effective_from <= target,
            (Prerequisite.effective_to.is_(None)) | (Prerequisite.effective_to >= target),
        )
    ).all()
    parents: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        parents[row.course_id].append(row.prerequisite_course_id)
    course_ids = {course.id for course in courses}
    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def depth(course_id: str) -> int:
        if course_id in depths:
            return depths[course_id]
        if course_id in visiting:
            raise WorkflowError(f"Prerequisite cycle detected at course {course_id}.")
        visiting.add(course_id)
        parent_depths = [depth(parent) for parent in parents[course_id] if parent in course_ids]
        visiting.remove(course_id)
        depths[course_id] = 1 + max(parent_depths, default=-1)
        return depths[course_id]

    for course_id in course_ids:
        depth(course_id)
    return depths


def run_forecast_and_allocation(
    session: Session,
    target_term: str,
    seed: int,
    max_section_capacity: int | None = None,
) -> dict[str, Any]:
    """Append a forecast, section proposals, assignments, and audit events."""
    target_key = _term_key(target_term)
    term_start = _term_date(target_term)
    capacity_limit = (
        settings.max_section_capacity
        if max_section_capacity is None
        else max_section_capacity
    )
    if capacity_limit < 1:
        raise WorkflowError("Maximum section capacity must be positive.")
    courses = session.scalars(select(Course).where(Course.lifecycle_state == "active").order_by(Course.id)).all()
    if not courses:
        raise WorkflowError("No active courses are available. Generate or import master data first.")

    counts = session.execute(
        select(RegistrationInterest.term, RegistrationInterest.course_id, RegistrationInterest.status, func.count())
        .group_by(RegistrationInterest.term, RegistrationInterest.course_id, RegistrationInterest.status)
    ).all()
    demand: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    for term, course_id, status, count in counts:
        demand[course_id][term][status] = count
    historical_terms = sorted(
        {
            term
            for course_demands in demand.values()
            for term in course_demands
            if _term_key(term) < target_key
        },
        key=_term_key,
    )[-2:]

    run_number = (session.scalar(select(func.count()).select_from(ForecastRun)) or 0) + 1
    run_id = f"FR-{target_term[:4]}-{run_number:06d}"
    run_timestamp = datetime.combine(term_start, time.min)
    run = ForecastRun(
        id=run_id,
        target_term=target_term,
        window_terms=",".join(historical_terms),
        seed=seed,
        created_at=run_timestamp,
        status="running",
    )
    session.add(run)
    session.flush()
    _insert_event(
        session,
        run_id,
        "forecast_run",
        run_id,
        "run_started",
        "running",
        "Forecast and allocation workflow started.",
        run_timestamp,
    )
    forecast_by_course: dict[str, int] = {}
    forecast_rows: list[ForecastRecord] = []
    for course in courses:
        course_demand = demand.get(course.id, {})
        target_counts = course_demand.get(target_term, {})
        current_interest = sum(target_counts.get(status, 0) for status in ACTIVE_INTEREST_STATUSES)
        history = [
            sum(course_demand.get(term, {}).get(status, 0) for status in HISTORICAL_STATUSES)
            for term in historical_terms
        ]
        if len(history) >= 2:
            historical_average = 0.70 * history[-1] + 0.30 * history[-2]
            forecast = round(0.55 * history[-1] + 0.30 * history[-2] + 0.15 * current_interest)
        elif history:
            historical_average = float(history[-1])
            forecast = round(0.70 * history[-1] + 0.30 * current_interest)
        else:
            historical_average = float(current_interest)
            forecast = current_interest
        forecast = max(0, forecast)
        forecast_by_course[course.id] = forecast
        forecast_rows.append(
            ForecastRecord(
                run_id=run_id,
                course_id=course.id,
                historical_demand=historical_average,
                current_interest=current_interest,
                forecast_enrollment=forecast,
            )
        )
    session.add_all(forecast_rows)
    session.flush()
    _insert_event(
        session,
        run_id,
        "forecast_run",
        run_id,
        "forecast_created",
        "forecasted",
        f"Historical window={','.join(historical_terms)}; weights=0.55/0.30/0.15.",
        run_timestamp,
        from_state="running",
    )
    depth_by_course = _prerequisite_depths(session, courses, term_start)

    faculty = session.scalars(select(Faculty).where(Faculty.lifecycle_state == "active")).all()
    rooms = session.scalars(select(Room).where(Room.lifecycle_state == "active").order_by(Room.capacity.desc())).all()
    skill_rows = session.scalars(
        select(FacultySkill).where(
            FacultySkill.effective_from <= term_start,
            (FacultySkill.effective_to.is_(None)) | (FacultySkill.effective_to >= term_start),
        )
    ).all()
    skills_by_course: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for skill in skill_rows:
        skills_by_course[skill.course_id].append((skill.faculty_id, skill.proficiency))

    availability_rows = session.scalars(
        select(Availability).where(
            Availability.term == target_term,
            Availability.effective_from <= term_start,
            Availability.effective_to >= term_start,
        )
    ).all()
    availability = {(row.faculty_id, row.slot_code): row.state for row in availability_rows}
    workload_by_faculty: dict[str, int] = defaultdict(int)
    existing_workload = session.execute(
        select(WorkloadAssignment.faculty_id, func.sum(WorkloadAssignment.weekly_hours))
        .join(Section, Section.id == WorkloadAssignment.section_id)
        .where(
            Section.term == target_term,
            Section.lifecycle_state == "approved",
            WorkloadAssignment.lifecycle_state == "approved",
        )
        .group_by(WorkloadAssignment.faculty_id)
    ).all()
    for faculty_id, hours in existing_workload:
        workload_by_faculty[faculty_id] = int(hours or 0)
    existing_slots = session.execute(
        select(Section.room_id, Section.slot_code, Section.scheduled_at, WorkloadAssignment.faculty_id)
        .join(WorkloadAssignment, WorkloadAssignment.section_id == Section.id)
        .where(
            Section.term == target_term,
            Section.lifecycle_state == "approved",
            WorkloadAssignment.lifecycle_state == "approved",
        )
    ).all()
    occupied_rooms = {(room_id, scheduled_at.date(), slot) for room_id, slot, scheduled_at, _ in existing_slots}
    occupied_faculty = {(faculty_id, scheduled_at.date(), slot) for _, slot, scheduled_at, faculty_id in existing_slots}

    faculty_by_id = {member.id: member for member in faculty}
    section_number = _next_identifier(
        session, Section.id, f"SEC-{target_term[:4]}-"
    )
    assignment_number = _next_identifier(
        session, WorkloadAssignment.id, f"WLA-{target_term[:4]}-"
    )
    # One meeting slot every two hours; depth places prerequisite courses in earlier weeks.
    slots = tuple((f"D{day}-{hour:02d}", day - 1, hour) for day in range(1, 6) for hour in range(8, 18, 2))
    total_demand = sum(forecast_by_course.values())
    allocated_seats = 0
    offered_capacity = 0
    try:
        for course in sorted(courses, key=lambda item: (depth_by_course[item.id], item.id)):
            remaining = forecast_by_course[course.id]
            if remaining == 0:
                continue
            course_week = min(depth_by_course[course.id], 12)
            course_hours = course.credits
            while remaining > 0:
                choice: tuple[Room, str, datetime, Faculty] | None = None
                candidate_faculty = sorted(
                    skills_by_course.get(course.id, []),
                    key=lambda item: (workload_by_faculty[item[0]], -item[1], item[0]),
                )
                for slot_code, weekday, hour in slots:
                    scheduled_at = datetime.combine(
                        term_start + timedelta(days=course_week * 7 + weekday),
                        time(hour=hour),
                    )
                    available_rooms = [
                        room
                        for room in rooms
                        if room.capacity > 0
                        and (room.id, scheduled_at.date(), slot_code) not in occupied_rooms
                    ]
                    if not available_rooms:
                        continue
                    target_capacity = min(remaining, capacity_limit)
                    room = min(
                        (candidate for candidate in available_rooms if candidate.capacity >= target_capacity),
                        key=lambda candidate: candidate.capacity,
                        default=max(available_rooms, key=lambda candidate: candidate.capacity),
                    )
                    eligible = [
                        faculty_by_id[faculty_id]
                        for faculty_id, _proficiency in candidate_faculty
                        if faculty_id in faculty_by_id
                        and availability.get((faculty_id, slot_code)) == "available"
                        and workload_by_faculty[faculty_id] + course_hours
                        <= faculty_by_id[faculty_id].weekly_workload_limit
                        and (faculty_id, scheduled_at.date(), slot_code) not in occupied_faculty
                    ]
                    if eligible:
                        choice = (room, slot_code, scheduled_at, eligible[0])
                        break
                if choice is None:
                    break

                room, slot_code, scheduled_at, instructor = choice
                section_capacity = min(room.capacity, capacity_limit)
                seats_served = min(section_capacity, remaining)
                section_id = f"SEC-{target_term[:4]}-{section_number:06d}"
                assignment_id = f"WLA-{target_term[:4]}-{assignment_number:06d}"
                section_number += 1
                assignment_number += 1
                session.add(
                    Section(
                        id=section_id,
                        run_id=run_id,
                        course_id=course.id,
                        room_id=room.id,
                        term=target_term,
                        capacity=section_capacity,
                        scheduled_at=scheduled_at,
                        slot_code=slot_code,
                        lifecycle_state="proposed",
                    )
                )
                session.add(
                    WorkloadAssignment(
                        id=assignment_id,
                        section_id=section_id,
                        faculty_id=instructor.id,
                        weekly_hours=course_hours,
                        lifecycle_state="proposed",
                        created_at=run_timestamp,
                    )
                )
                _insert_event(
                    session,
                    run_id,
                    "section",
                    section_id,
                    "section_proposed",
                    "proposed",
                    f"Capacity={section_capacity}; room={room.id}; slot={slot_code}.",
                    run_timestamp,
                )
                _insert_event(
                    session,
                    run_id,
                    "workload_assignment",
                    assignment_id,
                    "faculty_assigned",
                    "proposed",
                    f"Faculty={instructor.id}; course={course.id}; hours={course_hours}.",
                    run_timestamp,
                )
                remaining -= seats_served
                allocated_seats += seats_served
                offered_capacity += section_capacity
                workload_by_faculty[instructor.id] += course_hours
                occupied_rooms.add((room.id, scheduled_at.date(), slot_code))
                occupied_faculty.add((instructor.id, scheduled_at.date(), slot_code))
                session.flush()

            if remaining > 0:
                course_skills = skills_by_course.get(course.id, [])
                if not rooms:
                    reason = "no_room"
                    details = "No active room is available for this forecasted demand."
                elif not course_skills:
                    reason = "no_skilled_faculty"
                    details = "No active faculty member has an effective skill for this course."
                elif not any(availability.get((faculty_id, slot)) == "available" for faculty_id, _ in course_skills for slot, _, _ in slots):
                    reason = "faculty_unavailable"
                    details = "Skilled faculty have no available meeting slot in this term."
                else:
                    reason = "capacity_or_workload"
                    details = "Available rooms, faculty workload, or meeting slots cannot cover remaining seats."
                session.add(
                    UnmetDemand(
                        run_id=run_id,
                        course_id=course.id,
                        unmet_seats=remaining,
                        reason=reason,
                        details=details,
                    )
                )
                _insert_event(
                    session,
                    run_id,
                    "course",
                    course.id,
                    "demand_unmet",
                    "unmet",
                    f"Unmet seats={remaining}; reason={reason}.",
                    run_timestamp,
                )

        run.status = "completed"
        _insert_event(
            session,
            run_id,
            "forecast_run",
            run_id,
            "allocation_completed",
            "completed",
            f"Forecast demand={total_demand}; covered seats={allocated_seats}; proposed capacity={offered_capacity}.",
            run_timestamp,
            from_state="forecasted",
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    return get_run_metrics(session, run_id)


def get_run_metrics(session: Session, run_id: str) -> dict[str, Any]:
    run = session.get(ForecastRun, run_id)
    if run is None:
        raise WorkflowError(f"Forecast run {run_id!r} was not found.")
    forecast_rows = session.scalars(select(ForecastRecord).where(ForecastRecord.run_id == run_id)).all()
    sections = session.scalars(select(Section).where(Section.run_id == run_id)).all()
    unmet_rows = session.scalars(select(UnmetDemand).where(UnmetDemand.run_id == run_id)).all()
    assignment_rows = session.scalars(
        select(WorkloadAssignment)
        .join(Section, Section.id == WorkloadAssignment.section_id)
        .where(Section.run_id == run_id)
    ).all()
    forecasted_by_course = {row.course_id: row.forecast_enrollment for row in forecast_rows}
    capacity_by_course: dict[str, int] = defaultdict(int)
    for section in sections:
        capacity_by_course[section.course_id] += section.capacity
    forecasted = sum(forecasted_by_course.values())
    allocated = sum(
        min(forecasted_by_course.get(course_id, 0), capacity)
        for course_id, capacity in capacity_by_course.items()
    )
    offered_capacity = sum(section.capacity for section in sections)
    unmet = sum(row.unmet_seats for row in unmet_rows)
    utilization = round(allocated / offered_capacity, 4) if offered_capacity else 0.0
    return {
        "run_id": run.id,
        "target_term": run.target_term,
        "window_terms": [term for term in run.window_terms.split(",") if term],
        "status": run.status,
        "forecasted_enrollment": forecasted,
        "allocated_seats": allocated,
        "offered_capacity": offered_capacity,
        "unmet_seats": unmet,
        "reconciles": forecasted == allocated + unmet,
        "section_count": len(sections),
        "assignment_count": len(assignment_rows),
        "unmet_course_count": len(unmet_rows),
        "seat_utilization": utilization,
        "created_at": run.created_at.isoformat(),
    }


def get_dashboard(session: Session) -> dict[str, Any]:
    counts = {
        "programs": session.scalar(select(func.count()).select_from(Program)) or 0,
        "courses": session.scalar(select(func.count()).select_from(Course)) or 0,
        "faculty": session.scalar(select(func.count()).select_from(Faculty)) or 0,
        "rooms": session.scalar(select(func.count()).select_from(Room)) or 0,
        "student_plans": session.scalar(select(func.count()).select_from(StudentPlan)) or 0,
        "registration_interests": session.scalar(select(func.count()).select_from(RegistrationInterest)) or 0,
    }
    runs = session.scalars(
        select(ForecastRun).order_by(ForecastRun.created_at.desc(), ForecastRun.id.desc()).limit(10)
    ).all()
    return {"counts": counts, "runs": [get_run_metrics(session, run.id) for run in runs]}


def get_run_details(session: Session, run_id: str) -> dict[str, Any]:
    metrics = get_run_metrics(session, run_id)
    forecast = session.execute(
        select(Course.id, Course.title, ForecastRecord.historical_demand, ForecastRecord.current_interest, ForecastRecord.forecast_enrollment)
        .join(ForecastRecord, ForecastRecord.course_id == Course.id)
        .where(ForecastRecord.run_id == run_id)
        .order_by(ForecastRecord.forecast_enrollment.desc(), Course.id)
    ).all()
    unmet = session.execute(
        select(Course.id, Course.title, UnmetDemand.unmet_seats, UnmetDemand.reason, UnmetDemand.details)
        .join(UnmetDemand, UnmetDemand.course_id == Course.id)
        .where(UnmetDemand.run_id == run_id)
        .order_by(UnmetDemand.unmet_seats.desc(), Course.id)
    ).all()
    events = session.scalars(
        select(StateEvent)
        .where(StateEvent.run_id == run_id)
        .order_by(StateEvent.occurred_at, StateEvent.id)
        .limit(500)
    ).all()
    return {
        **metrics,
        "forecasts": [
            {
                "course_id": row[0],
                "course": row[1],
                "historical_demand": row[2],
                "current_interest": row[3],
                "forecast_enrollment": row[4],
            }
            for row in forecast
        ],
        "unmet_demand": [
            {
                "course_id": row[0],
                "course": row[1],
                "unmet_seats": row[2],
                "reason": row[3],
                "details": row[4],
            }
            for row in unmet
        ],
        "events": [
            {
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "event_type": event.event_type,
                "from_state": event.from_state,
                "to_state": event.to_state,
                "occurred_at": event.occurred_at.isoformat(),
                "details": event.details,
            }
            for event in events
        ],
    }


def validate_run(session: Session, run_id: str) -> dict[str, Any]:
    metrics = get_run_metrics(session, run_id)
    run = session.get(ForecastRun, run_id)
    assert run is not None
    target_date = _term_date(run.target_term)
    sections = session.scalars(select(Section).where(Section.run_id == run_id)).all()
    assignments = session.scalars(
        select(WorkloadAssignment)
        .join(Section, Section.id == WorkloadAssignment.section_id)
        .where(Section.run_id == run_id)
    ).all()
    faculty = {item.id: item for item in session.scalars(select(Faculty)).all()}
    rooms = {item.id: item for item in session.scalars(select(Room)).all()}
    skills = {
        (row.faculty_id, row.course_id)
        for row in session.scalars(
            select(FacultySkill).where(
                FacultySkill.effective_from <= target_date,
                (FacultySkill.effective_to.is_(None)) | (FacultySkill.effective_to >= target_date),
            )
        )
    }
    available_slots = {
        (row.faculty_id, row.slot_code)
        for row in session.scalars(
            select(Availability).where(
                Availability.term == run.target_term,
                Availability.state == "available",
                Availability.effective_from <= target_date,
                Availability.effective_to >= target_date,
            )
        )
    }
    prerequisites = session.scalars(
        select(Prerequisite).where(
            Prerequisite.effective_from <= target_date,
            (Prerequisite.effective_to.is_(None)) | (Prerequisite.effective_to >= target_date),
        )
    ).all()
    section_by_id = {section.id: section for section in sections}
    issues: list[dict[str, str]] = []
    assignment_counts: dict[str, int] = defaultdict(int)
    occupied_faculty_slots: dict[tuple[str, date, str], str] = {}
    occupied_room_slots: dict[tuple[str, datetime], str] = {}
    all_term_schedule = session.execute(
        select(
            WorkloadAssignment.faculty_id,
            Section.scheduled_at,
            Section.slot_code,
            Section.id,
            Section.room_id,
        )
        .join(Section, Section.id == WorkloadAssignment.section_id)
        .where(
            Section.term == run.target_term,
            or_(
                Section.run_id == run_id,
                and_(
                    Section.lifecycle_state == "approved",
                    WorkloadAssignment.lifecycle_state == "approved",
                ),
            ),
        )
    ).all()
    for faculty_id, scheduled_at, slot_code, section_id, room_id in all_term_schedule:
        faculty_key = (faculty_id, scheduled_at.date(), slot_code)
        if faculty_key in occupied_faculty_slots:
            issues.append({"type": "faculty_schedule_conflict", "entity_id": faculty_id})
        occupied_faculty_slots[faculty_key] = section_id
        room_key = (room_id, scheduled_at)
        if room_key in occupied_room_slots:
            issues.append({"type": "room_schedule_conflict", "entity_id": room_id})
        occupied_room_slots[room_key] = section_id
    all_term_assignments = session.execute(
        select(WorkloadAssignment.faculty_id, func.sum(WorkloadAssignment.weekly_hours))
        .join(Section, Section.id == WorkloadAssignment.section_id)
        .where(
            Section.term == run.target_term,
            or_(
                Section.run_id == run_id,
                and_(
                    Section.lifecycle_state == "approved",
                    WorkloadAssignment.lifecycle_state == "approved",
                ),
            ),
        )
        .group_by(WorkloadAssignment.faculty_id)
    ).all()
    for assignment in assignments:
        section = section_by_id[assignment.section_id]
        assignment_counts[section.id] += 1
        if (assignment.faculty_id, section.course_id) not in skills:
            issues.append({"type": "faculty_outside_skillset", "entity_id": assignment.id})
        if (assignment.faculty_id, section.slot_code) not in available_slots:
            issues.append({"type": "faculty_unavailable", "entity_id": assignment.id})
        if assignment.weekly_hours < 1:
            issues.append({"type": "invalid_workload", "entity_id": assignment.id})
    for section in sections:
        room = rooms[section.room_id]
        if section.capacity > room.capacity or section.capacity > settings.max_section_capacity:
            issues.append({"type": "section_over_capacity", "entity_id": section.id})
        if assignment_counts[section.id] != 1:
            issues.append({"type": "invalid_assignment_count", "entity_id": section.id})
    for faculty_id, hours in all_term_assignments:
        if hours > faculty[faculty_id].weekly_workload_limit:
            issues.append({"type": "workload_exceeded", "entity_id": faculty_id})
    for prereq in prerequisites:
        later = [section for section in sections if section.course_id == prereq.course_id]
        earlier = [section for section in sections if section.course_id == prereq.prerequisite_course_id]
        if later and earlier and min(item.scheduled_at for item in later) <= max(item.scheduled_at for item in earlier):
            issues.append({"type": "prerequisite_order_violation", "entity_id": prereq.course_id})
    if not metrics["reconciles"]:
        issues.append({"type": "demand_reconciliation_failure", "entity_id": run_id})
    lifecycle_issues = _validate_lifecycle(session, run_id, run.status)
    issues.extend(lifecycle_issues)
    return {"run_id": run_id, "valid": not issues, "issues": issues, "metrics": metrics}


def _validate_lifecycle(session: Session, run_id: str, run_status: str) -> list[dict[str, str]]:
    events = session.scalars(
        select(StateEvent)
        .where(StateEvent.run_id == run_id)
        .order_by(StateEvent.id)
    ).all()
    allowed = {
        "forecast_run": {
            (None, "running"),
            ("running", "forecasted"),
            ("forecasted", "completed"),
            ("running", "failed"),
            ("forecasted", "failed"),
        },
        "section": {
            (None, "proposed"),
            ("proposed", "approved"),
            ("proposed", "cancelled"),
        },
        "workload_assignment": {
            (None, "proposed"),
            ("proposed", "approved"),
            ("proposed", "cancelled"),
        },
        "course": {(None, "unmet")},
    }
    latest_state: dict[tuple[str, str], str] = {}
    issues: list[dict[str, str]] = []
    for event in events:
        key = (event.entity_type, event.entity_id)
        previous = latest_state.get(key)
        transition = (event.from_state, event.to_state)
        if (
            event.from_state != previous
            or transition not in allowed.get(event.entity_type, set())
        ):
            issues.append(
                {"type": "invalid_lifecycle_transition", "entity_id": event.entity_id}
            )
        latest_state[key] = event.to_state
    final_run_state = latest_state.get(("forecast_run", run_id))
    if final_run_state != run_status:
        issues.append({"type": "lifecycle_status_mismatch", "entity_id": run_id})
    return issues
