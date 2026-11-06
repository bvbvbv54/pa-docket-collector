from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from openpyxl import Workbook

from pa_docket.exports.csv_exporter import COLUMNS, iter_export_rows


def write_rows(
    rows: Iterable[list[str]],
    columns: list[str],
    output: Path,
    *,
    sheet_name: str = "Charges",
) -> int:
    """Write prepared string rows; every cell stays text, never a formula."""
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet(sheet_name)
    sheet.append(columns)
    count = 0
    for row in rows:
        sheet.append([str(cell) for cell in row])
        count += 1
    workbook.save(output)
    return count


def write_xlsx(session, job_id: str, output: Path) -> int:
    return write_rows(
        iter_export_rows(session, job_id),
        COLUMNS,
        output,
        sheet_name="Dockets",
    )
