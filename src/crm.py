#!/usr/bin/env python3
"""Track which CRM vendor each company matched, and merge it into the deliverable.

Usage: crm.py batch <n>            # print company ids for batch n (50 per batch)
       crm.py add <vendor> <id>... # record companies that matched this CRM vendor
       crm.py build                # regenerate the CSV with the CRM column
       crm.py status
"""
import csv, json, sys
from pathlib import Path

ORDER = Path("out/company_order.json")
HITS = Path("out/crm_hits.json")
COUNTS = Path("out/mcp_counts.csv")
OUT = Path("out/sdr_bdr_headcount.csv")
BATCH = 50

# Most-specific first: a company matching several gets the first as its primary.
PRECEDENCE = ["Salesforce", "HubSpot", "Microsoft Dynamics", "NetSuite CRM",
              "SugarCRM", "Zoho CRM", "Pipedrive", "Freshsales", "Close",
              "Copper", "Insightly", "Keap", "Zendesk Sell"]


def companies():
    return json.loads(ORDER.read_text())


def hits():
    return json.loads(HITS.read_text()) if HITS.exists() else {}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "batch":
        n = int(sys.argv[2])
        ids = [int(c["id"]) for c in companies()][n * BATCH:(n + 1) * BATCH]
        print(json.dumps(ids))
    elif cmd == "add":
        vendor, ids = sys.argv[2], sys.argv[3:]
        d = hits()
        d.setdefault(vendor, [])
        d[vendor] = sorted(set(d[vendor]) | set(ids))
        HITS.write_text(json.dumps(d, indent=0, sort_keys=True))
        print(f"{vendor}: {len(d[vendor])} companies")
    elif cmd == "build":
        d = hits()
        by_company = {}
        for vendor, ids in d.items():
            for cid in ids:
                by_company.setdefault(cid, []).append(vendor)
        counts = {}
        for row in csv.reader(COUNTS.open()):
            if len(row) >= 2 and row[0] != "company_id":
                counts[row[0]] = row[1]
        with OUT.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Company Name", "Company ID", "Company Domain",
                        "SDR/BDR Headcount", "CRM", "All CRM Signals"])
            for c in companies():
                found = by_company.get(c["id"], [])
                found.sort(key=lambda v: PRECEDENCE.index(v)
                           if v in PRECEDENCE else len(PRECEDENCE))
                w.writerow([c["name"], c["id"], c.get("domain", ""),
                            counts.get(c["id"], ""),
                            found[0] if found else "Unknown",
                            "; ".join(found)])
        known = sum(1 for c in companies() if c["id"] in by_company)
        print(f"wrote {OUT}: {known}/{len(companies())} with a CRM identified")
        for v in PRECEDENCE:
            if d.get(v):
                print(f"  {v:20} {len(d[v])}")
    else:
        d = hits()
        print(f"vendors recorded: {len(d)}; "
              f"companies matched: {len({c for v in d.values() for c in v})}")


main()
