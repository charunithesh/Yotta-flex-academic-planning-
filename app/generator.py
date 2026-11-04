from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Availability,
    Course,
    Faculty,
    FacultySkill,
    GroundTruthCase,
    Prerequisite,
    Program,
    RegistrationInterest,
    Room,
    StudentPlan,
)

DEFAULT_SEED = 20261022
TERMS = ("2025-Fall", "2026-Spring", "2026-Fall")
SLOTS = tuple(f"D{day}-{hour:02d}" for day in range(1, 6) for hour in range(8, 18, 2))
DEPARTMENTS = ("Applied Science", "Arts and Humanities", "Business", "Computing", "Health")
COURSE_WORDS = (
    "Foundations",
    "Methods",
    "Systems",
    "Design",
    "Analysis",
    "Applications",
    "Seminar",
    "Studio",
    "Research",
    "Practice",
)


@dataclass(frozen=True)
class GeneratorConfig:
    programs: int = 10
    courses: int = 100
    faculty: int = 30
    student_plans: int = 600
    seed: int = DEFAULT_SEED

    @classmethod
    def for_scale(cls, scale: str, seed: int = DEFAULT_SEED) -> "GeneratorConfig":
        if scale == "starter":
            return cls(seed=seed)
        if scale == "full":
            return cls(programs=100, courses=1_000, faculty=500, student_plans=20_000, seed=seed)
        raise ValueError(f"Unknown scale {scale!r}; expected 'starter' or 'full'.")

    def validate(self) -> None:
        if min(self.programs, self.courses, self.faculty, self.student_plans) < 1:
            raise ValueError("Programs, courses, faculty, and student_plans must all be positive.")
        if self.courses < self.programs:
            raise ValueError("At least one course is required for every program.")
        if self.faculty < 1:
            raise ValueError("At least one faculty member is required.")


def _chunks(items: list[dict[str, Any]], size: int = 2_000):
    for offset in range(0, len(items), size):
        yield items[offset : offset + size]


def _insert(session: Session, model: type, rows: list[dict[str, Any]]) -> None:
    for chunk in _chunks(rows):
        session.execute(model.__table__.insert(), chunk)


