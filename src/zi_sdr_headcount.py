#!/usr/bin/env python3
"""Count US SDR/BDR-type contacts per company via the ZoomInfo contact search API.

For every company in the input CSV, one /search/contact request is issued per job
title. Person IDs are collected across titles and de-duplicated, so a contact whose
title matches more than one search term is counted once.

Auth: username/password, or PKI (username + client id + private key).
Resumable: completed (companyId, title) pairs are checkpointed to a JSONL file and
skipped on a re-run.
"""

import argparse
import csv
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

AUTH_URL = "https://api.zoominfo.com/authenticate"
SEARCH_URL = "https://api.zoominfo.com/search/contact"

JOB_TITLES = [
    "SDR",
    "BDR",
    "Sales Development Representative",
    "Business Development Representative",
    "Sales Development Executive",
    "Business Development Executive",
]

RPP = 100          # records per page (API max)
MAX_PAGES = 50     # hard stop: 5000 contacts for one company+title


class Auth:
    """Holds a ZoomInfo JWT and refreshes it before the 1-hour expiry."""

    def __init__(self, username, password=None, client_id=None, private_key=None):
        self.username = username
        self.password = password
        self.client_id = client_id
        self.private_key = private_key
        self._token = None
        self._issued = 0.0
        self._lock = threading.Lock()

    def _fetch(self):
        if self.password:
            body = {"username": self.username, "password": self.password}
            r = requests.post(AUTH_URL, json=body, timeout=60)
        else:
            from zi_pki import generate_pki_jwt  # local helper, needs PyJWT+cryptography
            client_jwt = generate_pki_jwt(self.username, self.client_id, self.private_key)
            r = requests.post(
                AUTH_URL,
                headers={"Authorization": f"Bearer {client_jwt}"},
                json={},
                timeout=60,
            )
        r.raise_for_status()
        token = r.json().get("jwt")
        if not token:
            raise RuntimeError(f"no jwt in auth response: {r.text[:300]}")
        return token

    def token(self, force=False):
        with self._lock:
            # refresh at 50 minutes; ZoomInfo tokens live for 60
            if force or self._token is None or time.time() - self._issued > 50 * 60:
                self._token = self._fetch()
                self._issued = time.time()
            return self._token


class Throttle:
    """Simple token-bucket limiter shared across worker threads."""

    def __init__(self, per_second):
        self.interval = 1.0 / per_second if per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._next = time.monotonic()

    def wait(self):
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            if self._next < now:
                self._next = now
            delay = self._next - now
            self._next += self.interval
        if delay > 0:
            time.sleep(delay)


def search_page(session, auth, throttle, payload, attempt_limit=6):
    """POST one page of /search/contact, retrying on throttling and transient errors."""
    for attempt in range(attempt_limit):
        throttle.wait()
        headers = {"Authorization": f"Bearer {auth.token()}"}
        try:
            r = session.post(SEARCH_URL, headers=headers, json=payload, timeout=90)
        except requests.RequestException as exc:
            if attempt == attempt_limit - 1:
                raise
            time.sleep(min(60, 2 ** attempt) + random.random())
            continue

        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            # ZoomInfo returns 404 for a search that matched nothing
            return {"totalResults": 0, "data": []}
        if r.status_code in (401, 403):
            auth.token(force=True)
        elif r.status_code not in (429, 500, 502, 503, 504):
            raise RuntimeError(f"HTTP {r.status_code} for {payload}: {r.text[:300]}")

        if attempt == attempt_limit - 1:
            raise RuntimeError(f"giving up after {attempt_limit} tries: "
                               f"HTTP {r.status_code} {r.text[:300]}")
        retry_after = r.headers.get("Retry-After")
        backoff = float(retry_after) if retry_after and retry_after.isdigit() \
            else min(60, 2 ** attempt)
        time.sleep(backoff + random.random())
    raise RuntimeError("unreachable")


def search_title(session, auth, throttle, company_id, title, args):
    """All US contacts at one company matching one job title. Returns (ids, total)."""
    person_ids = []
    page = 1
    reported_total = None
    while page <= MAX_PAGES:
        payload = {
            "companyId": str(company_id),
            "jobTitle": title,
            "country": args.country,
            "rpp": RPP,
            "page": page,
        }
        if args.location_search_type:
            payload["locationSearchType"] = args.location_search_type
        body = search_page(session, auth, throttle, payload)
        if reported_total is None:
            reported_total = int(body.get("totalResults") or 0)
        rows = body.get("data") or []
        for row in rows:
            pid = row.get("id") or row.get("personId")
            if pid is not None:
                person_ids.append(str(pid))
        if len(rows) < RPP or len(person_ids) >= (reported_total or 0):
            break
        page += 1
    return person_ids, (reported_total or 0)


