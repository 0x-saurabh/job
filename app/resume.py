"""Resume parsing, master-profile building, per-job tailoring, PDF rendering."""
import os, json, re, datetime
from . import db, llm
from jinja2 import Environment, FileSystemLoader

TPL = Environment(loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "templates")), autoescape=True)

def extract_text(path):
    ext = path.lower().rsplit(".", 1)[-1]
    if ext == "pdf":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    if ext in ("docx",):
        import docx
        d = docx.Document(path)
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows: parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    return open(path, errors="ignore").read()

MASTER_SCHEMA = """{
 "name": str, "email": str, "phone": str, "location": str,
 "links": {"linkedin": str, "github": str, "portfolio": str},
 "headline": str,
 "summary": str,
 "skills": {"<category>": [str, ...]},
 "experience": [{"title": str, "company": str, "location": str, "start": str, "end": str, "bullets": [str]}],
 "projects": [{"name": str, "tech": [str], "link": str, "bullets": [str]}],
 "education": [{"degree": str, "institution": str, "year": str, "score": str}],
 "certifications": [str], "achievements": [str]
}"""

def build_master(resume_texts, model="sonnet"):
    joined = "\n\n=====NEXT RESUME=====\n\n".join(resume_texts)
    prompt = f"""You are merging a candidate's resumes into ONE master profile that is a superset of everything in them.
Rules: keep every real skill, project, role, bullet and achievement found in any resume (deduplicate near-identical ones, keep the stronger wording).
Do NOT invent anything that is not present. Keep bullets concise and quantified where the source has numbers.
Output JSON exactly in this shape:
{MASTER_SCHEMA}

RESUMES:
{joined[:60000]}"""
    return llm.ask_json(prompt, model=model)

def tailor(master, job, prefs, model="sonnet"):
    prompt = f"""You are tailoring a resume for one specific job. You get the candidate's MASTER profile (the full truth) and a JOB.
Produce a one-page resume as JSON in the SAME shape as the master, by:
- choosing a headline that matches the role title (only if candidate genuinely fits),
- writing a 2-3 line summary aimed at this job,
- selecting and ordering skills so the ones the JD asks for (and the candidate HAS) come first; group them sensibly,
- picking the 2-4 most relevant projects/experiences and rewording their bullets to use the JD's vocabulary,
- keeping education, certifications, achievements that matter.
HARD RULES: never add a skill, tool, employer, degree or metric that is not in the master. Rewording is fine, inventing is not.
Also return "cover_note": a 4-6 sentence first-person note for this application (plain text, no placeholders, no 'Dear Sir/Madam'),
"match_score": 0-100 honest fit, "missing": [key JD requirements the candidate lacks].

MASTER:
{json.dumps(master, ensure_ascii=False)}

JOB: {job['title']} at {job['company']} ({job.get('location','')})
JD:
{(job.get('description') or '')[:7000]}"""
    return llm.ask_json(prompt, model=model)

def score_batch(master_summary, jobs, prefs, model="sonnet"):
    items = [{"id": j["id"], "title": j["title"], "company": j["company"], "location": j["location"],
              "desc": (j.get("description") or "")[:1800]} for j in jobs]
    prompt = f"""Score how well each job fits this candidate. Candidate profile summary:
{master_summary}

Candidate preferences: wants {prefs.get('job_types')}, roles like: {prefs.get('roles')}; locations: {prefs.get('locations')}; remote ok: {prefs.get('remote_ok')};
experience level: {prefs.get('experience_level')}; avoid: {prefs.get('avoid_keywords')}; avoid companies: {prefs.get('avoid_companies')}.

Return a JSON list: [{{"id": <id>, "score": 0-100, "reason": "<=15 words", "skip": true/false}}]
skip=true if the job clearly needs more experience than the candidate has, is a different field, is unpaid/sales/BPO, or violates the preferences.

JOBS:
{json.dumps(items, ensure_ascii=False)}"""
    return llm.ask_json(prompt, model=model)

def master_summary(master):
    skills = [s for v in (master.get("skills") or {}).values() for s in v]
    exp = [f"{e.get('title')} @ {e.get('company')}" for e in master.get("experience") or []]
    edu = [f"{e.get('degree')} {e.get('institution')} {e.get('year')}" for e in master.get("education") or []]
    return f"{master.get('headline','')}. Skills: {', '.join(skills[:40])}. Experience: {'; '.join(exp[:5]) or 'none'}. Projects: {len(master.get('projects') or [])}. Education: {'; '.join(edu)}."

def safe_name(s): return re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")[:40]

def render_pdf(data, out_path):
    html = TPL.get_template("resume.html").render(r=data)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content(html, wait_until="load")
        pg.pdf(path=out_path, format="A4", margin={"top": "12mm", "bottom": "12mm", "left": "13mm", "right": "13mm"}, print_background=True)
        b.close()
    return out_path

def tailor_and_render(job, master, prefs, model=None):
    model = prefs.get("llm_model", "sonnet")
    t = tailor(master, job, prefs, model=model)
    # contact details always come from master/prefs (never let the model alter them)
    for k in ("name", "email", "phone", "location", "links"):
        if master.get(k): t[k] = master[k]
    fname = f"{job['platform']}_{safe_name(job['company'])}_{safe_name(job['title'])}_{datetime.date.today():%Y%m%d}.pdf"
    out = os.path.join(db.DATA, "tailored", fname)
    render_pdf(t, out)
    return t, out
