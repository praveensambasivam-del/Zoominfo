#!/usr/bin/env python3
"""Append `company_id,count` results to the ledger; rebuild the deliverable CSV.

Usage:  record.py add "<id>:<count>" ...       # idempotent, last write wins
        record.py build                        # regenerate the output CSV
        record.py todo [N]                     # next N company ids still unqueried
        record.py status
"""
import csv, json, sys
from pathlib import Path

ORDER = Path("out/company_order.json")
LEDGER = Path("out/mcp_counts.csv")
OUT = Path("out/sdr_bdr_headcount.csv")


def companies():
    return json.loads(ORDER.read_text())


def ledger():
    d = {}
    if LEDGER.exists():
        for row in csv.reader(LEDGER.open()):
            if len(row) >= 2 and row[0] != "company_id":
                d[row[0]] = int(row[1])
    return d


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "add":
        d = ledger()
        for pair in sys.argv[2:]:
            cid, cnt = pair.split(":")
            d[cid.strip()] = int(cnt)
        with LEDGER.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["company_id", "count"])
            for k, v in d.items():
                w.writerow([k, v])
        print(f"ledger: {len(d)}/{len(companies())}")
    elif cmd == "build":
        d, rows = ledger(), []
        with OUT.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Company Name", "Company ID", "Company Domain",
                        "SDR/BDR Headcount"])
            for c in companies():
                w.writerow([c["name"], c["id"], c.get("domain", ""),
                            d.get(c["id"], "")])
                rows.append(d.get(c["id"]))
        got = [r for r in rows if r is not None]
        print(f"wrote {OUT}: {len(got)}/{len(rows)} populated, "
              f"total headcount {sum(got)}, nonzero companies "
              f"{sum(1 for r in got if r > 0)}")
    elif cmd == "todo":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 16
        d = ledger()
        rem = [c for c in companies() if c["id"] not in d]
        print(json.dumps([int(c["id"]) for c in rem[:n]]))
        print(f"# {len(rem)} remaining", file=sys.stderr)
    else:
        d = ledger()
        print(f"{len(d)}/{len(companies())} done")


main()
