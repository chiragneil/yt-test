#!/usr/bin/env python3
"""Fill in emails and full details for a CSV made by apollo_people_export.py.

Sends the `id` column to Apollo's People Enrichment API (people/bulk_match),
10 people per request. Each matched person uses Apollo credits.

    export APOLLO_API_KEY=xxxxxxxx
    python3 apollo_enrich.py leads.csv --out leads_enriched.csv

Rows Apollo can't match are kept as they were. Uses only the standard library.
"""

import argparse
import csv
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request

BULK_MATCH_URL = "https://api.apollo.io/api/v1/people/bulk_match"
BATCH_SIZE = 10  # Apollo's maximum per bulk_match request

CSV_FIELDS = [
    "id", "first_name", "last_name", "name", "title", "seniority",
    "linkedin_url", "email", "email_status", "city", "state", "country",
    "organization_name", "organization_website", "organization_linkedin",
    "organization_employees", "organization_industry",
]


def post(api_key, body):
    req = urllib.request.Request(
        BULK_MATCH_URL,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "x-api-key": api_key,
        },
        method="POST",
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                wait = 2 ** (attempt + 1)
                print(f"HTTP {e.code}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            sys.exit(f"HTTP {e.code}: {e.read().decode(errors='replace')}")
    sys.exit("Giving up after repeated errors")


def flatten(person):
    org = person.get("organization") or {}
    return {
        "id": person.get("id"),
        "first_name": person.get("first_name"),
        "last_name": person.get("last_name"),
        "name": person.get("name"),
        "title": person.get("title"),
        "seniority": person.get("seniority"),
        "linkedin_url": person.get("linkedin_url"),
        "email": person.get("email"),
        "email_status": person.get("email_status"),
        "city": person.get("city"),
        "state": person.get("state"),
        "country": person.get("country"),
        "organization_name": org.get("name"),
        "organization_website": org.get("website_url"),
        "organization_linkedin": org.get("linkedin_url"),
        "organization_employees": org.get("estimated_num_employees"),
        "organization_industry": org.get("industry"),
    }


def merge(row, person):
    """Overwrite row fields with enriched values, keeping old values where Apollo has none."""
    for key, value in flatten(person).items():
        if value not in (None, ""):
            row[key] = value
    return row


def clean(row):
    return {k: html.unescape(v) if isinstance(v, str) else v for k, v in row.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="CSV from apollo_people_export.py")
    ap.add_argument("--out", default="leads_enriched.csv")
    ap.add_argument("--personal-emails", action="store_true",
                    help="also reveal personal emails (uses extra credits)")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--yes", action="store_true", help="skip the credit confirmation")
    args = ap.parse_args()

    api_key = os.environ.get("APOLLO_API_KEY")
    if not api_key:
        sys.exit("Set APOLLO_API_KEY (Apollo > Settings > Integrations > API)")

    with open(args.csv, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("id")]
    if not rows:
        sys.exit(f"No rows with an id found in {args.csv}")

    if not args.yes:
        answer = input(f"Enrich {len(rows)} people? This uses up to {len(rows)} Apollo credits. [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            sys.exit("Cancelled")

    matched = with_email = 0
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for start in range(0, len(rows), BATCH_SIZE):
            batch = rows[start:start + BATCH_SIZE]
            data = post(api_key, {
                "details": [{"id": r["id"]} for r in batch],
                "reveal_personal_emails": args.personal_emails,
            })
            by_id = {p["id"]: p for p in (data.get("matches") or []) if p and p.get("id")}
            for row in batch:
                person = by_id.get(row["id"])
                if person:
                    merge(row, person)
                    matched += 1
                    if person.get("email"):
                        with_email += 1
                writer.writerow(clean(row))
            f.flush()
            done = start + len(batch)
            print(f"{done}/{len(rows)} processed ({matched} matched, {with_email} with email)", file=sys.stderr)
            if done < len(rows):
                time.sleep(args.delay)

    print(f"Wrote {len(rows)} people to {args.out} ({with_email} with email)", file=sys.stderr)


if __name__ == "__main__":
    main()
