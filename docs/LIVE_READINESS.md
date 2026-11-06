# Live collection readiness

Live collection is gated by two environment flags and by a machine-readable
findings file. The gates exist so a deployment cannot start collecting because a
page loaded: they require the search flow, selectors and PDF delivery to have
been executed and recorded.

```
LIVE_COLLECTION_ENABLED=true
LIVE_ACCESS_APPROVED=true
NETWORK_MODE=direct            # production; proxy for development
```

`data/ujs_access_findings.json` must mark selectors, search flow and PDF delivery
as validated. `doctor` verifies both the flags and the findings file.

## Executed and confirmed

- [x] Portal reachable from the authorized network; page renders the search form
- [x] Date Filed search mode selected through the rendered control
- [x] `FiledStartDate` / `FiledEndDate` accept `YYYY-MM-DD`, 180-day ceiling
- [x] County filter applied; MDJ Court Office exposed after county selection
- [x] Results grid enumerated; absence of the truncation sentence proves a full render
- [x] MDJ truncation recovered by enumerating every court office and unioning rows
- [x] CP truncation detected and recorded as an unrecoverable gap
- [x] Docket-sheet PDFs retrieved through the issuing session (Chrome UA, sticky exit)
- [x] Parser output compared field by field against retrieved PDFs
- [x] Request rate capped at 10 per minute with a shared limiter
- [x] CSV and XLSX exports produced from stored `ExtractionResult` rows
- [x] Restart recovery: orphaned task leases re-claimed, no duplicate dockets or rows

## Operator decisions still required

- [ ] Client authorization documented for the intended collection and retention purpose
- [ ] Access policy and public-record restrictions reviewed for the deployment
- [ ] Export access controls and retention/deletion workflow approved

These are policy items, not code items. The collector records what it retrieved
and refuses to claim coverage it did not prove.

## What the gate protects

`UJSPortalSource.search`, `search_partition`, `iter_partitions` and `get_document`
raise `LiveAccessNotValidatedError` until both flags and the findings file are in
place. `check_access()` reports route state without performing a search.
