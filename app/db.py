import sqlite3, json, os, datetime, threading
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DB = os.path.join(DATA, "app.db")
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS resumes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, filename TEXT, text TEXT,
  active INTEGER DEFAULT 1, uploaded_at TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT, job_id TEXT, title TEXT, company TEXT,
  location TEXT, url TEXT, description TEXT, posted_at TEXT, easy_apply INTEGER,
  discovered_at TEXT, score INTEGER, score_reason TEXT, status TEXT DEFAULT 'new',
  resume_id INTEGER, tailored_json TEXT, tailored_pdf TEXT, cover_note TEXT,
  applied_at TEXT, note TEXT, screenshot TEXT,
  UNIQUE(platform, job_id));
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
  status TEXT, mode TEXT, log TEXT DEFAULT '');
"""

def now(): return datetime.datetime.now().isoformat(timespec="seconds")

def conn():
    c = sqlite3.connect(DB, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    return c

def init():
    os.makedirs(DATA, exist_ok=True)
    with conn() as c: c.executescript(SCHEMA)

def q(sql, args=(), one=False):
    with _lock, conn() as c:
        cur = c.execute(sql, args)
        if sql.lstrip().upper().startswith("SELECT"):
            rows = [dict(r) for r in cur.fetchall()]
            return (rows[0] if rows else None) if one else rows
        c.commit(); return cur.lastrowid

def get_setting(key, default=None):
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return json.loads(r["value"]) if r else default

def set_setting(key, value):
    q("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, json.dumps(value)))

def upsert_job(j):
    """j: dict with platform, job_id, title, company, location, url, description, posted_at, easy_apply"""
    ex = q("SELECT id FROM jobs WHERE platform=? AND job_id=?", (j["platform"], j["job_id"]), one=True)
    if ex: return ex["id"], False
    return q("""INSERT INTO jobs(platform,job_id,title,company,location,url,description,posted_at,easy_apply,discovered_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
             (j["platform"], j["job_id"], j.get("title",""), j.get("company",""), j.get("location",""),
              j.get("url",""), j.get("description",""), j.get("posted_at",""),
              None if j.get("easy_apply") is None else int(bool(j.get("easy_apply"))), now())), True

def update_job(id, **kw):
    sets = ", ".join(f"{k}=?" for k in kw)
    q(f"UPDATE jobs SET {sets} WHERE id=?", (*kw.values(), id))

def log(run_id, msg):
    line = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}\n"
    q("UPDATE runs SET log = log || ? WHERE id=?", (line, run_id))
    print(line, end="", flush=True)

DEFAULT_PREFS = {
  "name": "", "email": "", "phone": "", "city": "", "linkedin_url": "", "github_url": "", "portfolio_url": "",
  "job_types": ["internship", "full-time"], "roles": "", "locations": "India", "remote_ok": True,
  "experience_level": "fresher", "min_stipend": 0, "min_ctc_lpa": 0,
  "avoid_companies": "", "avoid_keywords": "senior, lead, manager, BPO, sales, unpaid",
  "notice_period_days": 0, "current_ctc_lpa": 0, "expected_ctc_lpa": 0, "graduation_year": "",
  "willing_to_relocate": True, "work_authorization": "Indian citizen, no sponsorship needed",
  "earliest_start": "Immediately",
  "max_per_day": 25, "per_platform_cap": 10, "min_score": 60,
  "platforms": ["linkedin", "indeed", "naukri", "internshala"],
  "auto_submit": False, "llm_model": "sonnet"
}
def prefs():
    p = dict(DEFAULT_PREFS); p.update(get_setting("prefs", {}) or {}); return p
