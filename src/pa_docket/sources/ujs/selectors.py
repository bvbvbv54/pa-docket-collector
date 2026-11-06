"""Observed selectors belong here only after direct live inspection.

The UJS page is JavaScript-driven. Until the public search flow is observed
from an authorized network, every locator remains explicitly undiscovered.
"""

DISCOVERY_REQUIRED = "DISCOVERY_REQUIRED"

SEARCH_MODE_SELECTOR = 'select[title="Search By"]'
DATE_FIELD_OPTION = "DateFiled"
DATE_FROM_INPUT = 'input[name="FiledStartDate"]'
DATE_TO_INPUT = 'input[name="FiledEndDate"]'
COUNTY_SELECTOR = 'select[title="County"]'
ADVANCED_SEARCH = 'input[name="AdvanceSearch"]'
COURT_TYPE_SELECTOR = None
DOCKET_TYPE_SELECTOR = None
SEARCH_BUTTON = "#btnSearch"
RESET_BUTTON = "#btnReset"
# Confirmed 2026-11-06 in ujs_access_20261106_144611.json and screenshot.
RESULT_ROWS = None
DOCKET_SHEET_LINK = None
NEXT_PAGE_CONTROL = None

# Date form and advanced county verified in ujs_workflow_20261106_145130.json
# and ujs_workflow_20261106_145205.json. HTML date inputs accept YYYY-MM-DD.
