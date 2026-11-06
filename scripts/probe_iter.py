"""Iterate one partition through the collector's own generator."""
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
        async for result in source.iter_partitions(day, day, [county], max_partitions=1):
            print("rows      :", len(result.rows))
            print("criminal  :", len(result.criminal_rows))
            print("status    :", result.coverage_status)
            print("notice    :", result.truncated_notice)
            print("errors    :", result.errors[:2])
            print("pages     :", result.pages_visited)
            print("first     :", result.rows[0].docket_number if result.rows else "-")
    finally:
        await source.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1], date.fromisoformat(sys.argv[2]))))
