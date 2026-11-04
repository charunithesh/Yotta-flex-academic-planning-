from __future__ import annotations

import csv
import io
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.orm import Session

from app.config import DATA_DIR, settings
from app.database import get_db, initialize_database
from app.exports import OPERATIONAL_MODELS
from app.generator import DEFAULT_SEED, GeneratorConfig, generate_dataset
from app.models import GroundTruthCase
from app.services import (
    WorkflowError,
    get_dashboard,
    get_run_details,
    run_forecast_and_allocation,
    validate_run,
)

logger = logging.getLogger("yotta_flex")
STATIC_DIR = Path(__file__).resolve().parent / "static"
EXPORT_MODELS = {
    model.__tablename__: model
    for model in (*OPERATIONAL_MODELS, GroundTruthCase)
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    initialize_database()
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="Auditable demand forecasting and capacity-aware faculty allocation.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class GenerateRequest(BaseModel):
    scale: Literal["starter", "full"] = "starter"
    seed: int = Field(default=DEFAULT_SEED, ge=0, le=2_147_483_647)


class ForecastRequest(BaseModel):
    target_term: str = Field(pattern=r"^\d{4}-(Spring|Summer|Fall)$")
    seed: int = Field(default=DEFAULT_SEED, ge=0, le=2_147_483_647)


@app.exception_handler(WorkflowError)
async def workflow_error_handler(_request, exc: WorkflowError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/", include_in_schema=False)
def dashboard_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/tables/{dataset}", include_in_schema=False)
def data_table_page(dataset: str) -> FileResponse:
    if dataset not in EXPORT_MODELS:
        raise HTTPException(status_code=404, detail="Unknown data table.")
    return FileResponse(STATIC_DIR / "data-table.html")


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict:
    return get_dashboard(db)


@app.post("/api/data/generate", status_code=201)
def generate_data(payload: GenerateRequest, db: Session = Depends(get_db)) -> dict:
    config = GeneratorConfig.for_scale(payload.scale, seed=payload.seed)
    try:
        counts = generate_dataset(db, config)
    except ValueError as exc:
        message = str(exc)
        status = 409 if "already contains" in message else 422
        raise HTTPException(status_code=status, detail=message) from exc
    logger.info("Generated %s synthetic academic records with seed %s", payload.scale, payload.seed)
    return {"status": "generated", "scale": payload.scale, "counts": counts}


@app.post("/api/forecast", status_code=201)
def forecast(payload: ForecastRequest, db: Session = Depends(get_db)) -> dict:
    try:
        result = run_forecast_and_allocation(db, payload.target_term, payload.seed)
    except WorkflowError:
        raise
    logger.info("Completed forecast and allocation run %s", result["run_id"])
    return result


@app.get("/api/runs/{run_id}")
def run_details(run_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return get_run_details(db, run_id)
    except WorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/validation")
def run_validation(run_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        return validate_run(db, run_id)
    except WorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/scenarios/ground-truth")
def ground_truth_scenarios(db: Session = Depends(get_db)) -> dict:
    cases = db.scalars(select(GroundTruthCase).order_by(GroundTruthCase.id)).all()
    return {
        "separate_from_forecast_inputs": True,
        "cases": [
            {
                "id": case.id,
                "issue_type": case.issue_type,
                "entity_type": case.entity_type,
                "entity_id": case.entity_id,
                "related_entity_id": case.related_entity_id,
                "expected_decision": case.expected_decision,
                "details": case.details,
            }
            for case in cases
        ],
    }


@app.get("/api/tables/{dataset}")
def data_table(
    dataset: str,
    db: Session = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    search: str = Query(default="", max_length=120),
    sort: str = Query(default=""),
    direction: Literal["asc", "desc"] = "asc",
) -> dict:
    model = EXPORT_MODELS.get(dataset)
    if model is None:
        raise HTTPException(status_code=404, detail="Unknown data table.")
    columns = list(model.__table__.columns)
    column_map = {column.key: column for column in columns}
    statement = select(model)
    count_statement = select(func.count()).select_from(model)
    search = search.strip()
    if search:
        predicate = or_(*(cast(column, String).ilike(f"%{search}%") for column in columns))
        statement = statement.where(predicate)
        count_statement = count_statement.where(predicate)
    total = db.scalar(count_statement) or 0
    order_column = column_map.get(sort)
    if order_column is not None:
        statement = statement.order_by(
            order_column.desc() if direction == "desc" else order_column.asc()
        )
    else:
        primary_key_columns = [column for column in columns if column.primary_key]
        statement = statement.order_by(*primary_key_columns)
    rows = []
    for instance in db.scalars(statement.offset(offset).limit(limit)):
        rows.append(
            {
                column.key: (
                    value.isoformat() if hasattr(value, "isoformat") else value
                )
                for column in columns
                for value in (getattr(instance, column.key),)
            }
        )
    return {
        "dataset": dataset,
        "columns": [column.key for column in columns],
        "rows": rows,
        "total": total,
        "offset": offset,
        "limit": limit,
        "search": search,
        "sort": sort if order_column is not None else "",
        "direction": direction if order_column is not None else "asc",
        "csv_url": f"/api/exports/{dataset}.csv",
    }


@app.get("/api/exports/ground_truth.csv")
def export_ground_truth_csv(db: Session = Depends(get_db)) -> StreamingResponse:
    return _csv_response(db, GroundTruthCase, "ground_truth.csv")


@app.get("/api/exports/{dataset}.csv")
def export_csv(dataset: str, db: Session = Depends(get_db)) -> StreamingResponse:
    model = EXPORT_MODELS.get(dataset)
    if model is None:
        raise HTTPException(status_code=404, detail="Unknown operational dataset.")
    return _csv_response(db, model, f"{dataset}.csv")


def _csv_response(db: Session, model: type, filename: str) -> StreamingResponse:
    columns = [column.key for column in model.__table__.columns]
    primary_key = [column.key for column in model.__table__.primary_key.columns]

    def rows():
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        batch = []
        query = select(model).order_by(*(getattr(model, key) for key in primary_key))
        for item in db.scalars(query.execution_options(yield_per=1_000)):
            batch.append(
                [
                    getattr(item, column).isoformat()
                    if hasattr(getattr(item, column), "isoformat")
                    else getattr(item, column)
                    for column in columns
                ]
            )
            if len(batch) == 500:
                writer.writerows(batch)
                batch.clear()
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)
        if batch:
            writer.writerows(batch)
            yield buffer.getvalue()

    return StreamingResponse(
        rows(),
        media_type="text/plain",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
