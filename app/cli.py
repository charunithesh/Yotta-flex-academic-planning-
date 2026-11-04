from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

DEFAULT_SEED = 20261022


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate, forecast, validate, and export academic planning data.")
    parser.add_argument(
        "--database",
        help="SQLAlchemy database URL. Defaults to DATABASE_URL or sqlite:///./data/yotta_flex.db.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="Generate a fresh deterministic dataset and allocation run.")
    generate.add_argument("--scale", choices=("starter", "full"), default="starter")
    generate.add_argument("--seed", type=int, default=DEFAULT_SEED)
    generate.add_argument("--term", default="2026-Fall")
    generate.add_argument("--output", type=Path, default=Path("data/exports"))

    forecast = commands.add_parser("forecast", help="Append a forecast and constrained allocation run.")
    forecast.add_argument("--term", required=True)
    forecast.add_argument("--seed", type=int, default=DEFAULT_SEED)

    validate = commands.add_parser("validate", help="Validate source data and the latest allocation run.")
    export = commands.add_parser("export", help="Export operational tables and separate ground-truth labels.")
    export.add_argument("--seed", type=int, default=DEFAULT_SEED)
    export.add_argument("--output", type=Path, default=Path("data/exports"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.database:
        os.environ["DATABASE_URL"] = args.database

    from sqlalchemy import select

    from app.database import SessionLocal, initialize_database
    from app.exports import export_dataset, validate_dataset
    from app.generator import GeneratorConfig, generate_dataset
    from app.models import ForecastRun
    from app.services import run_forecast_and_allocation, validate_run

    initialize_database()
    with SessionLocal() as session:
        if args.command == "generate":
            counts = generate_dataset(session, GeneratorConfig.for_scale(args.scale, args.seed))
            run = run_forecast_and_allocation(session, args.term, args.seed)
            export = export_dataset(session, args.output, args.seed)
            result = {"status": "generated", "scale": args.scale, "counts": counts, "run": run, "export": export}
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "forecast":
            result = run_forecast_and_allocation(session, args.term, args.seed)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "validate":
            source_report = validate_dataset(session)
            latest = session.scalar(
                select(ForecastRun)
                .order_by(ForecastRun.created_at.desc(), ForecastRun.id.desc())
                .limit(1)
            )
            run_report = validate_run(session, latest.id) if latest else None
            result = {"source_data": source_report, "latest_run": run_report}
            print(json.dumps(result, indent=2))
            return 0 if source_report["valid"] and (run_report is None or run_report["valid"]) else 1
        if args.command == "export":
            result = export_dataset(session, args.output, args.seed)
            print(json.dumps(result, indent=2))
            return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
