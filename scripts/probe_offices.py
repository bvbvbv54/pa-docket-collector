"""Walk one truncated county/day exactly as the collector does, counting both sides."""
from __future__ import annotations

import asyncio
import sys
from datetime import date

from pa_docket.config import Settings
from pa_docket.sources.ujs.source import UJSPortalSource


async def main(county: str, day: date) -> int:
    settings = Settings.from_env()
    source = UJSPortalSource(settings)
    await source.open()
    try:
        base = await source.search_partition(day, county)
        print(f"base rows={len(base.rows)} status={base.coverage_status} notice={base.truncated_notice}")
        offices = await source.court_offices(county)
        print(f"offices={len(offices)}")
        union: dict[str, int] = {}
        total = 0
        for office in offices:
            result = await source.search_partition(day, county, office)
            total += len(result.rows)
            for row in result.rows:
                union.setdefault(row.docket_number, 0)
            print(
                f"  {office:14} rows={len(result.rows):4} status={result.coverage_status:10}"
                f" notice={'-' if not result.truncated else result.truncated_notice[:40]}"
            )
            if result.errors:
                print(f"      errors={result.errors[0][:120]}")
        print(f"per-office sum={total} union={len(union)}")
    finally:
        await source.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1], date.fromisoformat(sys.argv[2]))))
