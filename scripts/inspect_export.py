import csv
import sys

rows = list(csv.DictReader(open(sys.argv[1], encoding="utf-8")))
print("rows:", len(rows))
bad = [r for r in rows if "Recent entries" in r["charge_description"]]
print("bleed rows:", len(bad))
for r in rows:
    print(
        f"{r['docket_number']:24} #{r['charge_number']:>2} "
        f"{r['charge_description'][:56]:56} | {r['charge_disposition']:>8} | {r['defendant_race']}"
    )
