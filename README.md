# Yotta Flex — Course Demand & Faculty Allocation

## Problem statement

Academic planners need to decide which courses to offer, how many sections to open, and how to assign instructors and rooms. Making those decisions from student plans, registration interest, historical enrollment, faculty skills and availability, workload limits, and room capacity can be time-consuming. Planning with these constraints separately can also leave demand uncovered or produce schedules with conflicts.

## How this project addresses it

Yotta Flex is a local academic-planning prototype that brings those inputs into one repeatable workflow. It uses historical demand and current interest to forecast enrollment, proposes sections and faculty assignments subject to resource and scheduling constraints, and reports unmet demand when resources are insufficient. Each run is saved with its inputs, results, validation, and audit events so planners can review and compare proposals. Synthetic data and deterministic seeds make the workflow reproducible without requiring institutional data.

> Sections and assignments are **proposals**, not an approved or published timetable. Review the run's validation and unmet-demand exceptions before making operational decisions.

## Technology stack

- **Python 3.11+** for the generator, forecasting, allocation, validation, and command-line interface.
- **FastAPI** and **Uvicorn** for the HTTP API and local web server; **Pydantic** validates API inputs.
- **SQLAlchemy** for relational data models and database access.
- **SQLite** as the default local database (`data/yotta_flex.db`).
- **HTML, CSS, and vanilla JavaScript** for the browser dashboard and searchable, sortable data-table views.
- **CSV, JSON, and SQLite SQL** for portable data exports.
- **`unittest`** for workflow and export tests.

## Run from a terminal

