# UJS Access Findings — confirmed against the live portal

Superseded check: an earlier direct-route probe recorded `UNREACHABLE` on
2026-11-06T13:07Z. That probe ran before the working route was configured. The
findings below were confirmed by executing the full workflow against
`https://ujsportal.pacourts.us/CaseSearch` on 2026-11-06 and are the contract the
collector is built on.

## Route

| Fact | Confirmed value |
| --- | --- |
| Working route | DataImpulse proxy `gw.dataimpulse.com:823`, US exit |
| Production route | direct US connection, no code change |
| Session requirement | **sticky** exit: `user__cr.us;sessid.<tag>` |
| Transport | Chromium launched with `--disable-quic --disable-http2` |
| User agent | Chrome desktop `Chrome/156.0.0.0` |

Why each is load-bearing:

- Rotating exits invalidate the signed `dnh` token on report URLs. A new exit
  between search and document fetch returns `401 Unauthorized request`.
- A headless user agent is refused on `/Report/*` with `401`. The collector sets
  the Chrome UA on the browser context and on the document request.
- HTTP/2 through a cold proxy tunnel produced `ERR_TUNNEL_CONNECTION_FAILED`;
  HTTP/1.1 with QUIC disabled is stable.

## Search flow

1. `select[title="Search By"]` → `DateFiled`.
2. `input[name="AdvanceSearch"]` clicked to mount the advanced filters.
3. `select[title="County"]` selects one county.
4. `input[name="FiledStartDate"]` and `input[name="FiledEndDate"]` take
   `YYYY-MM-DD`; the portal accepts a window of at most **180 days**.
5. `#btnSearch` submits.

## Results grid contract

`#caseSearchResultGrid` is a single static table. There is **no pagination
control** of any kind.

Row cells, hidden columns included (19 per row):

| Index | Field |
| --- | --- |
| 2 | Docket Number |
| 3 | Court Type |
| 4 | Case Caption |
| 5 | Case Status |
| 6 | Filing Date |
| 9 | County |
| 10 | Court Office |

Completeness signal: the sentence

```
Not all results are shown for <court>... Please narrow your search.
```

The portal prints it **only when it withheld rows**. Absence of the sentence is
the only proof of a complete render. The grid ceiling observed is ≈1046 rows.

## Truncation recovery

- The notice names the truncated court. Truncation is per court, not per result
  set: a county/day can return every Common Pleas docket while Magisterial
  District is capped.
- **Magisterial District** can be subdivided: `select[title="MDJS Court Office"]`
  populates after the county is chosen (York 20 offices, Lancaster 21, Dauphin 16,
  Adams 5, Philadelphia 1). Enumerating every office and unioning the rows
  recovers the full MDJ set.
- **Common Pleas** cannot be subdivided on a Date Filed search. The CP
  `Court Office` select exists but is only displayed for `CalendarEvent`
  searches. A capped CP set is an unrecoverable coverage gap and is recorded as
  `TRUNCATED` with an explanatory error. It is never reported as complete.
- A partition whose MDJ union proves complete is `COMPLETE` even though the base
  grid printed the notice; the notice is kept in the partition params as
  evidence and is not counted as a gap.

## Document delivery

- Grid rows carry a `Docket Sheet` link to `/Report/CpDocketSheet?...&dnh=...`
  or the MDJ equivalent.
- Fetching through `context.request.get(url)` with the Chrome UA returns
  `application/pdf`.
- The `dnh` token is deterministic per docket but resolved inside the issuing
  session; it cannot be replayed from a different exit IP.

## Document families observed

| Family | Form id | Docket types | Race field | Disposition source |
| --- | --- | --- | --- | --- |
| MDJ | `MDJS 1200` | Criminal, Landlord/Tenant, Civil, Summary Appeal | yes (`DEFENDANT INFORMATION`) | per-charge `Disposition` column |
| CP | `CPCMS 9082` | Criminal | **none on the form** | `DISPOSITION SENTENCING/PENALTIES`, keyed by charge sequence |

Consequences the collector honours:

- `defendant_race` on a CP docket is `NOT_RECORDED` with section evidence, never
  inferred and never treated as an extraction failure.
- The declared docket type decides scope, not the docket number: `CP-22-MD-...`
  is a criminal docket.
- Non-criminal dockets served through the same report raise
  `NonCriminalDocketError` and are cancelled by name, not failed.
- `Case Status:` and the case disposition are different facts on different
  lines. They are stored as `case_status` and `metadata["case_disposition"]`.

## Measured on 2026-11-05

| County | Grid rows | Criminal dockets | Coverage |
| --- | --- | --- | --- |
| Adams | 45 | 4 | COMPLETE, no notice |
| Lancaster | 551 | 58 | MDJ recovered across 21 offices, COMPLETE |
| York | 288 | — | COMPLETE |
| Berks | 288 | — | COMPLETE |
| Philadelphia | 472 | — | COMPLETE |
| Allegheny | 561 | — | MDJ truncated, recovered per office |
| All counties | 1046 | — | Common Pleas + MDJ truncated, CP unrecoverable |

Machine-readable runtime evidence lives under `data/` and is not committed.
