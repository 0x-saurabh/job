"""Job discovery. LinkedIn via Apify (no login). Indeed/Naukri/Internshala via the logged-in Playwright profile."""
import os, json, re, urllib.request, urllib.parse, ssl, time
from . import db
from .browser import open_context

ACTOR = "dataji~apify-linkdin-jobs"
try:
    import certifi; SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError: SSL_CTX = ssl.create_default_context()

def _env(key):
    if os.environ.get(key): return os.environ[key]
    p = os.path.join(db.ROOT, ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.startswith(key + "="): return line.strip().split("=", 1)[1]
    return None

def _split(s): return [x.strip() for x in re.split(r"[,\n;]", s or "") if x.strip()]

def linkedin(prefs, log, max_results=40):
    tok = _env("APIFY_TOKEN")
    if not tok: log("linkedin: APIFY_TOKEN missing in .env, skipping"); return []
    seen = [r["job_id"] for r in db.q("SELECT job_id FROM jobs WHERE platform='linkedin'")]
    types = []
    if "internship" in prefs["job_types"]: types.append("internship")
    if "full-time" in prefs["job_types"]: types.append("fullTime")
    levels = {"fresher": ["internship", "entryLevel"], "0-1": ["internship", "entryLevel"], "1-3": ["entryLevel", "associate"], "3-5": ["associate", "midSenior"]}.get(prefs.get("experience_level"), [])
    out = []
    roles = _split(prefs.get("roles")) or ["software developer"]
    locs = _split(prefs.get("locations")) or ["India"]
    per = max(10, max_results // max(1, len(roles)))
    for role in roles:
        payload = {"keywords": role, "location": locs[0], "datePosted": "pastWeek", "sortBy": "recent",
                   "maxResults": per, "scrapeDetails": True, "scrapeCompany": False, "jobTypes": types,
                   "experienceLevels": levels, "workplaceTypes": [],
                   "titleExcludeKeywords": [k for k in _split(prefs.get("avoid_keywords")) if len(k) < 20],
                   "excludeCompanies": _split(prefs.get("avoid_companies")), "skipJobIds": seen,
                   "proxyConfiguration": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]}}
        url = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items?" + urllib.parse.urlencode({"token": tok, "timeout": 300, "format": "json"})
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=330, context=SSL_CTX) as r: items = json.load(r)
        except Exception as e:
            log(f"linkedin: apify error for '{role}': {e}"); continue
        for j in items:
            out.append({"platform": "linkedin", "job_id": str(j.get("jobId")), "title": j.get("title") or "",
                        "company": j.get("companyName") or "", "location": j.get("location") or "",
                        "url": f"https://www.linkedin.com/jobs/view/{j.get('jobId')}", "description": j.get("description") or "",
                        "posted_at": j.get("postedAt") or "", "easy_apply": j.get("easyApply")})
        log(f"linkedin: '{role}' -> {len(items)} jobs")
    return out

def _text(page, sel, default=""):
    try:
        el = page.locator(sel).first
        if el.count(): return el.inner_text(timeout=4000).strip()
    except Exception: pass
    return default

def _desc(page, selectors):
    for s in selectors:
        t = _text(page, s)
        if len(t) > 150: return t[:8000]
    try: return page.inner_text("body")[:6000]
    except Exception: return ""

def indeed(prefs, log, ctx, max_results=25):
    out = []
    page = ctx.new_page()
    for role in _split(prefs.get("roles")) or ["software developer"]:
        for loc in (_split(prefs.get("locations")) or ["India"])[:2]:
            q = urllib.parse.urlencode({"q": role + (" internship" if prefs["job_types"] == ["internship"] else ""), "l": loc, "fromage": 7, "sort": "date"})
            page.goto(f"https://in.indeed.com/jobs?{q}", wait_until="domcontentloaded"); page.wait_for_timeout(2500)
            if "captcha" in page.url or page.locator("text=Verify you are human").count():
                log("indeed: human verification shown, skipping (open the login window and solve it)"); break
            cards = page.locator("a[data-jk]")
            n = min(cards.count(), max_results)
            ids = []
            for i in range(n):
                try:
                    jk = cards.nth(i).get_attribute("data-jk"); title = cards.nth(i).inner_text().strip().split("\n")[0]
                    if jk: ids.append((jk, title))
                except Exception: pass
            log(f"indeed: '{role}' in {loc} -> {len(ids)} cards")
            seen = {r["job_id"] for r in db.q("SELECT job_id FROM jobs WHERE platform='indeed'")}
            for jk, title in ids:
                if jk in seen: continue
                url = f"https://in.indeed.com/viewjob?jk={jk}"
                try:
                    page.goto(url, wait_until="domcontentloaded"); page.wait_for_timeout(1500)
                    out.append({"platform": "indeed", "job_id": jk, "title": _text(page, "h1") or title,
                                "company": _text(page, "[data-company-name], [data-testid='inlineHeader-companyName']"),
                                "location": _text(page, "[data-testid='inlineHeader-companyLocation'], [data-testid='job-location']"),
                                "url": url, "description": _desc(page, ["#jobDescriptionText"]), "posted_at": "",
                                "easy_apply": bool(page.locator("button:has-text('Apply now'), #indeedApplyButton").count())})
                except Exception as e: log(f"indeed: failed {jk}: {e}")
                if len(out) >= max_results: break
    page.close(); return out