def load_companies(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    companies = []
    seen = set()
    for row in rows:
        cid = (row.get("ZoomInfo Company ID") or "").strip().strip('"')
        name = (row.get("Company Name") or "").strip().strip('"')
        if not cid or cid in seen:
            continue
        seen.add(cid)
        companies.append({"id": cid, "name": name})
    return companies


def load_checkpoint(path):
    done = {}
    if not Path(path).exists():
        return done
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            done[(rec["company_id"], rec["title"])] = rec
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/companies.csv")
    ap.add_argument("--out", default="out/sdr_bdr_headcount.csv")
    ap.add_argument("--detail", default="out/detail_by_title.csv")
    ap.add_argument("--checkpoint", default="out/checkpoint.jsonl")
    ap.add_argument("--country", default="United States")
    ap.add_argument("--location-search-type", default="Person",
                    help="Person | HQ | PersonOrHQ | PersonAndHQ | PersonThenHQ. "
                         "'Person' counts contacts whose own location is in the US.")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--rps", type=float, default=8.0, help="requests per second cap")
    ap.add_argument("--limit", type=int, default=0, help="only process first N companies")
    args = ap.parse_args()

    username = os.environ.get("ZI_USERNAME")
    password = os.environ.get("ZI_PASSWORD")
    client_id = os.environ.get("ZI_CLIENT_ID")
    private_key = os.environ.get("ZI_PRIVATE_KEY")
    if not username or not (password or (client_id and private_key)):
        sys.exit("set ZI_USERNAME and either ZI_PASSWORD, or ZI_CLIENT_ID + ZI_PRIVATE_KEY")

    auth = Auth(username, password, client_id, private_key)
    auth.token()  # fail fast on bad credentials

    companies = load_companies(args.input)
    if args.limit:
        companies = companies[: args.limit]
    done = load_checkpoint(args.checkpoint)
    # a checkpointed failure is not "done" — a re-run retries it
    tasks = [(c, t) for c in companies for t in JOB_TITLES
             if (done.get((c["id"], t)) or {}).get("error") is not None
             or (c["id"], t) not in done]
    print(f"{len(companies)} companies x {len(JOB_TITLES)} titles = "
          f"{len(companies) * len(JOB_TITLES)} calls; "
          f"{len(companies) * len(JOB_TITLES) - len(tasks)} already done, "
          f"{len(tasks)} to run", flush=True)

    Path(args.checkpoint).parent.mkdir(parents=True, exist_ok=True)
    throttle = Throttle(args.rps)
    session = requests.Session()
    ck_lock = threading.Lock()
    counter = {"n": 0, "err": 0}
    ck_fh = open(args.checkpoint, "a", encoding="utf-8")

    def run(task):
        company, title = task
        try:
            ids, total = search_title(session, auth, throttle, company["id"], title, args)
            rec = {"company_id": company["id"], "company_name": company["name"],
                   "title": title, "person_ids": ids, "total_results": total}
        except Exception as exc:  # recorded, not fatal: the run continues
            rec = {"company_id": company["id"], "company_name": company["name"],
                   "title": title, "person_ids": [], "total_results": 0,
                   "error": f"{type(exc).__name__}: {exc}"}
        with ck_lock:
            ck_fh.write(json.dumps(rec) + "\n")
            ck_fh.flush()
            counter["n"] += 1
            if "error" in rec:
                counter["err"] += 1
            if counter["n"] % 100 == 0:
                print(f"  {counter['n']}/{len(tasks)} calls "
                      f"({counter['err']} errors)", flush=True)
        return rec

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(run, tasks))
    ck_fh.close()
    print(f"done: {counter['n']} calls, {counter['err']} errors", flush=True)

    write_outputs(companies, load_checkpoint(args.checkpoint), args)


def write_outputs(companies, done, args):
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    errors = 0
    with open(args.out, "w", newline="", encoding="utf-8") as fh_sum, \
         open(args.detail, "w", newline="", encoding="utf-8") as fh_det:
        summary = csv.writer(fh_sum)
        summary.writerow(["Company Name", "Company ID", "SDR/BDR Headcount"])
        detail = csv.writer(fh_det)
        detail.writerow(["Company Name", "Company ID", "Job Title",
                         "Matches", "Error"])
        for company in companies:
            unique_ids = set()
            for title in JOB_TITLES:
                rec = done.get((company["id"], title))
                if rec is None:
                    detail.writerow([company["name"], company["id"], title, "", "not run"])
                    continue
                if rec.get("error"):
                    errors += 1
                ids = rec.get("person_ids") or []
                unique_ids.update(ids)
                detail.writerow([company["name"], company["id"], title,
                                 len(ids), rec.get("error", "")])
            summary.writerow([company["name"], company["id"], len(unique_ids)])
    print(f"wrote {args.out} and {args.detail}"
          + (f" ({errors} failed searches — re-run to retry)" if errors else ""))


if __name__ == "__main__":
    main()
