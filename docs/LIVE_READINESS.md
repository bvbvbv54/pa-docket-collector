# Live collection readiness

Live collection stays disabled until every item is completed and reviewed by the operator. A landing-page reachability result is not search authorization and does not validate a collection workflow.

- [ ] Portal reachable from the authorized deployment network over a direct, TLS-verified connection
- [ ] Public search mode validated for the intended filing-date query
- [ ] Search controls and selectors discovered by inspecting the public UI
- [ ] County, court, and docket-type requirements recorded
- [ ] Search results and every pagination page can be enumerated; incomplete coverage is detectable
- [ ] Public docket-sheet PDF delivery mechanism confirmed without accessing secure records
- [ ] Parser output compared field-by-field with authorized sample documents; only matching fields marked verified
- [ ] Request rate configured conservatively and kept at or below 10 requests per minute
- [ ] Client authorization documented for the intended collection and retention purpose
- [ ] Access policy and public-record restrictions reviewed
- [ ] Export access controls and retention/deletion workflow approved

`scripts/validate_ujs_access.py` performs one direct read-only landing-page check. It does not submit a search, enumerate results, retrieve a docket, or validate pagination/PDF delivery. `UJSPortalSource.search`, `get_docket`, and `get_document` remain gated until machine-readable findings mark selectors, search flow, and PDF delivery as validated and both live flags are explicitly enabled.

