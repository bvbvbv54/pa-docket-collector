"""Field-by-field proof against the PDFs actually stored by a collection job.

Reports, for every requested client field, how many extracted charges carry it
and how many explicitly record that the document does not. Nothing is inferred:
a field the report never states is counted as NOT_RECORDED or MISSING.
"""
from __future__ import annotations

import json
import sys
from collections import Counter

from sqlalchemy import select

from pa_docket.config import Settings
from pa_docket.db.models import ExtractionResult
from pa_docket.db.session import make_session_factory

FIELDS = (
    "docket_number",
    "defendant_name",
    "charge_description",
    "charge_statute",
    "offense_date",
    "arresting_officer",
    "defendant_race",
    "charge_disposition",
    "disposition_date",
    "case_status",
    "case_disposition",
)


def _state(value: dict | None) -> str:
    if not value:
        return "ABSENT"
    return str(value.get("presence_status") or "ABSENT")


def main(job_id: str) -> int:
    settings = Settings.from_env()
    _engine, session_factory = make_session_factory(settings)
    with session_factory() as session:
        records = [
            result.result_json
            for result in session.scalars(
                select(ExtractionResult)
                .where(ExtractionResult.job_id == job_id)
                .order_by(ExtractionResult.id)
            )
        ]
    if not records:
        print(json.dumps({"job_id": job_id, "error": "no extraction results"}))
        return 1

    counts = {field: Counter() for field in FIELDS}
    chargers = 0
    for record in records:
        counts["docket_number"][_state(record.get("docket_number"))] += 1
        counts["case_status"][_state(record.get("case_status"))] += 1
        counts["case_disposition"][
            _state((record.get("metadata") or {}).get("case_disposition"))
        ] += 1
        defendant = (record.get("defendants") or [{}])[0]
        counts["defendant_name"][_state(defendant.get("name"))] += 1
        counts["defendant_race"][_state(defendant.get("race"))] += 1
        for charge in record.get("charges", []):
            chargers += 1
            for field, key in (
                ("charge_description", "description"),
                ("charge_statute", "statute"),
                ("offense_date", "offense_date"),
                ("arresting_officer", "arresting_officer"),
                ("charge_disposition", "disposition"),
                ("disposition_date", "disposition_date"),
            ):
                counts[field][_state(charge.get(key))] += 1

    print(f"job_id={job_id}")
    print(f"records={len(records)} charge_rows={chargers}")
    print()
    print(f"{'field':22} {'PRESENT':>8} {'NOT_RECORDED':>13} {'MISSING':>8} {'UNVERIFIED':>11}")
    for field in FIELDS:
        row = counts[field]
        print(
            f"{field:22} {row.get('PRESENT', 0):>8} {row.get('NOT_RECORDED', 0):>13} "
            f"{row.get('MISSING', 0):>8} {row.get('UNVERIFIED', 0):>11}"
        )

    print()
    print("Per docket:")
    for record in records:
        defendant = (record.get("defendants") or [{}])[0]
        name = (defendant.get("name") or {}).get("normalized_value")
        race = defendant.get("race") or {}
        print(
            f"  {record['docket_number']['normalized_value']:24} "
            f"family={record.get('metadata', {}).get('document_family'):3} "
            f"status={record.get('validation_status'):12} "
            f"charges={len(record.get('charges', [])):>2} "
            f"race={race.get('normalized_value') or race.get('presence_status')} "
            f"| {name}"
        )
        for charge in record.get("charges", []):
            officer = charge["arresting_officer"]
            disposition = charge["disposition"]
            print(
                f"      #{charge['charge_number']['normalized_value']:<3} "
                f"{(charge['offense_date'].get('normalized_value') or '-'):11} "
                f"{(officer.get('normalized_value') or officer.get('presence_status'))[:26]:26} "
                f"disp={(disposition.get('normalized_value') or disposition.get('presence_status')):12} "
                f"{(charge['description'].get('normalized_value') or '')[:44]}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
