# UJS Access Findings

- Checked: `2026-11-06T13:07:39.954221+00:00`
- Result: **UNREACHABLE**
- HTTP status: `None`
- Final URL: `https://ujsportal.pacourts.us/CaseSearch`
- Inspection: `DIRECT_REQUEST_FAILED`
- Page title: `not observed`
- Screenshot: `not captured`
- Connection mode: `direct; proxy environment variables ignored`
- Search submitted: `no`
- Selectors verified: `no`
- Search flow validated: `no`
- PDF delivery validated: `no`

## Observed Controls

No search controls were confirmed in the observed response.

## Pagination and PDF Delivery

Neither behavior is validated by a landing-page visit. The script does not submit searches or retrieve case documents.

- Pagination controls visible on landing page: `0`
- Docket/PDF links visible on landing page: `0`

## Notes

The direct request failed; geographic blocking is unconfirmed.

Machine-readable findings: `data/ujs_access_findings.json` (local runtime output).
