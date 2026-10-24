"""Flexible job search over the Adzuna API.

Any search, any filters. Results are saved to recruiters.csv (and optionally JSON).
Needs only Python 3.8+. Free key: https://developer.adzuna.com

Set ADZUNA_APP_ID and ADZUNA_APP_KEY, or you will be asked for them.

Examples
  # The team's search: Java Developer, keywords, last 24h, US
  python job_search.py --preset c2c

  # Same thing, written out (the dashes in the task mean EXCLUDE these words)
  python job_search.py --what "Java Developer" --keywords C2C --exclude-text "Bench,Sales,Hotlist,W2,Full-Time" --days 1 --country us

  # Other searches
  python job_search.py --what "Data Scientist" --where "Dallas, TX" --distance 50 --days 3 --contract
  python job_search.py --phrase "spring boot" --what-or "java kotlin" --salary-min 90000 --keywords W2 --match all
  python job_search.py --what "React Developer" --title-only --exclude "intern junior" --country gb --require-email
"""
import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

COUNTRIES = "gb us at au be br ca ch de es fr in it mx nl nz pl sg za".split()
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

PRESETS = {
    "c2c": {
        "what": "Java Developer",
        "keywords": "C2C",
        "exclude_text": "Bench,Sales,Hotlist,W2,Full-Time,Fulltime",
        "days": 1,
        "country": "us",
    },
}


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_argument_group("what to search for (sent to Adzuna)")
    g.add_argument("--what", help="all words, anywhere (default: Java Developer)")
    g.add_argument("--what-and", help="every one of these words must appear")
    g.add_argument("--what-or", help="at least one of these words must appear")
    g.add_argument("--phrase", help="this exact phrase must appear")
    g.add_argument("--exclude", help="words that must NOT appear")
    g.add_argument("--title-only", help="words that must be in the job title")
    g.add_argument("--company", help="company name")
    g.add_argument("--category", help="Adzuna category tag, e.g. it-jobs")

    g = p.add_argument_group("where and when")
    g.add_argument("--country", help=f"one of: {', '.join(COUNTRIES)} (default: us)")
    g.add_argument("--where", help='location, e.g. "Dallas, TX"')
    g.add_argument("--distance", type=int, help="kilometres around --where")
    g.add_argument("--days", type=int, help="posted within this many days (1 = last 24 hours)")

    g = p.add_argument_group("job type and pay")
    g.add_argument("--contract", action="store_true", help="contract jobs only")
    g.add_argument("--permanent", action="store_true", help="permanent jobs only")
    g.add_argument("--full-time", action="store_true", help="full-time only")
    g.add_argument("--part-time", action="store_true", help="part-time only")
    g.add_argument("--salary-min", type=int)
    g.add_argument("--salary-max", type=int)
    g.add_argument("--include-unknown-salary", action="store_true", help="keep jobs with no salary listed")

    g = p.add_argument_group("text filters (applied by this script on title + description)")
    g.add_argument("--keywords", help="comma list; keep jobs that mention these")
    g.add_argument("--match", choices=["any", "all"], default="any", help="match any or all --keywords (default: any)")
    g.add_argument("--exclude-text", help="comma list; drop jobs that mention any of these")
    g.add_argument("--require-email", action="store_true", help="keep only listings containing an email")

    g = p.add_argument_group("output")
    g.add_argument("--sort", choices=["date", "relevance", "salary", "hybrid", "default"], default="date")
    g.add_argument("--pages", type=int, default=1, help="pages of results to fetch (default 1; each page is one API call)")
    g.add_argument("--per-page", type=int, default=50, help="results per page, max 50")
    g.add_argument("--out", default="recruiters.csv")
    g.add_argument("--json", action="store_true", help="also write results as JSON")
    g.add_argument("--preset", choices=list(PRESETS), help="start from a saved search")
    a = p.parse_args()

    for k, v in PRESETS.get(a.preset, {}).items():
        if getattr(a, k) is None:
            setattr(a, k, v)
    a.country = (a.country or "us").lower()
    if a.country not in COUNTRIES:
        p.error(f"--country must be one of: {', '.join(COUNTRIES)}")
    searched = [a.what, a.what_and, a.what_or, a.phrase, a.title_only, a.company, a.category]
    if not any(searched):
        a.what = "Java Developer"
    return a


