from __future__ import annotations

import importlib
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from alembic import command
from pa_docket.workers.main import run_worker


def test_alembic_upgrade_and_downgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "migration.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    config = Config(str(Path("alembic.ini").resolve()))
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    names = set(inspect(engine).get_table_names())
    assert {"collection_jobs", "collection_partitions", "task_queue", "dockets", "charges"} <= names
    command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()


def test_api_requires_login_and_processes_fixture_job(tmp_path, monkeypatch):
    db_path = tmp_path / "api.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "api-data"))
    monkeypatch.setenv("OPERATOR_USERNAME", "test-operator")
    monkeypatch.setenv("OPERATOR_PASSWORD", "test-password")
    monkeypatch.setenv("SESSION_SECRET", "test-only-long-random-session-secret")
    import pa_docket.api.app as api_module

    api_module = importlib.reload(api_module)
    with TestClient(api_module.app, follow_redirects=False) as client:
        assert client.get("/api/jobs").status_code == 401
        response = client.post("/login", data={"username": "test-operator", "password": "test-password"})
        assert response.status_code == 303
        response = client.post("/api/collect", json={"docket_number": "MD-01-CR-0000123-2025"})
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        run_worker(api_module.SessionFactory, api_module.settings, concurrency=1)
        jobs = client.get("/api/jobs")
        assert jobs.status_code == 200
        assert any(job["id"] == job_id and job["status"] == "COMPLETED" for job in jobs.json())
        csv_response = client.get(f"/api/exports/{job_id}/csv")
        assert csv_response.status_code == 200
        assert csv_response.text.startswith("docket_number,court_type,county,filing_date")
        assert "MD-01-CR-0000123-2025" in csv_response.text
    api_module.engine.dispose()
