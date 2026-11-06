# Source access policy

Live UJS collection remains disabled until an operator verifies the permitted public workflow and completes a limited acceptance run. The official portal help describes Case Search for public docket information, and its guide depicts Date Filed start/end search fields and clicking a Docket Sheet button. The available help does not settle pagination limits, date-only behavior for the required criminal docket types, PDF response mechanisms, session requirements, or an authorized request rate.

Before enabling a live source, record the observed labels and available options, test one approved public search manually, verify result pagination and PDF delivery using the visible UI, and establish a conservative request rate. Do not bypass access controls, CAPTCHA, TLS, robots restrictions, sealed/expunged/limited-access records, or portal security. Record incomplete pagination and inaccessible records as coverage gaps. Do not use credentials or proxy settings copied from screenshots or chat attachments; enter client-owned secrets through environment configuration and rotate any credential that was exposed.

The current live adapter uses direct connections only. It rejects proxy mode, checks reachability before any operation, and raises for search/document operations until the findings record confirms verified selectors, search behavior, and PDF delivery. Both live flags are required by configuration; enabling them alone does not complete implementation or validation. The access script never submits a search and never uses environment proxy settings.