def naukri(prefs, log, ctx, max_results=25):
    out = []
    page = ctx.new_page()
    exp = {"fresher": 0, "0-1": 1, "1-3": 2, "3-5": 4}.get(prefs.get("experience_level"), 0)
    for role in _split(prefs.get("roles")) or ["software developer"]:
        slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")
        loc = (_split(prefs.get("locations")) or ["india"])[0].lower()
        url = f"https://www.naukri.com/{slug}-jobs-in-{re.sub(r'[^a-z0-9]+','-',loc)}?experience={exp}&jobAge=7"
        if "internship" in prefs["job_types"] and "full-time" not in prefs["job_types"]:
            url = f"https://www.naukri.com/{slug}-internship-jobs-in-{re.sub(r'[^a-z0-9]+','-',loc)}?jobAge=7"
        page.goto(url, wait_until="domcontentloaded"); page.wait_for_timeout(3500)
        links = page.locator("a.title, .srp-jobtuple-wrapper a.title, article.jobTuple a.title")
        n = min(links.count(), max_results); items = []
        for i in range(n):
            try:
                href = links.nth(i).get_attribute("href"); t = links.nth(i).inner_text().strip()
                m = re.search(r"-(\d{9,})(?:\?|$)", href or "")
                if href and m: items.append((m.group(1), href, t))
            except Exception: pass
        log(f"naukri: '{role}' -> {len(items)} cards")
        seen = {r["job_id"] for r in db.q("SELECT job_id FROM jobs WHERE platform='naukri'")}
        for jid, href, t in items:
            if jid in seen: continue
            try:
                page.goto(href, wait_until="domcontentloaded"); page.wait_for_timeout(2000)
                out.append({"platform": "naukri", "job_id": jid, "title": _text(page, "h1") or t,
                            "company": _text(page, "[class*=jd-header-comp-name] a, .jd-header-comp-name, a.comp-name"),
                            "location": _text(page, "[class*=location], .loc"), "url": href,
                            "description": _desc(page, ["[class*=job-desc], .dang-inner-html, section.job-desc"]), "posted_at": "",
                            "easy_apply": bool(page.locator("#apply-button, button:has-text('Apply')").count()) and not page.locator("button:has-text('Apply on company site')").count()})
            except Exception as e: log(f"naukri: failed {jid}: {e}")
            if len(out) >= max_results: break
    page.close(); return out

def internshala(prefs, log, ctx, max_results=25):
    out = []
    page = ctx.new_page()
    for role in _split(prefs.get("roles")) or ["web development"]:
        slug = re.sub(r"[^a-z0-9]+", "-", role.lower()).strip("-")
        url = f"https://internshala.com/internships/keywords-{slug}/"
        if prefs.get("remote_ok"): pass
        page.goto(url, wait_until="domcontentloaded"); page.wait_for_timeout(3000)
        cards = page.locator(".individual_internship[internshipid]")
        n = min(cards.count(), max_results); items = []
        for i in range(n):
            try:
                c = cards.nth(i); iid = c.get_attribute("internshipid")
                a = c.locator("a.job-title-href, h3 a, a[href*='/internship/detail/']").first
                href = a.get_attribute("href"); t = a.inner_text().strip()
                if href and iid: items.append((iid, "https://internshala.com" + href if href.startswith("/") else href, t))
            except Exception: pass
        log(f"internshala: '{role}' -> {len(items)} cards")
        seen = {r["job_id"] for r in db.q("SELECT job_id FROM jobs WHERE platform='internshala'")}
        for iid, href, t in items:
            if iid in seen: continue
            try:
                page.goto(href, wait_until="domcontentloaded"); page.wait_for_timeout(1500)
                out.append({"platform": "internshala", "job_id": iid, "title": _text(page, ".heading_4_5.profile, h1") or t,
                            "company": _text(page, ".company_name, .heading_6.company_name a, .company"),
                            "location": _text(page, "#location_names, .location_link"), "url": href,
                            "description": _desc(page, [".internship_details, .detail_view"]), "posted_at": "",
                            "easy_apply": True})
            except Exception as e: log(f"internshala: failed {iid}: {e}")
            if len(out) >= max_results: break
    page.close(); return out

def run_all(prefs, log, cap_per_platform=25):
    found = []
    if "linkedin" in prefs["platforms"]:
        found += linkedin(prefs, log, max_results=cap_per_platform * 2)
    browser_platforms = [p for p in ("indeed", "naukri", "internshala") if p in prefs["platforms"]]
    if browser_platforms:
        with open_context(headless=False) as ctx:
            for p in browser_platforms:
                try: found += globals()[p](prefs, log, ctx, max_results=cap_per_platform)
                except Exception as e: log(f"{p}: discovery error: {e}")
    new = 0
    for j in found:
        if not j.get("job_id"): continue
        _, created = db.upsert_job(j); new += created
    log(f"discovery done: {len(found)} fetched, {new} new")
    return new
