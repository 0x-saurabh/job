# Job Autopilot

Local dashboard that finds jobs on LinkedIn, Indeed, Naukri and Internshala, tailors your resume
to each job description (truthfully, from a master profile built from your uploaded resumes),
and applies, never twice to the same job.

## Start
    ./run.sh
then open http://127.0.0.1:8765

## First-time setup (in the dashboard)
1. Resumes tab: drop 2-3 resumes (PDF/DOCX).
2. Preferences tab: roles, locations, job types, standard answers. Save.
3. Dashboard: "Open login window", log in to the 4 sites in the window that opens, close it. "Check" shows status.
4. Press "Run today's applications".

## Daily
Open the dashboard, press "Run today's applications". With auto-submit OFF (default) it fills every form and
leaves the Submit click to you; those jobs show under "Needs you" with the tab left open. Turn auto-submit ON
once you trust the output.

## Layout
- app/           FastAPI server + pipeline (discover -> score -> tailor -> apply)
- data/app.db    all jobs, statuses, resumes, settings (dedup source of truth)
- data/tailored/ one PDF per job
- browser_profile/  the logged-in browser profile used for Indeed/Naukri/Internshala discovery and all applying
- .env           APIFY_TOKEN (LinkedIn discovery, ~$0.80 per 1000 jobs)
- candidates/, scripts/fetch_linkedin.py  standalone LinkedIn fetch (optional)

Claude calls go through your local `claude` CLI login (no separate API key).
