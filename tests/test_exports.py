from __future__ import annotations

import csv
import re
import unittest
from io import StringIO

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import app
from app.database import Base, get_db
from app.generator import GeneratorConfig, generate_dataset
from app.exports import OPERATIONAL_MODELS
from app.models import GroundTruthCase


class DashboardExportTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            generate_dataset(
                session,
                GeneratorConfig(programs=2, courses=8, faculty=8, student_plans=48, seed=20261022),
            )

        def override_get_db():
            with Session(self.engine, expire_on_commit=False) as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        app.dependency_overrides.pop(get_db, None)
        self.engine.dispose()

    def test_dashboard_controls_and_every_portable_export_work(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/static/app.css").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/static/data-table.css").status_code, 200)
        self.assertEqual(self.client.get("/static/data-table.js").status_code, 200)

        dashboard = self.client.get("/api/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(dashboard.json()["counts"]["courses"], 8)

        forecast = self.client.post(
            "/api/forecast",
            json={"target_term": "2026-Fall", "seed": 20261022},
        )
        self.assertEqual(forecast.status_code, 201, forecast.text)
        run_id = forecast.json()["run_id"]
        details = self.client.get(f"/api/runs/{run_id}")
        self.assertEqual(details.status_code, 200)
        self.assertTrue(details.json()["reconciles"])
        self.assertTrue(self.client.get(f"/api/runs/{run_id}/validation").json()["valid"])
        self.assertTrue(
            self.client.get("/api/scenarios/ground-truth").json()["separate_from_forecast_inputs"]
        )

        page = self.client.get("/").text
        export_links = re.findall(r'href="(/tables/[^"]+)"[^>]*target="_blank"', page)
        self.assertEqual(
            set(export_links),
            {"/tables/ground_truth_cases"}
            | {
                f"/tables/{model.__tablename__}" for model in OPERATIONAL_MODELS
            },
        )
        for path in export_links:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn('id="tableHead"', response.text)

        datasets = [model.__tablename__ for model in OPERATIONAL_MODELS] + [
            GroundTruthCase.__tablename__
        ]
        for dataset in datasets:
            with self.subTest(dataset=dataset):
                response = self.client.get(f"/api/tables/{dataset}")
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["dataset"], dataset)
                self.assertGreater(len(result["columns"]), 0)
                self.assertLessEqual(len(result["rows"]), result["limit"])
                self.assertEqual(self.client.get(f"/tables/{dataset}").status_code, 200)
                csv_response = self.client.get(f"/api/exports/{dataset}.csv")
                self.assertEqual(csv_response.status_code, 200)
                self.assertIn("text/plain", csv_response.headers["content-type"])
                self.assertGreater(len(csv_response.content), 0)

        first_page = self.client.get("/api/tables/courses?limit=3&sort=id&direction=asc").json()
        second_page = self.client.get("/api/tables/courses?limit=3&offset=3&sort=id&direction=asc").json()
        self.assertEqual(len(first_page["rows"]), 3)
        self.assertNotEqual(first_page["rows"][0]["id"], second_page["rows"][0]["id"])
        self.assertLess(first_page["rows"][0]["id"], first_page["rows"][1]["id"])

        search = self.client.get("/api/tables/courses?search=Foundations&limit=100").json()
        self.assertGreater(search["total"], 0)
        self.assertTrue(all("Foundations" in row["title"] for row in search["rows"]))

        raw_csv = self.client.get("/api/exports/courses.csv")
        self.assertGreaterEqual(len(list(csv.reader(StringIO(raw_csv.text)))), 2)
        self.assertEqual(
            self.client.get("/api/tables/courses?limit=101").status_code,
            422,
        )
        self.assertEqual(self.client.get("/tables/not_a_dataset").status_code, 404)
        self.assertEqual(self.client.get("/api/tables/not_a_dataset").status_code, 404)


if __name__ == "__main__":
    unittest.main()