def _make_ground_truth(
    courses: list[dict[str, Any]],
    faculty: list[dict[str, Any]],
    room_ids: list[str],
    prerequisites: list[dict[str, Any]],
    skills: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    prereq_by_course = {row["course_id"]: row["prerequisite_course_id"] for row in prerequisites}
    skill_pairs = {(row["faculty_id"], row["course_id"]) for row in skills}
    out_of_skill_pair = next(
        (
            (member["id"], course["id"])
            for member in faculty
            for course in courses
            if (member["id"], course["id"]) not in skill_pairs
        ),
        (faculty[-1]["id"], courses[0]["id"]),
    )
    out_of_skill_faculty = out_of_skill_pair[0]
    target_course = next(course for course in courses if course["id"] == out_of_skill_pair[1])
    target_course_for_prereq = next(iter(prereq_by_course), courses[-1]["id"])
    prerequisite_course = prereq_by_course.get(target_course_for_prereq, courses[0]["id"])
    workload_faculty = faculty[0]
    cases = [
        {
            "id": "GTC-000001",
            "issue_type": "faculty_outside_skillset",
            "entity_type": "faculty",
            "entity_id": out_of_skill_faculty,
            "related_entity_id": target_course["id"],
            "expected_decision": "reject_assignment",
            "details": "Proposed course is outside the faculty member's declared skills.",
        },
        {
            "id": "GTC-000002",
            "issue_type": "workload_exceeded",
            "entity_type": "faculty",
            "entity_id": workload_faculty["id"],
            "related_entity_id": courses[-1]["id"],
            "expected_decision": "reject_assignment",
            "details": (
                f"Scenario adds {workload_faculty['weekly_workload_limit'] + 1} weekly hours; "
                f"limit is {workload_faculty['weekly_workload_limit']}."
            ),
        },
        {
            "id": "GTC-000003",
            "issue_type": "section_capacity_too_low",
            "entity_type": "course",
            "entity_id": target_course["id"],
            "related_entity_id": room_ids[0],
            "expected_decision": "split_or_record_unmet_demand",
            "details": "Scenario demand exceeds the referenced room's seating capacity.",
        },
        {
            "id": "GTC-000004",
            "issue_type": "prerequisite_order_violation",
            "entity_type": "course",
            "entity_id": target_course_for_prereq,
            "related_entity_id": prerequisite_course,
            "expected_decision": "reject_schedule",
            "details": "Scenario places the course before its prerequisite.",
        },
        {
            "id": "GTC-000005",
            "issue_type": "duplicate_section_assignment",
            "entity_type": "section",
            "entity_id": "SCN-2026-000001",
            "related_entity_id": workload_faculty["id"],
            "expected_decision": "reject_duplicate",
            "details": "Scenario submits a second assignment for the same section and faculty.",
        },
    ]
    return cases


def generate_dataset(session: Session, config: GeneratorConfig) -> dict[str, Any]:
    """Create reproducible, referentially valid source data in an empty database."""
    config.validate()
    existing = session.scalar(select(func.count()).select_from(Program)) or 0
    if existing:
        raise ValueError("The database already contains generated data; use an empty database.")

    rng = random.Random(config.seed)
    today = date(2026, 1, 1)
    programs = [
        {
            "id": f"PRG-{index:06d}",
            "name": f"Program {index:03d}",
            "department": DEPARTMENTS[(index - 1) % len(DEPARTMENTS)],
            "lifecycle_state": "active",
        }
        for index in range(1, config.programs + 1)
    ]
    courses: list[dict[str, Any]] = []
    program_ids = [row["id"] for row in programs]
    for index in range(1, config.courses + 1):
        program_id = program_ids[(index - 1) % len(program_ids)]
        profile_draw = rng.random()
        demand_profile = "popular" if profile_draw < 0.10 else "low" if profile_draw < 0.25 else "typical"
        courses.append(
            {
                "id": f"CRS-{index:06d}",
                "program_id": program_id,
                "title": f"{COURSE_WORDS[(index - 1) % len(COURSE_WORDS)]} {index:04d}",
                "credits": 4 if rng.random() < 0.15 else 3,
                "course_type": "elective" if rng.random() < 0.35 else "core",
                "demand_profile": demand_profile,
                "lifecycle_state": "active",
            }
        )
    courses_by_program: dict[str, list[str]] = {}
    course_profiles: dict[str, str] = {}
    for row in courses:
        courses_by_program.setdefault(row["program_id"], []).append(row["id"])
        course_profiles[row["id"]] = row["demand_profile"]

    prerequisites: list[dict[str, Any]] = []
    course_counter_by_program: dict[str, list[str]] = {}
    for course in courses:
        program_courses = course_counter_by_program.setdefault(course["program_id"], [])
        if program_courses and rng.random() < 0.28:
            prerequisites.append(
                {
                    "course_id": course["id"],
                    "prerequisite_course_id": program_courses[-1],
                    "min_grade": "C",
                    "effective_from": today,
                    "effective_to": None,
                }
            )
        program_courses.append(course["id"])
    if not prerequisites and len(courses) > 1:
        prerequisites.append(
            {
                "course_id": courses[1]["id"],
                "prerequisite_course_id": courses[0]["id"],
                "min_grade": "C",
                "effective_from": today,
                "effective_to": None,
            }
        )

    faculty: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    for index in range(1, config.faculty + 1):
        program_id = program_ids[(index - 1) % len(program_ids)]
        faculty_id = f"FAC-{index:06d}"
        faculty.append(
            {
                "id": faculty_id,
                "program_id": program_id,
                "weekly_workload_limit": rng.choice((9, 12, 15, 18)),
                "lifecycle_state": "active",
            }
        )
        candidates = courses_by_program[program_id]
        for course_id in rng.sample(candidates, min(len(candidates), rng.randint(4, 8))):
            skills.append(
                {
                    "faculty_id": faculty_id,
                    "course_id": course_id,
                    "proficiency": rng.randint(3, 5),
                    "effective_from": today,
                    "effective_to": None,
                }
            )

    rooms: list[dict[str, Any]] = []
    room_count = max(12, min(config.courses, config.faculty))
    for index in range(1, room_count + 1):
        rooms.append(
            {
                "id": f"ROM-{index:06d}",
                "building": f"Building {(index - 1) % 12 + 1:02d}",
                "capacity": rng.choice((18, 24, 30, 36, 48, 60, 80, 120)),
                "lifecycle_state": "active",
            }
        )

    availabilities: list[dict[str, Any]] = []
    for member in faculty:
        on_leave = rng.random() < 0.05
        for term in TERMS:
            start = date.fromisoformat(f"{term[:4]}-01-01")
            end = date(start.year + (term.endswith("Fall")), 12 if term.endswith("Fall") else 6, 31 if term.endswith("Fall") else 30)
            leave_this_term = on_leave or rng.random() < 0.025
            for slot in SLOTS:
                state = "leave" if leave_this_term else "available" if rng.random() < 0.72 else "unavailable"
                availabilities.append(
                    {
                        "faculty_id": member["id"],
                        "term": term,
                        "slot_code": slot,
                        "state": state,
                        "effective_from": start,
                        "effective_to": end,
                    }
                )

    plans: list[dict[str, Any]] = []
    interests: list[dict[str, Any]] = []
    term_weights = (0.30, 0.30, 0.40)
    term_plan_counts = [int(config.student_plans * weight) for weight in term_weights]
    term_plan_counts[-1] += config.student_plans - sum(term_plan_counts)
    plan_index = 0
    interest_index = 0
    for term, plan_count in zip(TERMS, term_plan_counts):
        term_date = date(int(term[:4]), 8 if term.endswith("Fall") else 1, 15)
        for student_index in range(1, plan_count + 1):
            plan_index += 1
            program = rng.choice(programs)
            plan_id = f"PLN-{plan_index:08d}"
            plans.append(
                {
                    "id": plan_id,
                    "student_ref": f"STU-{student_index:08d}",
                    "program_id": program["id"],
                    "term": term,
                    "created_at": datetime.combine(term_date, datetime.min.time()),
                }
            )
            available_courses = courses_by_program[program["id"]]
            take_count = min(len(available_courses), rng.randint(3, 5))
            weighted_courses = [
                (
                    course_id,
                    3 if course_profiles[course_id] == "popular"
                    else 0.45 if course_profiles[course_id] == "low"
                    else 1,
                )
                for course_id in available_courses
            ]
            selected: list[str] = []
            pool = weighted_courses.copy()
            for _pick in range(take_count):
                chosen = rng.choices([item[0] for item in pool], weights=[item[1] for item in pool], k=1)[0]
                selected.append(chosen)
                pool = [item for item in pool if item[0] != chosen]
            for course_id in selected:
                interest_index += 1
                draw = rng.random()
                status = "registered" if draw < 0.68 else "waitlisted" if draw < 0.77 else "interested" if draw < 0.95 else "withdrawn"
                interests.append(
                    {
                        "id": f"INT-{interest_index:08d}",
                        "student_plan_id": plan_id,
                        "course_id": course_id,
                        "term": term,
                        "status": status,
                        "created_at": datetime.combine(term_date, datetime.min.time()),
                    }
                )

    cases = _make_ground_truth(
        courses,
        faculty,
        [room["id"] for room in rooms],
        prerequisites,
        skills,
    )
    try:
        _insert(session, Program, programs)
        _insert(session, Course, courses)
        _insert(session, Prerequisite, prerequisites)
        _insert(session, Faculty, faculty)
        _insert(session, FacultySkill, skills)
        _insert(session, Room, rooms)
        _insert(session, Availability, availabilities)
        _insert(session, StudentPlan, plans)
        _insert(session, RegistrationInterest, interests)
        _insert(session, GroundTruthCase, cases)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return {
        "seed": config.seed,
        "programs": len(programs),
        "courses": len(courses),
        "prerequisites": len(prerequisites),
        "faculty": len(faculty),
        "faculty_skills": len(skills),
        "availabilities": len(availabilities),
        "rooms": len(rooms),
        "student_plans": len(plans),
        "registration_interests": len(interests),
        "ground_truth_cases": len(cases),
    }
