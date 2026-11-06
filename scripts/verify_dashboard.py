"""Hit the running dashboard with real credentials and prove it serves stored records."""
from __future__ import annotations

import json
import sys

import httpx

from pa_docket.config import Settings

BASE = "http://127.0.0.1:8077"


def main(job_id: str) -> int:
    settings = Settings.from_env()
    with httpx.Client(base_url=BASE, follow_redirects=False, timeout=30) as client:
        health = client.get("/health")
        print("health:", health.status_code, health.json())

        anonymous = client.get("/api/jobs")
        print("anonymous /api/jobs:", anonymous.status_code)

        login = client.post(
            "/login",
            data={
                "username": settings.operator_username,
                "password": settings.operator_password,
            },
        )
        print("login:", login.status_code)

        dashboard = client.get("/")
        print("dashboard:", dashboard.status_code, "job links:", dashboard.text.count("/jobs/"))

        detail = client.get(f"/api/jobs/{job_id}")
        print("api detail:", detail.status_code)
        body = detail.json()
        print(
            "  dockets:", body["docket_count"],
            "charges:", body["charge_row_count"],
            "documents:", body["document_count"],
            "tasks:", body["tasks"],
        )
        print("  coverage:", [(c["county"], c["coverage_status"]) for c in body["coverage"]])
        first = body["dockets"][0]
        print("  first docket:", first["docket_number"], "|", first["defendant_name"],
              "| charges:", first["charge_count"], "| race:", first["race_presence"],
              "| case:", first["case_status"], "->", first["case_disposition"])

        page = client.get(f"/jobs/{job_id}")
        print("html job page:", page.status_code, "bytes:", len(page.text))
        for docket in body["dockets"]:
            assert docket["docket_number"] in page.text, docket["docket_number"]
        assert "Coverage" in page.text
        print("  every stored docket number appears in the page")

        csv_response = client.get(f"/api/exports/{job_id}/csv")
        lines = csv_response.text.splitlines()
        print("csv export:", csv_response.status_code, "rows:", len(lines) - 1)
        print("  header:", lines[0])
        print("  sample:", lines[1][:160])

        xlsx_response = client.get(f"/api/exports/{job_id}/xlsx")
        print(
            "xlsx export:", xlsx_response.status_code,
            "bytes:", len(xlsx_response.content),
            "magic:", xlsx_response.content[:2],
        )
        assert xlsx_response.content[:2] == b"PK"

        summary = {
            "dashboard": dashboard.status_code,
            "job_page": page.status_code,
            "dockets": body["docket_count"],
            "charge_rows": body["charge_row_count"],
            "csv_rows": len(lines) - 1,
            "xlsx_bytes": len(xlsx_response.content),
        }
        print()
        print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
