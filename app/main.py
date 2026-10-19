import os, json, threading, shutil
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from . import db, resume, pipeline, browser

db.init()
app = FastAPI(title="Job Autopilot")
HERE = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
tpl = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates")), autoescape=True)

@app.get("/", response_class=HTMLResponse)
def index():
    return tpl.get_template("index.html").render()

@app.get("/api/state")
def api_state():
    s = pipeline.state()
    run = db.q("SELECT * FROM runs WHERE id=?", (s["run_id"],), one=True) if s.get("run_id") else db.q("SELECT * FROM runs ORDER BY id DESC LIMIT 1", one=True)
    counts = {r["status"]: r["c"] for r in db.q("SELECT status, COUNT(*) c FROM jobs GROUP BY status")}
    return {"state": s, "run": run, "counts": counts, "applied_today": pipeline.applied_today(),
            "resumes": db.q("SELECT id,name,filename,active,uploaded_at,length(text) chars FROM resumes ORDER BY id"),
            "has_master": bool(db.get_setting("master")), "prefs": db.prefs()}

@app.post("/api/resumes")
async def upload_resume(files: list[UploadFile] = File(...)):
    out = []
    for f in files:
        dest = os.path.join(db.DATA, "uploads", f.filename)
        with open(dest, "wb") as w: shutil.copyfileobj(f.file, w)
        try: text = resume.extract_text(dest)
        except Exception as e: text = ""; print("extract failed", e)
        rid = db.q("INSERT INTO resumes(name,filename,text,active,uploaded_at) VALUES(?,?,?,?,?)",
                   (os.path.splitext(f.filename)[0], dest, text, 1, db.now()))
        out.append({"id": rid, "chars": len(text)})
    db.set_setting("master", None)   # master must be rebuilt
    return {"ok": True, "uploaded": out}

@app.post("/api/resumes/{rid}/toggle")
def toggle_resume(rid: int):
    r = db.q("SELECT active FROM resumes WHERE id=?", (rid,), one=True)
    db.q("UPDATE resumes SET active=? WHERE id=?", (0 if r["active"] else 1, rid)); db.set_setting("master", None)
    return {"ok": True}

@app.delete("/api/resumes/{rid}")
def delete_resume(rid: int):
    db.q("DELETE FROM resumes WHERE id=?", (rid,)); db.set_setting("master", None); return {"ok": True}

@app.get("/api/master")
def get_master(): return db.get_setting("master") or {}

@app.post("/api/master/rebuild")
def rebuild_master():
    if pipeline.state()["running"]: return JSONResponse({"error": "a run is in progress"}, 409)
    rid = db.q("INSERT INTO runs(started_at,status,mode) VALUES(?,?,?)", (db.now(), "running", "master"))
    def w():
        try: pipeline.ensure_master(lambda m: db.log(rid, m), force=True); db.q("UPDATE runs SET finished_at=?, status='done' WHERE id=?", (db.now(), rid))
        except Exception as e: db.log(rid, f"ERROR {e}"); db.q("UPDATE runs SET finished_at=?, status='error' WHERE id=?", (db.now(), rid))
    threading.Thread(target=w, daemon=True).start(); return {"ok": True, "run_id": rid}

@app.post("/api/prefs")
async def save_prefs(req: Request):
    data = await req.json(); cur = db.prefs(); cur.update(data); db.set_setting("prefs", cur); return {"ok": True}

@app.post("/api/run/{mode}")
async def start_run(mode: str, req: Request):
    body = {}
    try: body = await req.json()
    except Exception: pass
    if mode not in ("discover", "prepare", "apply", "full"): return JSONResponse({"error": "bad mode"}, 400)
    rid = pipeline.run(mode, job_ids=body.get("job_ids"))
    if rid is None: return JSONResponse({"error": "already running"}, 409)
    return {"ok": True, "run_id": rid}

@app.post("/api/login")
def login(platforms: str = Form("linkedin,indeed,naukri,internshala")):
    if pipeline.state()["running"]: return JSONResponse({"error": "a run is in progress"}, 409)
    threading.Thread(target=browser.login_session, args=([p for p in platforms.split(",") if p],), daemon=True).start()
    return {"ok": True}

@app.get("/api/login/status")
def login_status():
    if pipeline.state()["running"]: return {"busy": True}
    try: return browser.login_status()
    except Exception as e: return {"error": str(e)}

@app.get("/api/jobs")
def jobs(status: str = "", limit: int = 300):
    if status: return db.q("SELECT id,platform,job_id,title,company,location,url,posted_at,easy_apply,score,score_reason,status,tailored_pdf,note,screenshot,applied_at,discovered_at FROM jobs WHERE status=? ORDER BY score DESC, id DESC LIMIT ?", (status, limit))
    return db.q("SELECT id,platform,job_id,title,company,location,url,posted_at,easy_apply,score,score_reason,status,tailored_pdf,note,screenshot,applied_at,discovered_at FROM jobs ORDER BY CASE status WHEN 'needs_you' THEN 0 WHEN 'ready' THEN 1 WHEN 'shortlisted' THEN 2 WHEN 'new' THEN 3 WHEN 'applied' THEN 4 ELSE 5 END, score DESC, id DESC LIMIT ?", (limit,))

@app.post("/api/jobs/{jid}/status")
async def set_status(jid: int, req: Request):
    d = await req.json(); db.update_job(jid, status=d["status"], note=d.get("note", ""), applied_at=db.now() if d["status"] == "applied" else None); return {"ok": True}

@app.get("/api/jobs/{jid}")
def job_detail(jid: int):
    j = db.q("SELECT * FROM jobs WHERE id=?", (jid,), one=True)
    if j and j.get("tailored_json"): j["tailored"] = json.loads(j["tailored_json"])
    return j

@app.get("/files/pdf/{jid}")
def pdf(jid: int):
    j = db.q("SELECT tailored_pdf FROM jobs WHERE id=?", (jid,), one=True)
    return FileResponse(j["tailored_pdf"], media_type="application/pdf") if j and j["tailored_pdf"] and os.path.exists(j["tailored_pdf"]) else JSONResponse({"error": "no pdf"}, 404)

@app.get("/files/shot/{jid}")
def shot(jid: int):
    j = db.q("SELECT screenshot FROM jobs WHERE id=?", (jid,), one=True)
    return FileResponse(j["screenshot"]) if j and j["screenshot"] and os.path.exists(j["screenshot"]) else JSONResponse({"error": "no screenshot"}, 404)