Run these commands in **PowerShell from the project root** (the directory containing `requirements.txt`). Python 3.11 or newer is required.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m app.cli generate --scale starter --seed 20261022
.\.venv\Scripts\python.exe -m uvicorn app.api:app --reload
```

The `generate` command creates the starter dataset, an initial `2026-Fall` planning run, and portable exports under `data/exports`. It only writes source data into an empty database; it does not overwrite existing data. If you already have a populated database, skip the `generate` command and start the server with the final command.

Open the dashboard at <http://127.0.0.1:8000>. The API documentation is at <http://127.0.0.1:8000/docs>, and the health check is at <http://127.0.0.1:8000/api/health>. Stop the development server with **Ctrl+C**.

The default SQLite database is `data/yotta_flex.db`. To use another SQLAlchemy database URL, set `DATABASE_URL` before starting the CLI or server. SQLite foreign-key checks are enabled. The dashboard can also generate starter data; generation still requires an empty database. Use a separate database URL for a fresh dataset rather than deleting existing data.

## Workflow

1. Generate master and source records in parent-first order: programs, courses/prerequisites, faculty/skills, rooms/availability, student plans, registration interest, and labeled scenario fixtures.
2. Select a target academic term and create a forecast run. Demand comes from the two latest available prior terms and current non-withdrawn interest:
   - Two historical terms: `round(0.55 × latest + 0.30 × previous + 0.15 × current interest)`.
   - One historical term: `round(0.70 × latest + 0.30 × current interest)`.
   - No history: current interest.
   - Historical counts include registered and waitlisted records; current interest includes registered, waitlisted and interested records.
3. Allocate sections in prerequisite order. A proposal requires an active room, an effective faculty skill, an available time slot, no collision with an approved section, and remaining approved weekly workload. Earlier proposed runs are alternatives and do not consume resources; approving a section makes its room and faculty assignment count against later runs. Section capacity is bounded by room capacity and `MAX_SECTION_CAPACITY` (default 60).
4. Persist the forecast, proposals, assignments, unmet seats, and state-event history. Runs are appended; prior runs and their identifiers remain unchanged.
5. Inspect metrics and exceptions in the dashboard or API. A run reconciles when `forecasted seats = allocated seats + unmet seats`.

If resources cannot cover a course's remaining seats, an explicit `UnmetDemand` record includes the seat count, reason, and explanation. Reported allocated seats mean forecast demand covered by proposed sections; `offered_capacity` is the sum of section capacities, and `seat_utilization` is covered forecast seats divided by offered capacity. The run validation checks skill match, availability, workload, room capacity, schedule conflicts, prerequisite order, assignment count, lifecycle transitions, and demand reconciliation.

## Data and relationships

The required entities and field-level types, requiredness, allowed values, units, uniqueness, validation rules, IDs, and cardinalities are documented in [`docs/data_dictionary.json`](docs/data_dictionary.json). SQLAlchemy models are in [`app/models.py`](app/models.py).

| Relationship | Cardinality |
|---|---|
| Program → Course, StudentPlan, Faculty | 1:N |
| StudentPlan → RegistrationInterest | 1:N |
| Course → RegistrationInterest, Section | 1:N |
| Faculty ↔ Course through FacultySkill | N:M |
| Course ↔ Course through Prerequisite | N:M |
| Faculty → Availability, WorkloadAssignment | 1:N |
| Room → Section | 1:N |
| ForecastRun → ForecastRecord, Section, UnmetDemand, StateEvent | 1:N |
| Section → WorkloadAssignment | 1:N; the allocator proposes exactly one instructor per staffed section |

Foreign keys, unique bridge constraints, enum/range checks, and relationship validation protect joins. Prerequisite and skill bridge rows carry effective dates; availability is term- and slot-specific. Master records use lifecycle states. Proposed schedule changes are represented by new run records and append-only `StateEvent` rows rather than overwriting old run results.

## Synthetic data, scale and reproducibility

The fixed default seed is `20261022`. Starter and full profiles call the same generator:

| Profile | Programs | Courses | Faculty | Student plans |
|---|---:|---:|---:|---:|
| `starter` | 10 | 100 | 30 | 600 |
| `full` | 100 | 1,000 | 500 | 20,000 |

Both use terms `2025-Fall`, `2026-Spring`, and `2026-Fall`. Plans include 3–5 course interests; course profiles create popular electives and low-enrollment variation. The generator includes faculty leave/unavailability, varied room capacities, prerequisite chains, and workload limits. Learner references are synthetic and contain no personal information. No external data or third-party source is used.

Reproduce the full-size output in a fresh database and compare checksums:

```powershell
python -m app.cli --database sqlite:///./data/full_a.db generate --scale full --seed 20261022 --output data/full_a
python -m app.cli --database sqlite:///./data/full_b.db generate --scale full --seed 20261022 --output data/full_b
```

Compare each `source_manifest.json` `sha256` map. Event timestamps and generated IDs are deterministic; the source manifest records the seed, synthetic-only provenance, license/attribution status, transformations, and fields discarded. The output contains row counts and checksums for CSV, JSON, SQL seed and validation exports. Each output directory contains:

- `csv/` and `json/`: operational source and decision tables.
- `ground_truth.csv` and `ground_truth.json`: separate labeled evaluation cases.
- `seed.sql`: SQLite schema and seed data.
- `source_manifest.json`: provenance, seed, row counts, validation summary and SHA-256 checksums.
- `validation_summary.json`: foreign-key and plan/course/term join checks.

Ground truth scenarios cover faculty outside skill set, exceeded workload, insufficient room capacity, prerequisite schedule order, and duplicate assignment. They are stored separately and excluded from forecast/allocation queries and operational CSV exports. Download labels only via `/api/exports/ground_truth.csv` or the explicit `/api/scenarios/ground-truth` endpoint.

## CLI and API

```powershell
python -m app.cli generate --scale starter --seed 20261022
python -m app.cli forecast --term 2027-Fall --seed 20261022
python -m app.cli validate
python -m app.cli export --output data/exports
```

`generate` requires an empty database; `forecast` appends a run. Key API routes:

- `GET /api/dashboard`
- `POST /api/data/generate` — `{ "scale": "starter", "seed": 20261022 }`
- `POST /api/forecast` — `{ "target_term": "2026-Fall", "seed": 20261022 }`
- `GET /api/runs/{run_id}` and `GET /api/runs/{run_id}/validation`
- `GET /api/scenarios/ground-truth`
- `GET /tables/{dataset}` opens a searchable, sortable, paginated table in a new browser tab.
- `GET /api/tables/{dataset}` serves a page of structured table data; supports `offset`, `limit` (up to 100), `search`, `sort`, and `direction`.
- `GET /api/exports/{operational_table}.csv` and `/api/exports/ground_truth.csv` download the original CSV from each table view.

## Tests

```powershell
python -m unittest discover -s tests -v
```

The test suite checks valid parent/child joins, scenario labels, deterministic workflow behavior, demand reconciliation, capacity/workload/skill constraints, and append-only run history.

## Production deployment notes

- Configure `DATABASE_URL`, `MAX_SECTION_CAPACITY`, a managed ASGI server, HTTPS termination, database backups, and monitoring for the deployment environment.
- SQLite is suitable for local/single-instance use; use a managed transactional database for multi-worker or high-concurrency production.
- The app deliberately has no public registration/authentication. Place it behind the institution's identity-aware network gateway before exposing it beyond a trusted local environment.
- Synthetic data is a development and evaluation fixture. Importing real student or faculty data requires institutional approval, data minimization, access controls, retention policy, and privacy review.
- Schedule publication, room equipment constraints, cross-listed course logic, student-level prerequisite audit, and term-specific workload rules need institution-specific integrations before using proposals as official schedules.
