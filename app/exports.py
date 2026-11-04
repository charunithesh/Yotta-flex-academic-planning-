from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, aliased

from app.config import settings
from app.models import (
    Availability,
    Course,
    Faculty,
    FacultySkill,
    ForecastRecord,
    ForecastRun,
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

OPERATIONAL_MODELS = (
    Program,
    Course,
    Prerequisite,
    StudentPlan,
    RegistrationInterest,
    Faculty,
    FacultySkill,
    Availability,
    Room,
    ForecastRun,
    ForecastRecord,
    Section,
    WorkloadAssignment,
    UnmetDemand,
    StateEvent,
)


def _value(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _row(model: type, instance: Any) -> dict[str, Any]:
    return {column.key: _value(getattr(instance, column.key)) for column in model.__table__.columns}


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    return "'" + str(value).replace("'", "''") + "'"


def _write_sql_seed(database_path: Path, seed_path: Path) -> None:
    with sqlite3.connect(str(database_path)) as connection, seed_path.open("w", encoding="utf-8") as file:
        tables = connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        indexes = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND sql IS NOT NULL ORDER BY name"
        ).fetchall()
        file.write("PRAGMA foreign_keys=OFF;\nBEGIN TRANSACTION;\n")
        for _name, create_sql in tables:
            file.write(f"{create_sql};\n")
        for (create_sql,) in indexes:
            file.write(f"{create_sql};\n")
        for table_name, _create_sql in tables:
            columns = connection.execute(
                f"PRAGMA table_info({_quote_identifier(table_name)})"
            ).fetchall()
            primary_key = sorted((row[5], row[1]) for row in columns if row[5])
            order_by = (
                " ORDER BY " + ", ".join(_quote_identifier(column) for _, column in primary_key)
                if primary_key
                else ""
            )
            table = _quote_identifier(table_name)
            for row in connection.execute(f"SELECT * FROM {table}{order_by}"):
                values = ", ".join(_sql_literal(value) for value in row)
                file.write(f"INSERT INTO {table} VALUES ({values});\n")
        file.write("COMMIT;\nPRAGMA foreign_keys=ON;\n")


def _write_table(session: Session, model: type, csv_dir: Path, json_dir: Path) -> tuple[int, list[str]]:
    columns = [column.key for column in model.__table__.columns]
    primary_key = [column.key for column in model.__table__.primary_key.columns]
    query = select(model).order_by(*(getattr(model, key) for key in primary_key))
    rows = (_row(model, instance) for instance in session.scalars(query))
    csv_path = csv_dir / f"{model.__tablename__}.csv"
    json_path = json_dir / f"{model.__tablename__}.json"
    count = 0
    with (
        csv_path.open("w", newline="", encoding="utf-8") as csv_file,
        json_path.open("w", encoding="utf-8") as json_file,
    ):
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        json_file.write("[\n")
        for row in rows:
            writer.writerow(row)
            if count:
                json_file.write(",\n")
            json_file.write(json.dumps(row, ensure_ascii=False))
            count += 1
        json_file.write("\n]\n")
    return count, [str(csv_path), str(json_path)]


def validate_dataset(session: Session) -> dict[str, Any]:
    counts = {
        model.__tablename__: session.scalar(select(func.count()).select_from(model)) or 0
        for model in (*OPERATIONAL_MODELS, GroundTruthCase)
    }
    mismatched_interests = session.execute(
        select(RegistrationInterest.id)
        .join(StudentPlan, StudentPlan.id == RegistrationInterest.student_plan_id)
        .join(Course, Course.id == RegistrationInterest.course_id)
        .where(
            (RegistrationInterest.term != StudentPlan.term)
            | (StudentPlan.program_id != Course.program_id)
        )
        .limit(10)
    ).scalars().all()
    mismatched_skills = session.execute(
        select(FacultySkill.id)
        .join(Faculty, Faculty.id == FacultySkill.faculty_id)
        .join(Course, Course.id == FacultySkill.course_id)
        .where(Faculty.program_id != Course.program_id)
        .limit(10)
    ).scalars().all()
    prerequisite_course = aliased(Course)
    mismatched_prerequisites = session.execute(
        select(Prerequisite.id)
        .join(Course, Course.id == Prerequisite.course_id)
        .join(prerequisite_course, prerequisite_course.id == Prerequisite.prerequisite_course_id)
        .where(Course.program_id != prerequisite_course.program_id)
        .limit(10)
    ).scalars().all()
    fk_violations: list[dict[str, Any]] = []
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        fk_violations = [
            {"table": row[0], "rowid": row[1], "parent": row[2]}
            for row in connection.exec_driver_sql("PRAGMA foreign_key_check").all()
        ]
    issues = []
    if mismatched_interests:
        issues.append(
            {"type": "interest_plan_course_mismatch", "record_ids": list(mismatched_interests)}
        )
    if mismatched_skills:
        issues.append({"type": "faculty_skill_program_mismatch", "record_ids": list(mismatched_skills)})
    if mismatched_prerequisites:
        issues.append(
            {"type": "prerequisite_program_mismatch", "record_ids": list(mismatched_prerequisites)}
        )
    if fk_violations:
        issues.append({"type": "foreign_key_violation", "records": fk_violations[:10]})
    return {"valid": not issues, "issues": issues, "row_counts": counts}


def export_dataset(session: Session, output_dir: Path, seed: int) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_dir, json_dir = output_dir / "csv", output_dir / "json"
    csv_dir.mkdir(exist_ok=True)
    json_dir.mkdir(exist_ok=True)
    row_counts: dict[str, int] = {}
    exported_paths: list[Path] = []
    for model in OPERATIONAL_MODELS:
        count, paths = _write_table(session, model, csv_dir, json_dir)
        row_counts[model.__tablename__] = count
        exported_paths.extend(Path(path) for path in paths)

    ground_truth = [
        _row(GroundTruthCase, item)
        for item in session.scalars(
            select(GroundTruthCase).order_by(GroundTruthCase.id)
        )
    ]
    gt_csv_path = output_dir / "ground_truth.csv"
    gt_json_path = output_dir / "ground_truth.json"
    if ground_truth:
        with gt_csv_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(ground_truth[0]))
            writer.writeheader()
            writer.writerows(ground_truth)
    else:
        gt_csv_path.write_text("", encoding="utf-8")
    gt_json_path.write_text(json.dumps(ground_truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    exported_paths.extend((gt_csv_path, gt_json_path))

    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("SQL seed export currently requires a file-backed SQLite database.")
    database_path = Path(url.database)
    if not database_path.is_absolute():
        database_path = Path.cwd() / database_path
    seed_path = output_dir / "seed.sql"
    _write_sql_seed(database_path, seed_path)
    exported_paths.append(seed_path)

    validation = validate_dataset(session)
    validation_path = output_dir / "validation_summary.json"
    validation_path.write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    exported_paths.append(validation_path)
    checksums = {
        str(path.relative_to(output_dir)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(exported_paths)
    }
    manifest = {
        "dataset_version": "1.0",
        "provenance": "Synthetic data generated locally; no external datasets or personal records used.",
        "external_sources": [],
        "license_terms": "No third-party dataset or license applies; generated synthetic records.",
        "attribution": "None required; no external data used.",
        "generator": "app.generator.generate_dataset",
        "transformations": (
            "Seeded synthetic generation, weighted term/course sampling, and rule-based "
            "forecasts and allocations."
        ),
        "fields_discarded": [],
        "seed": seed,
        "timestamp_policy": "Event times are deterministic and term-derived; wall-clock generation time is omitted.",
        "row_counts": row_counts,
        "ground_truth_rows": len(ground_truth),
        "ground_truth_separation": "ground_truth.csv/json are labels and must not be joined into model input features.",
        "validation": validation,
        "sha256": checksums,
    }
    manifest_path = output_dir / "source_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest
