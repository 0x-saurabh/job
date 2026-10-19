"""Orchestration: discover -> score -> tailor -> apply. Runs in a background thread."""
import threading, json, traceback, datetime
from . import db, discover, resume, apply as applymod
from .browser import open_context

_state = {"running": False, "run_id": None, "stage": "idle"}

def state(): return dict(_state)

def ensure_master(log, force=False):
    master = db.get_setting("master")
    if master and not force: return master
    rs = db.q("SELECT * FROM resumes WHERE active=1")
    if not rs: raise RuntimeError("No active resumes uploaded")
    log(f"building master profile from {len(rs)} resume(s)...")
    master = resume.build_master([r["text"] for r in rs], model=db.prefs().get("llm_model", "sonnet"))
    p = db.prefs()
    for k_m, k_p in (("name", "name"), ("email", "email"), ("phone", "phone"), ("location", "city")):
        if p.get(k_p): master[k_m] = p[k_p]
    master.setdefault("links", {})
    for k_m, k_p in (("linkedin", "linkedin_url"), ("github", "github_url"), ("portfolio", "portfolio_url")):
        if p.get(k_p): master["links"][k_m] = p[k_p]
    db.set_setting("master", master); log("master profile saved")
    return master

def score_new(master, prefs, log):
    jobs = db.q("SELECT * FROM jobs WHERE status='new' AND score IS NULL ORDER BY id DESC LIMIT 120")
    if not jobs: return
    summ = resume.master_summary(master)
    for i in range(0, len(jobs), 8):
        batch = jobs[i:i+8]
        try: res = resume.score_batch(summ, batch, prefs, model=prefs.get("llm_model", "sonnet"))
        except Exception as e: log(f"scoring error: {e}"); continue
        for r in res:
            try:
                st = "skipped" if r.get("skip") or int(r.get("score", 0)) < int(prefs.get("min_score", 60)) else "shortlisted"
                db.update_job(int(r["id"]), score=int(r.get("score", 0)), score_reason=r.get("reason", ""), status=st)
            except Exception: pass
        log(f"scored {min(i+8, len(jobs))}/{len(jobs)}")

def applied_today():
    return db.q("SELECT COUNT(*) c FROM jobs WHERE status='applied' AND applied_at LIKE ?", (datetime.date.today().isoformat() + "%",), one=True)["c"]

def run(mode="full", job_ids=None):
    """mode: discover | prepare | apply | full"""
    if _state["running"]: return None
    rid = db.q("INSERT INTO runs(started_at,status,mode) VALUES(?,?,?)", (db.now(), "running", mode))
    _state.update(running=True, run_id=rid, stage="starting")
    def log(m): db.log(rid, m)
    def work():
        try:
            prefs = db.prefs()
            if mode in ("discover", "full"):
                _state["stage"] = "discovering"
                discover.run_all(prefs, log, cap_per_platform=int(prefs.get("per_platform_cap", 10)) * 2)
            if mode in ("prepare", "full", "apply"):
                master = ensure_master(log)
            if mode in ("prepare", "full"):
                _state["stage"] = "scoring"; score_new(master, prefs, log)
                _state["stage"] = "tailoring"
                cands = db.q("SELECT * FROM jobs WHERE status='shortlisted' ORDER BY score DESC LIMIT ?", (int(prefs.get("max_per_day", 25)),))
                for j in cands:
                    try:
                        t, pdf = resume.tailor_and_render(j, master, prefs)
                        db.update_job(j["id"], tailored_json=json.dumps(t, ensure_ascii=False), tailored_pdf=pdf,
                                      cover_note=t.get("cover_note", ""), status="ready")
                        log(f"tailored: {j['company']} / {j['title']} (fit {t.get('match_score')})")
                    except Exception as e: log(f"tailor failed {j['title']}: {e}")
            if mode in ("apply", "full"):
                _state["stage"] = "applying"
                if job_ids:
                    jobs = [db.q("SELECT * FROM jobs WHERE id=?", (i,), one=True) for i in job_ids]
                    jobs = [j for j in jobs if j and j["tailored_pdf"]]
                else:
                    jobs = db.q("SELECT * FROM jobs WHERE status='ready' ORDER BY score DESC")
                budget = int(prefs.get("max_per_day", 25)) - applied_today()
                per_cap = int(prefs.get("per_platform_cap", 10)); counts = {}
                submit = bool(prefs.get("auto_submit")) or bool(job_ids)
                if jobs and budget > 0:
                    with open_context(headless=False) as ctx:
                        for j in jobs:
                            if budget <= 0: log("daily cap reached"); break
                            if counts.get(j["platform"], 0) >= per_cap and not job_ids: continue
                            st = applymod.apply_job(ctx, j, prefs, log, submit)
                            counts[j["platform"]] = counts.get(j["platform"], 0) + 1
                            if st == "applied": budget -= 1
                        if db.q("SELECT COUNT(*) c FROM jobs WHERE status='needs_you'", one=True)["c"] and not submit:
                            log("some tabs left open for you to finish; browser stays open 15 min")
                            import time; time.sleep(15 * 60)
                else: log("nothing to apply (no ready jobs or daily cap reached)")
            db.q("UPDATE runs SET finished_at=?, status='done' WHERE id=?", (db.now(), rid)); log("run finished")
        except Exception as e:
            log(f"RUN ERROR: {e}\n{traceback.format_exc()[-800:]}")
            db.q("UPDATE runs SET finished_at=?, status='error' WHERE id=?", (db.now(), rid))
        finally:
            _state.update(running=False, stage="idle")
    threading.Thread(target=work, daemon=True).start()
    return rid
