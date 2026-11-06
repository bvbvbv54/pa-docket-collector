"""Locators and page-contract constants confirmed against the live portal.

Every value here was observed in a real browser session against
https://ujsportal.pacourts.us/CaseSearch on 2026-11-06 and is recorded in
docs/UJS_ACCESS_FINDINGS.md. Nothing in this file is a guess.
"""

DISCOVERY_REQUIRED = "DISCOVERY_REQUIRED"

# Landing form.
SEARCH_MODE_SELECTOR = 'select[title="Search By"]'
SEARCH_BUTTON = "#btnSearch"
RESET_BUTTON = "#btnReset"

# Date Filed mode.
DATE_FIELD_OPTION = "DateFiled"
DATE_FROM_INPUT = 'input[name="FiledStartDate"]'
DATE_TO_INPUT = 'input[name="FiledEndDate"]'
ADVANCED_SEARCH = 'input[name="AdvanceSearch"]'
COUNTY_SELECTOR = 'select[title="County"]'
# Populated per county; the only subdivision available on a Date Filed search.
COURT_OFFICE_SELECTOR = 'select[title="MDJS Court Office"]'

# Results grid: a single static table with no pagination control.
RESULT_GRID = "#caseSearchResultGrid"
GRID_ROWS = "#caseSearchResultGrid tbody tr"
# Column order as rendered (hidden cells included):
# 0 row key, 1 selection, 2 Docket Number, 3 Court Type, 4 Case Caption,
# 5 Case Status, 6 Filing Date, 7 Primary Participant(s), 8 Date Of Birth(s),
# 9 County, 10 Court Office, 11 OTN, 12 Complaint #, 13 Incident #, ...
DOCKET_COLUMN_INDEX = 2
COURT_TYPE_COLUMN_INDEX = 3
FILING_DATE_COLUMN_INDEX = 6
COUNTY_COLUMN_INDEX = 9
COURT_OFFICE_COLUMN_INDEX = 10
DOCKET_SHEET_LINK = "Docket Sheet"
COURT_SUMMARY_LINK = "Court Summary"

# The portal has no "next page". It renders every row it intends to render and
# prints this sentence when it withheld some. The sentence names the court.
TRUNCATION_NOTICE = "Not all results are shown"

# Document delivery.
DOCKET_PDF_PATH = "/Report/"
PDF_CONTENT_TYPE = "application/pdf"
CASE_SEARCH_URL = "https://ujsportal.pacourts.us/CaseSearch"
