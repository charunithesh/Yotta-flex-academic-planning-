from __future__ import annotations

import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.generator import GeneratorConfig, generate_dataset
from app.models import (
    Availability,
    Course,
    Faculty,
    FacultySkill,
    ForecastRecord,
    GroundTruthCase,
    Prerequisite,
    Program,
    RegistrationInterest,
    Room,
    Section,
    StateEvent,
    StudentPlan,
    UnmetDemand,
    WorkloadAssignment,
)
from app.services import run_forecast_and_allocation, validate_run


def new_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return engine, Session(engine, expire_on_commit=False)


class PlanningWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.session = new_session()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_master_data_joins_and_labeled_scenarios_are_valid(self):
        counts = generate_dataset(
            self.session,
            GeneratorConfig(programs=3, courses=15, faculty=9, student_plans=90, seed=20261022),
        )
        self.assertEqual(counts["programs"], 3)
        self.assertEqual(counts["courses"], 15)
        self.assertEqual(counts["faculty"], 9)
        self.assertEqual(counts["student_plans"], 90)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Program)), 3)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Course)), 15)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Faculty)), 9)
        self.assertGreater(self.session.scalar(select(func.count()).select_from(Prerequisite)), 0)
        self.assertGreater(self.session.scalar(select(func.count()).select_from(Availability)), 0)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(GroundTruthCase)), 5)

        invalid = self.session.execute(
            select(RegistrationInterest.id)
            .join(StudentPlan, StudentPlan.id == RegistrationInterest.student_plan_id)
            .join(Course, Course.id == RegistrationInterest.course_id)
            .where(
                (RegistrationInterest.term != StudentPlan.term)
                | (StudentPlan.program_id != Course.program_id)
            )
        ).all()
        self.assertEqual(invalid, [])
        self.assertEqual(
            {case.issue_type for case in self.session.scalars(select(GroundTruthCase))},
            {
                "faculty_outside_skillset",
                "workload_exceeded",
                "section_capacity_too_low",
                "prerequisite_order_violation",
                "duplicate_section_assignment",
            },
        )
        outside_skill = self.session.scalars(
            select(GroundTruthCase).where(GroundTruthCase.issue_type == "faculty_outside_skillset")
        ).one()
        self.assertIsNone(
            self.session.scalar(
                select(FacultySkill.id).where(
                    FacultySkill.faculty_id == outside_skill.entity_id,
                    FacultySkill.course_id == outside_skill.related_entity_id,
                )
            )
        )

    def test_forecast_allocation_reconciles_and_respects_constraints(self):
        generate_dataset(
            self.session,
            GeneratorConfig(programs=4, courses=24, faculty=20, student_plans=240, seed=20261022),
        )
        result = run_forecast_and_allocation(self.session, "2026-Fall", 20261022)
        self.assertTrue(result["reconciles"])
        self.assertEqual(
            result["forecasted_enrollment"],
            result["allocated_seats"] + result["unmet_seats"],
        )
        self.assertEqual(result["section_count"], result["assignment_count"])
        self.assertGreaterEqual(result["offered_capacity"], result["allocated_seats"])
        self.assertEqual(
            result["seat_utilization"],
            round(result["allocated_seats"] / result["offered_capacity"], 4),
        )
        self.assertGreater(self.session.scalar(select(func.count()).select_from(ForecastRecord)), 0)
        self.assertGreater(self.session.scalar(select(func.count()).select_from(StateEvent)), 0)

        report = validate_run(self.session, result["run_id"])
        self.assertTrue(report["valid"], report["issues"])
        for section in self.session.scalars(select(Section).where(Section.run_id == result["run_id"])):
            room = self.session.get(Room, section.room_id)
            course = self.session.get(Course, section.course_id)
            assignment = self.session.scalars(
                select(WorkloadAssignment).where(WorkloadAssignment.section_id == section.id)
            ).one()
            instructor = self.session.get(Faculty, assignment.faculty_id)
            self.assertLessEqual(section.capacity, room.capacity)
            self.assertLessEqual(section.capacity, 60)
            self.assertLessEqual(assignment.weekly_hours, instructor.weekly_workload_limit)
            self.assertIsNotNone(course)

    def test_runs_append_without_replacing_history(self):
        generate_dataset(
            self.session,
            GeneratorConfig(programs=2, courses=8, faculty=8, student_plans=48, seed=42),
        )
        first = run_forecast_and_allocation(self.session, "2026-Fall", 42)
        first_section_ids = {
            section.id
            for section in self.session.scalars(select(Section).where(Section.run_id == first["run_id"]))
        }
        second = run_forecast_and_allocation(self.session, "2026-Fall", 42)
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(second["allocated_seats"], first["allocated_seats"])
        self.assertEqual(second["section_count"], first["section_count"])
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(Section).where(Section.run_id == first["run_id"])),
            len(first_section_ids),
        )
        self.assertTrue(first_section_ids.isdisjoint({
            section.id
            for section in self.session.scalars(select(Section).where(Section.run_id == second["run_id"]))
        }))
        self.assertTrue(validate_run(self.session, first["run_id"])["valid"])
        self.assertTrue(validate_run(self.session, second["run_id"])["valid"])

    def test_approved_schedule_is_reserved_across_runs(self):
        generate_dataset(
            self.session,
            GeneratorConfig(programs=2, courses=8, faculty=8, student_plans=48, seed=42),
        )
        first = run_forecast_and_allocation(self.session, "2026-Fall", 42)
        approved_section = self.session.scalars(
            select(Section).where(Section.run_id == first["run_id"])
        ).first()
        self.assertIsNotNone(approved_section)
        assert approved_section is not None
        approved_assignment = self.session.scalars(
            select(WorkloadAssignment).where(
                WorkloadAssignment.section_id == approved_section.id
            )
        ).one()
        approved_section.lifecycle_state = "approved"
        approved_assignment.lifecycle_state = "approved"
        self.session.commit()

        second = run_forecast_and_allocation(self.session, "2026-Fall", 42)
        second_sections = self.session.scalars(
            select(Section).where(Section.run_id == second["run_id"])
        ).all()
        self.assertFalse(
            any(
                section.room_id == approved_section.room_id
                and section.scheduled_at == approved_section.scheduled_at
                for section in second_sections
            )
        )
        self.assertTrue(validate_run(self.session, second["run_id"])["valid"])

    def test_fixed_seed_reproduces_forecasts_and_section_ids(self):
        config = GeneratorConfig(programs=3, courses=15, faculty=9, student_plans=90, seed=20261022)
        generate_dataset(self.session, config)
        first = run_forecast_and_allocation(self.session, "2026-Fall", config.seed)
        first_rows = [
            (row.course_id, row.forecast_enrollment)
            for row in self.session.scalars(
                select(ForecastRecord).where(ForecastRecord.run_id == first["run_id"]).order_by(ForecastRecord.course_id)
            )
        ]
        first_sections = [
            (row.id, row.course_id, row.room_id, row.capacity, row.scheduled_at)
            for row in self.session.scalars(
                select(Section).where(Section.run_id == first["run_id"]).order_by(Section.id)
            )
        ]

        other_engine, other_session = new_session()
        try:
            generate_dataset(other_session, config)
            second = run_forecast_and_allocation(other_session, "2026-Fall", config.seed)
            second_rows = [
                (row.course_id, row.forecast_enrollment)
                for row in other_session.scalars(
                    select(ForecastRecord).where(ForecastRecord.run_id == second["run_id"]).order_by(ForecastRecord.course_id)
                )
            ]
            second_sections = [
                (row.id, row.course_id, row.room_id, row.capacity, row.scheduled_at)
                for row in other_session.scalars(
                    select(Section).where(Section.run_id == second["run_id"]).order_by(Section.id)
                )
            ]
        finally:
            other_session.close()
            other_engine.dispose()

        self.assertEqual(first_rows, second_rows)
        self.assertEqual(first_sections, second_sections)

    def test_generator_refuses_to_overwrite_existing_source_records(self):
        config = GeneratorConfig(programs=2, courses=8, faculty=4, student_plans=12, seed=1)
        generate_dataset(self.session, config)
        with self.assertRaisesRegex(ValueError, "already contains"):
            generate_dataset(self.session, config)

    def test_validation_detects_invalid_lifecycle_transition(self):
        generate_dataset(
            self.session,
            GeneratorConfig(programs=2, courses=8, faculty=8, student_plans=48, seed=7),
        )
        result = run_forecast_and_allocation(self.session, "2026-Fall", 7)
        event = self.session.scalars(
            select(StateEvent).where(
                StateEvent.run_id == result["run_id"],
                StateEvent.event_type == "forecast_created",
            )
        ).one()
        event.from_state = None
        self.session.commit()
        report = validate_run(self.session, result["run_id"])
        self.assertFalse(report["valid"])
        self.assertIn("invalid_lifecycle_transition", {item["type"] for item in report["issues"]})


if __name__ == "__main__":
    unittest.main()
