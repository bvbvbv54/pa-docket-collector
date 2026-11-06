from pa_docket.exports.csv_exporter import _safe_cell, rows_for_record


def test_spreadsheet_formula_prefix_is_escaped():
    assert _safe_cell("=1+1") == "'=1+1"
    assert _safe_cell("@SUM(A1:A2)") == "'@SUM(A1:A2)"
    assert _safe_cell("normal text") == "normal text"


def test_csv_row_uses_charge_defendant_association_and_nulls():
    record = {
        "docket_number": {"normalized_value": "MD-1", "raw_value": "MD-1"},
        "court": {"normalized_value": "MDC"},
        "county": {"normalized_value": "Dauphin"},
        "filing_date": {"normalized_value": "2025-01-01"},
        "case_status": {"raw_value": "Pending"},
        "defendants": [
            {
                "index": 1,
                "name": {"normalized_value": "=2+2"},
                "race": {"normalized_value": None, "presence_status": "NOT_RECORDED"},
            }
        ],
        "charges": [
            {
                "defendant_index": 1,
                "charge_number": {"normalized_value": "1"},
                "description": {"normalized_value": "Theft"},
                "statute": {"normalized_value": "18 Pa.C.S. § 3921"},
                "offense_date": {"normalized_value": "2025-01-01"},
                "arresting_officer": {"normalized_value": None, "presence_status": "UNVERIFIED"},
                "disposition": {"raw_value": "Pending", "normalized_value": "pending"},
                "disposition_date": {"normalized_value": None},
            }
        ],
        "validation_status": "VALIDATED",
    }
    rows = list(rows_for_record(record))
    assert len(rows) == 1
    assert rows[0][4] == "'=2+2"
    assert rows[0][5] == ""
    assert rows[0][6] == ""
    assert rows[0][11] == "Pending"
