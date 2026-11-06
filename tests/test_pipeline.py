from __future__ import annotations

import pymupdf
from openpyxl import load_workbook
from sqlalchemy import func, select

from pa_docket.config import Settings
from pa_docket.db.models import (
    ChargeRow,
    CollectionJob,
    Docket,
    ExtractionResult,
    SourceDocument,
    TaskQueue,
)
from pa_docket.db.session import init_db, make_session_factory
from pa_docket.exports.csv_exporter import COLUMNS, csv_bytes, iter_export_rows
from pa_docket.exports.xlsx_exporter import write_xlsx
from pa_docket.services.jobs import create_fixture_job
from pa_docket.sources.fixture.generator import (
    SyntheticCharge,
    SyntheticDefendant,
    SyntheticDocket,
    render_pdf,
    sample_cases,
)
from pa_docket.workers.main import run_worker


def setup_db(tmp_path, *, max_pdfs=500):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'pipeline.sqlite3').as_posix()}",
        storage_dir=tmp_path / "store",
        max_pdfs_per_job=max_pdfs,
    )
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    engine, factory = make_session_factory(settings)
    init_db(engine)
    return settings, engine, factory


def test_fixture_pipeline_is_idempotent_and_exports_actual_values(tmp_path):
    settings, engine, factory = setup_db(tmp_path)
    case = sample_cases()[1]
    job_id = create_fixture_job(factory, settings, [(case, render_pdf(case))], {"source": "fixture"})
    run_worker(factory, settings, concurrency=2)
    with factory() as session:
        job = session.get(CollectionJob, job_id)
        assert job.status == "COMPLETED"
        assert job.completed_tasks == job.total_tasks == 1
        docket = session.scalars(select(Docket).where(Docket.docket_number == case.docket_number)).one()
        assert session.scalar(select(func.count()).select_from(ChargeRow).where(ChargeRow.docket_id == docket.id)) == 3
        rows = list(iter_export_rows(session, job_id))
        assert len(rows) == 3
        assert rows[0][0] == case.docket_number
        assert rows[0][4] == "Casey Sample"
        assert rows[0][5] == "Officer Pat Demo"
        assert rows[0][6] == ""
        assert rows[0][8] == "Simple assault"
        assert rows[0][10] == "2025-01-01"
        assert rows[0][11] == "Guilty"
        assert rows[1][5] == ""
        assert rows[1][6] == ""

        csv_data = csv_bytes(rows).decode("utf-8")
        assert csv_data.splitlines()[0].split(",") == COLUMNS
        assert "Simple assault" in csv_data

        xlsx_path = tmp_path / "out.xlsx"
        assert write_xlsx(session, job_id, xlsx_path) == 3
    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    sheet = workbook["Dockets"]
    assert list(next(sheet.iter_rows(values_only=True))) == COLUMNS
    excel_rows = list(sheet.iter_rows(min_row=2, values_only=True))
    assert len(excel_rows) == 3
    assert excel_rows[0][8] == "Simple assault"


def test_same_pdf_rerun_does_not_duplicate_docket_charges_or_document(tmp_path):
    settings, engine, factory = setup_db(tmp_path)
    case = sample_cases()[0]
    data = render_pdf(case)
    first = create_fixture_job(factory, settings, [(case, data)], {"run": 1})
    run_worker(factory, settings, concurrency=1)
    second = create_fixture_job(factory, settings, [(case, data)], {"run": 2})
    run_worker(factory, settings, concurrency=1)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Docket)) == 1
        assert session.scalar(select(func.count()).select_from(ChargeRow)) == 1
        assert session.scalar(select(func.count()).select_from(SourceDocument)) == 1
        assert session.scalar(select(func.count()).select_from(ExtractionResult)) == 2
        assert session.get(CollectionJob, first).status == "COMPLETED"
        assert session.get(CollectionJob, second).status == "COMPLETED"


def test_modified_pdf_creates_a_new_document_version_for_same_docket(tmp_path):
    settings, engine, factory = setup_db(tmp_path)
    case = sample_cases()[0]
    first_data = render_pdf(case)
    pdf = pymupdf.open(stream=first_data, filetype="pdf")
    pdf[0].insert_text((48, 780), "New version", fontsize=8)
    second_data = pdf.tobytes()
    pdf.close()
    for i, data in enumerate([first_data, second_data]):
        job = create_fixture_job(factory, settings, [(case, data)], {"version": i})
        run_worker(factory, settings, concurrency=1)
        with factory() as session:
            assert session.get(CollectionJob, job).status == "COMPLETED"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Docket)) == 1
        assert session.scalar(select(func.count()).select_from(SourceDocument)) == 2


def test_100_synthetic_documents_processed_by_concurrent_workers(tmp_path):
    settings, engine, factory = setup_db(tmp_path, max_pdfs=200)
    documents = []
    for i in range(110):
        docket_number = f"MD-01-CR-{i:07d}-2025"
        case = SyntheticDocket(
            docket_number,
            filing_date="01/15/2025",
            defendants=[SyntheticDefendant(f"Synthetic Person {i}")],
            charges=[SyntheticCharge("1", f"Synthetic charge {i}", "01/01/2025", "Pending")],
        )
        documents.append((case, render_pdf(case)))
    job_id = create_fixture_job(factory, settings, documents, {"source": "scale-test"})
    count = run_worker(factory, settings, concurrency=4)
    assert count >= 110
    with factory() as session:
        job = session.get(CollectionJob, job_id)
        assert job.status == "COMPLETED"
        assert job.completed_tasks == 110
        assert job.failed_tasks == 0
        assert session.scalar(select(func.count()).select_from(Docket)) == 110
        assert session.scalar(select(func.count()).select_from(ChargeRow)) == 110
        assert session.scalar(select(func.count()).select_from(TaskQueue).where(TaskQueue.status == "COMPLETED")) == 110
