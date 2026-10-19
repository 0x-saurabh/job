#!/usr/bin/env python3
"""Fetch LinkedIn jobs via Apify actor dataji/apify-linkdin-jobs (no login).
Usage: fetch_linkedin.py --keywords "frontend developer" --location "India" [--max 50]
       [--date past24Hours|pastWeek|pastMonth] [--types internship,fullTime]
       [--levels internship,entryLevel] [--workplace remote,hybrid,onSite]
       [--exclude-title senior,manager] [--out candidates/linkedin_<date>.json]
Skips jobs already in tracker/applications.db. Prints a compact table.
"""
import argparse, json, os, sys, urllib.request, urllib.parse, datetime, sqlite3, ssl
try:
    import certifi; SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTOR = "dataji~apify-linkdin-jobs"

def load_env():
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p):
            if "=" in line and not line.startswith("#"):
                k, v = line.strip().split("=", 1); os.environ.setdefault(k, v)

def seen_ids():
    db = os.path.join(ROOT, "tracker", "applications.db")
    if not os.path.exists(db): return set()
    c = sqlite3.connect(db)
    try: return {r[0] for r in c.execute("SELECT job_id FROM applications WHERE platform='linkedin'")}
    except sqlite3.OperationalError: return set()

def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", required=True); ap.add_argument("--location", default="India")
    ap.add_argument("--max", type=int, default=50); ap.add_argument("--date", default="pastWeek")
    ap.add_argument("--types", default=""); ap.add_argument("--levels", default="")
    ap.add_argument("--workplace", default=""); ap.add_argument("--exclude-title", default="")
    ap.add_argument("--exclude-companies", default=""); ap.add_argument("--out", default="")
    a = ap.parse_args()
    tok = os.environ.get("APIFY_TOKEN")
    if not tok: sys.exit("APIFY_TOKEN missing (put it in .env)")
    split = lambda s: [x.strip() for x in s.split(",") if x.strip()]
    payload = {
        "keywords": a.keywords, "location": a.location, "datePosted": a.date, "sortBy": "recent",
        "maxResults": a.max, "scrapeDetails": True, "scrapeCompany": False,
        "jobTypes": split(a.types), "experienceLevels": split(a.levels), "workplaceTypes": split(a.workplace),
        "titleExcludeKeywords": split(a.exclude_title), "excludeCompanies": split(a.exclude_companies),
        "skipJobIds": sorted(seen_ids()),
        "proxyConfiguration": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
    }
    url = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items?" + urllib.parse.urlencode({"token": tok, "timeout": 300, "format": "json"})
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=330, context=SSL_CTX) as r: items = json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"Apify error {e.code}: {e.read().decode()[:500]}")
    os.makedirs(os.path.join(ROOT, "candidates"), exist_ok=True)
    out = a.out or os.path.join(ROOT, "candidates", f"linkedin_{datetime.date.today().isoformat()}.json")
    # merge with existing file for the day
    existing = {}
    if os.path.exists(out):
        for j in json.load(open(out)): existing[j["jobId"]] = j
    for j in items: existing[j["jobId"]] = j
    json.dump(list(existing.values()), open(out, "w"), indent=1, ensure_ascii=False)
    print(f"{len(items)} new jobs fetched, {len(existing)} total in {os.path.relpath(out, ROOT)}\n")
    for j in items:
        print(f"{j.get('jobId')} | {j.get('title','')[:45]:45} | {j.get('companyName','')[:25]:25} | {j.get('location','')[:22]:22} | {'EASY' if j.get('easyApply') else 'ext '} | {j.get('postedAt','')}")

if __name__ == "__main__": main()
