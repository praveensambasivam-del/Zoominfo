# ZoomInfo SDR/BDR headcount

Counts US SDR/BDR-type contacts per company using the ZoomInfo contact search API.

## What it does

For each of the companies in `data/companies.csv` (keyed on `ZoomInfo Company ID`),
it issues one `POST /search/contact` per job title:

- SDR
- BDR
- Sales Development Representative
- Business Development Representative
- Sales Development Executive
- Business Development Executive

Each search is anchored to the United States via `locationSearchType=PersonOrHQ` — a
contact counts if they are US-based **or** their company is US-headquartered, so
contacts sitting outside the US at a US company are still included. Results are
paginated to completion. Person IDs
are pooled across the six titles and de-duplicated, so a contact whose title matches
more than one term is counted once.

## Run

```sh
export ZI_USERNAME='...'
export ZI_PASSWORD='...'          # or: ZI_CLIENT_ID + ZI_PRIVATE_KEY for PKI auth
python3 src/zi_sdr_headcount.py
```

Useful flags:

| flag | default | meaning |
| --- | --- | --- |
| `--limit N` | 0 (all) | process only the first N companies — use for a smoke test |
| `--location-search-type` | `PersonOrHQ` | matches a contact located in the US **or** working for a US-headquartered company. Use `Person` to require the contact themselves to be US-based |
| `--country` | `United States` | pass `--country ""` to drop the location filter entirely |
| `--workers` / `--rps` | 4 / 8.0 | concurrency and request-rate cap |

## Output

- `out/sdr_bdr_headcount.csv` — `Company Name, Company ID, SDR/BDR Headcount`
- `out/detail_by_title.csv` — per-title match counts and any per-search error
- `out/checkpoint.jsonl` — every completed search; the run resumes from it

The run is resumable: re-running skips searches that already succeeded and retries
the ones that failed.