def build_params(a, page, creds):
    q = {"app_id": creds[0], "app_key": creds[1], "results_per_page": min(a.per_page, 50),
         "sort_by": a.sort, "content-type": "application/json"}
    opt = {
        "what": a.what, "what_and": a.what_and, "what_or": a.what_or, "what_phrase": a.phrase,
        "what_exclude": a.exclude, "title_only": a.title_only, "company": a.company,
        "category": a.category, "where": a.where, "distance": a.distance,
        "max_days_old": a.days, "salary_min": a.salary_min, "salary_max": a.salary_max,
    }
    q.update({k: v for k, v in opt.items() if v not in (None, "")})
    for flag, name in [(a.contract, "contract"), (a.permanent, "permanent"),
                       (a.full_time, "full_time"), (a.part_time, "part_time"),
                       (a.include_unknown_salary, "salary_include_unknown")]:
        if flag:
            q[name] = 1
    return f"https://api.adzuna.com/v1/api/jobs/{a.country}/search/{page}?" + urllib.parse.urlencode(q)


def fetch(url):
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        hint = " (check your app id and key)" if e.code in (401, 403) else ""
        sys.exit(f"Adzuna returned HTTP {e.code}{hint}.")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach Adzuna: {e.reason}")


def has_word(text, word):
    return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, re.I) is not None


def csv_list(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def main():
    a = parse_args()
    creds = (os.getenv("ADZUNA_APP_ID") or input("Adzuna app id: ").strip(),
             os.getenv("ADZUNA_APP_KEY") or input("Adzuna app key: ").strip())
    keywords, excludes = csv_list(a.keywords), csv_list(a.exclude_text)
    stats = {"fetched": 0, "no keyword match": 0, "excluded text": 0, "no email": 0, "duplicates": 0}
    rows, seen = [], set()
    total_available = None

    for page in range(1, a.pages + 1):
        data = fetch(build_params(a, page, creds))
        total_available = data.get("count", total_available)
        results = data.get("results", [])
        stats["fetched"] += len(results)
        for j in results:
            text = f"{j.get('title', '')} {j.get('description', '')}"
            if keywords:
                hits = [has_word(text, k) for k in keywords]
                if not (all(hits) if a.match == "all" else any(hits)):
                    stats["no keyword match"] += 1
                    continue
            if any(has_word(text, x) for x in excludes):
                stats["excluded text"] += 1
                continue
            emails = EMAIL_RE.findall(text)
            if a.require_email and not emails:
                stats["no email"] += 1
                continue
            for email in (emails or [""]):
                key = email.lower() or j.get("redirect_url")
                if key in seen:
                    stats["duplicates"] += 1
                    continue
                seen.add(key)
                rows.append({
                    "recruiter_name": (j.get("company") or {}).get("display_name", ""),
                    "recruiter_email": email.lower(),
                    "role": j.get("title", ""),
                    "post_url": j.get("redirect_url", ""),
                    "job_description": " ".join(text.split())[:500],
                    "email_sent_status": "pending" if email else "no_email",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "location": (j.get("location") or {}).get("display_name", ""),
                    "contract_type": j.get("contract_type", "") or "",
                    "salary_min": j.get("salary_min", "") or "",
                    "salary_max": j.get("salary_max", "") or "",
                    "posted": j.get("created", ""),
                })
        if len(results) < min(a.per_page, 50):
            break  # no more pages

    fields = ["recruiter_name", "recruiter_email", "role", "post_url", "job_description",
              "email_sent_status", "timestamp", "location", "contract_type",
              "salary_min", "salary_max", "posted"]
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    if a.json:
        with open(os.path.splitext(a.out)[0] + ".json", "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2)

    with_email = sum(1 for r in rows if r["recruiter_email"])
    print(f"Adzuna reports {total_available} matching jobs; fetched {stats['fetched']}.")
    for k in ("no keyword match", "excluded text", "no email", "duplicates"):
        if stats[k]:
            print(f"  removed by filter - {k}: {stats[k]}")
    print(f"Kept {len(rows)} listings, {with_email} with a recruiter email. Saved to {a.out}")


if __name__ == "__main__":
    main()
