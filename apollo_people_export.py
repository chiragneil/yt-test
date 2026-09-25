#!/usr/bin/env python3
"""Export an Apollo.io people search to CSV via the official Apollo API.

Mirrors the saved UI search (Bengaluru HR/People leaders + founders at
101-500 employee companies). Requires an Apollo *master* API key:

    export APOLLO_API_KEY=xxxxxxxx
    python3 apollo_people_export.py --out leads.csv

The script asks you to paste an Apollo search URL (copied from the
browser address bar) and uses its filters. Press Enter without pasting
to use the built-in FILTERS below. You can also pass --url "<url>".

Uses the "search" endpoint by default (returns emails, may use credits).
Pass --endpoint api_search for the free, no-email endpoint.

Uses only the Python standard library.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ENDPOINTS = {
    # People API Search: no credits consumed, but no emails/phones returned.
    "api_search": "https://api.apollo.io/api/v1/mixed_people/api_search",
    # Legacy search endpoint (plan-dependent, may consume credits).
    "search": "https://api.apollo.io/api/v1/mixed_people/search",
}

# Filters translated 1:1 from the Apollo UI URL (camelCase -> snake_case).
FILTERS = {
    "person_titles": [
        "Chief People Officer", "CHRO", "VP People", "VP Human Resources",
        "Head of People", "Head of HR", "Head of People Operations",
        "Director HR", "Director People Operations", "Senior Manager HR",
        "HR Manager", "HRBP", "HR Business Partner", "Head of Culture",
        "Founder", "Co-Founder", "CEO", "COO",
    ],
    "person_not_titles": [
        "Recruiter", "Talent Acquisition", "Sourcer", "Payroll",
        "HR Executive", "HR Coordinator", "HR Intern", "HR Generalist",
        "Office Manager", "Admin",
    ],
    "person_seniorities": [
        "founder", "c_suite", "partner", "vp", "head", "director", "manager",
    ],
    "include_similar_titles": False,
    "person_locations": ["Bengaluru, India"],
    "organization_num_employees_ranges": ["101,200", "201,500"],
    "organization_industry_tag_ids": [
        "5567cd4e7369643b70010000", "5567cd4d736964397e020000",
        "5567cd4773696439b10b0000", "5567cd877369644cf94b0000",
        "5567cdd67369643e64020000", "5567cdd973696453d93f0000",
        "5567ce237369644ee5490000", "5567cd467369644d39040000",
        "5567cdbc73696439d90b0000", "5567cdd47369643dbf260000",
        "5567e19c7369641c48e70100", "5567e0ea7369640d2ba31600",
        "5567cdb373696439dd540000", "5567cd8b736964540d0f0000",
        "5567ce2d7369644d25250000", "5567ce1f7369643b78570000",
        "5567cdb77369645401080000", "5567e1387369641ec75d0200",
        "5567e1587369641c48370000", "5567d08e7369645dbc4b0000",
        "5567e0eb73696410e4bd1200",
    ],
    "organization_not_industry_tag_ids": [
        "5567e09973696410db020800", "5567d04173696457ee520000",
        "5567cdde73696439812c0000", "5567d0467369645dbc200000",
        "5567e0e0736964198de70700",
    ],
    "organization_latest_funding_stage_cd": ["0", "2", "3", "4"],
    "latest_funding_date_range": {"min": "24_months_ago"},
    "organization_founded_year_range": {"min": 2005},
    "sort_by_field": "recommendations_score",
    "sort_ascending": False,
}

CSV_FIELDS = [
    "id", "first_name", "last_name", "name", "title", "seniority",
    "linkedin_url", "email", "email_status", "city", "state", "country",
    "organization_name", "organization_website", "organization_linkedin",
    "organization_employees", "organization_industry",
]


# UI-only URL params that are not search filters.
IGNORED_URL_PARAMS = {"page", "recommendation_config_id"}


def filters_from_url(url):
    """Turn an Apollo UI search URL into API filters.

    personTitles[]=CEO&personTitles[]=COO  -> {"person_titles": ["CEO", "COO"]}
    latestFundingDateRange[min]=24_months_ago -> {"latest_funding_date_range": {"min": ...}}
    """
    query = url.split("?", 1)[1] if "?" in url else url
    filters = {}
    for raw_key, value in urllib.parse.parse_qsl(query, keep_blank_values=True):
        m = re.fullmatch(r"([^\[]+)(?:\[([^\]]*)\])?", raw_key)
        if not m:
            continue
        name, sub = m.groups()
        key = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
        if key in IGNORED_URL_PARAMS:
            continue
        if value in ("true", "false"):
            value = value == "true"
        if sub is None:
            filters[key] = value
        elif sub == "":
            filters.setdefault(key, []).append(value)
        else:
            filters.setdefault(key, {})[sub] = value
    return filters


def fetch_page(url, api_key, page, per_page, filters):
    body = dict(filters, page=page, per_page=per_page)
    req = urllib.request.Request(
        url,
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
                print(f"HTTP {e.code} on page {page}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            sys.exit(f"HTTP {e.code}: {e.read().decode(errors='replace')}")
    sys.exit(f"Giving up on page {page} after repeated errors")


def flatten(person):
    org = person.get("organization") or {}
    return {
        "id": person.get("id"),
        "first_name": person.get("first_name"),
        "last_name": person.get("last_name") or person.get("last_name_obfuscated"),
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="apollo_people.csv")
    ap.add_argument("--endpoint", choices=ENDPOINTS, default="search")
    ap.add_argument("--per-page", type=int, default=100, help="max 100")
    ap.add_argument("--max-pages", type=int, default=500, help="Apollo caps at 500")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--url", help="Apollo search URL copied from the browser")
    args = ap.parse_args()

    api_key = os.environ.get("APOLLO_API_KEY")
    if not api_key:
        sys.exit("Set APOLLO_API_KEY (Apollo > Settings > Integrations > API)")

    search_url = args.url
    if search_url is None:
        search_url = input("Paste your Apollo search URL (or press Enter for built-in filters):\n").strip()
    if search_url:
        filters = filters_from_url(search_url)
        if not filters:
            sys.exit("No filters found in that URL - copy the full address from the browser")
        print(f"Using {len(filters)} filters from the URL", file=sys.stderr)
    else:
        filters = FILTERS
        print("Using built-in FILTERS", file=sys.stderr)

    url = ENDPOINTS[args.endpoint]
    seen = set()
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        page = 1
        while page <= args.max_pages:
            data = fetch_page(url, api_key, page, args.per_page, filters)
            people = (data.get("people") or []) + (data.get("contacts") or [])
            if not people:
                break
            for p in people:
                if p.get("id") in seen:
                    continue
                seen.add(p.get("id"))
                writer.writerow(flatten(p))
            total_pages = (data.get("pagination") or {}).get("total_pages")
            total = data.get("total_entries") or (data.get("pagination") or {}).get("total_entries")
            print(f"page {page}: +{len(people)} (saved {len(seen)} / total {total})", file=sys.stderr)
            if total_pages and page >= total_pages:
                break
            if len(people) < args.per_page:
                break
            page += 1
            time.sleep(args.delay)

    print(f"Wrote {len(seen)} people to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
