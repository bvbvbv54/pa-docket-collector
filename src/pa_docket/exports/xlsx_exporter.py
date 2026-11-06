from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from pa_docket.exports.csv_exporter import COLUMNS, iter_export_rows


def write_xlsx(session, job_id: str, output: Path) -> int:
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("Dockets")
    sheet.append(COLUMNS)
    count = 0
    for row in iter_export_rows(session, job_id):
        # Force text format to prevent spreadsheet formula evaluation.
        sheet.append([str(cell) for cell in row])
        count += 1
    workbook.save(output)
    return count
