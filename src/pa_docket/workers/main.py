from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from pa_docket.workers.tasks import process_one


def run_worker(session_factory, settings, *, concurrency: int = 1, watch: bool = False, once: bool = False) -> int:
    concurrency = max(1, min(int(concurrency), 10))
    completed_calls = 0
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="pa-docket") as pool:
        while True:
            futures = [
                pool.submit(process_one, session_factory, settings, f"worker-{i}-{id(pool)}")
                for i in range(1 if once else concurrency)
            ]
            outcomes = [future.result() for future in futures]
            completed_calls += sum(outcomes)
            if once:
                return completed_calls
            if any(outcomes):
                continue
            if not watch:
                return completed_calls
            time.sleep(1)
