"""Apply handlers per platform. Each returns (status, note): status in applied | needs_you | failed."""
import os, re, json, time, datetime
from . import db, llm

YES_WORDS = ("authori", "eligible", "legally", "relocat", "available", "start immediately", "comfortable", "willing", "agree", "confirm", "18", "commit", "notice")
NO_WORDS = ("sponsor", "visa")

def _shot(page, job):
    path = os.path.join(db.DATA, "screenshots", f"{job['platform']}_{job['job_id']}_{int(time.time())}.png")
    try: page.screenshot(path=path, full_page=False)
    except Exception: return ""
    return path

def _answer_for(label, prefs, tailored, job, model):
    """Map a form question to an answer from prefs, else ask Claude a short answer."""
    l = label.lower()
    rules = [
        (("phone", "mobile", "contact number"), prefs.get("phone")),
        (("email",), prefs.get("email")),
        (("first name",), (prefs.get("name") or "").split(" ")[0]),
        (("last name", "surname"), (prefs.get("name") or "").split(" ")[-1]),
        (("full name", "your name", "name"), prefs.get("name")),
        (("linkedin",), prefs.get("linkedin_url")),
        (("github",), prefs.get("github_url")),
        (("portfolio", "website"), prefs.get("portfolio_url") or prefs.get("github_url")),
        (("notice period",), str(prefs.get("notice_period_days", 0))),
        (("current ctc", "current salary", "current compensation"), str(prefs.get("current_ctc_lpa", 0))),
        (("expected ctc", "expected salary", "salary expectation", "desired salary", "expected compensation"), str(prefs.get("expected_ctc_lpa", 0))),
        (("city", "location", "where are you based"), prefs.get("city")),
        (("graduat", "passing year", "year of completion"), str(prefs.get("graduation_year", ""))),
        (("years of experience", "years of work experience", "how many years"), "0" if prefs.get("experience_level") == "fresher" else prefs.get("experience_level", "0").split("-")[0]),
        (("start date", "when can you join", "joining"), prefs.get("earliest_start", "Immediately")),
    ]
    for keys, val in rules:
        if any(k in l for k in keys) and val not in (None, ""): return str(val)
    try:
        return llm.ask_text(f"""Answer this job application question in first person, truthfully, in at most 3 sentences (or a single number/word if the question asks for one). Use ONLY facts from this profile; if unknown say so briefly and positively.
Question: {label}
Job: {job['title']} at {job['company']}
Profile: {json.dumps(tailored, ensure_ascii=False)[:5000]}
Preferences: experience={prefs.get('experience_level')}, notice period={prefs.get('notice_period_days')} days, expected ctc={prefs.get('expected_ctc_lpa')} LPA, relocate={prefs.get('willing_to_relocate')}, authorization={prefs.get('work_authorization')}
Reply with the answer text only.""", model=model)
    except Exception: return ""

def _fill_generic_form(scope, page, prefs, tailored, job, model, log, pdf_path):
    """Fill visible inputs within `scope` (a Locator). Returns list of unanswered required labels."""
    unanswered = []
    # file inputs
    for fi in scope.locator("input[type=file]").all():
        try: fi.set_input_files(pdf_path); log("  uploaded resume"); page.wait_for_timeout(1500)
        except Exception as e: log(f"  file upload failed: {e}")
    # text / number / textarea
    for inp in scope.locator("input:not([type=file]):not([type=hidden]):not([type=radio]):not([type=checkbox]):not([type=submit]):not([type=button]), textarea").all():
        try:
            if not inp.is_visible(): continue
            if (inp.input_value() or "").strip(): continue
            label = _label_for(inp, scope)
            if not label: continue
            ans = _answer_for(label, prefs, tailored, job, model)
            if ans:
                inp.fill(ans[:1000]); log(f"  '{label[:50]}' -> '{ans[:40]}'")
                page.wait_for_timeout(300)
                # typeahead dropdowns: pick first suggestion if offered
                sug = page.locator("[role=listbox] [role=option], .search-typeahead__hit").first
                if sug.count() and sug.is_visible():
                    sug.click(); page.wait_for_timeout(300)
            else: unanswered.append(label)
        except Exception as e: log(f"  input error: {e}")
    # radio groups and selects
    for fs in scope.locator("fieldset, [role=radiogroup]").all():
        try:
            if not fs.is_visible(): continue
            if fs.locator("input[type=radio]:checked").count(): continue
            radios = fs.locator("input[type=radio]")
            if not radios.count(): continue
            legend = (fs.locator("legend, [data-test-form-builder-radio-button-form-component__title], span").first.inner_text() or "").strip()
            want = "yes"
            if any(w in legend.lower() for w in NO_WORDS): want = "no"
            picked = False
            for r in radios.all():
                lab = _label_for(r, fs).lower()
                if lab.startswith(want) or (want == "yes" and lab in ("true", "y")):
                    r.check(force=True); picked = True; break
            if not picked: radios.first.check(force=True)
            log(f"  radio '{legend[:50]}' -> {want}")
        except Exception as e: log(f"  radio error: {e}")
    for sel in scope.locator("select").all():
        try:
            if not sel.is_visible(): continue
            cur = sel.input_value()
            opts = [o.inner_text().strip() for o in sel.locator("option").all()]
            if cur and cur not in ("", "Select an option") and not cur.lower().startswith("select"): continue
            label = _label_for(sel, scope)
            ans = _answer_for(label, prefs, tailored, job, model).lower()
            choice = next((o for o in opts if o.lower() == ans), None) or next((o for o in opts if ans and ans in o.lower()), None)
            if not choice: choice = next((o for o in opts if o.lower().startswith("yes")), None) or (opts[1] if len(opts) > 1 else None)
            if choice: sel.select_option(label=choice); log(f"  select '{label[:40]}' -> {choice}")
        except Exception as e: log(f"  select error: {e}")
    # required checkboxes (consent)
    for cb in scope.locator("input[type=checkbox][required], input[type=checkbox][aria-required=true]").all():
        try:
            if cb.is_visible() and not cb.is_checked(): cb.check(force=True)
        except Exception: pass
    return unanswered

def _label_for(el, scope):
    try:
        id_ = el.get_attribute("id")
        if id_:
            lab = scope.locator(f"label[for='{id_}']")
            if lab.count(): return lab.first.inner_text().strip()
        for attr in ("aria-label", "placeholder", "name"):
            v = el.get_attribute(attr)
            if v: return v.strip()
        lab = el.locator("xpath=ancestor::label[1]")
        if lab.count(): return lab.first.inner_text().strip()
        lab = el.locator("xpath=preceding::label[1]")
        if lab.count(): return lab.first.inner_text().strip()
    except Exception: pass
    return ""

def _click_first(page, selectors, timeout=3000):
    for s in selectors:
        try:
            b = page.locator(s).first
            if b.count() and b.is_visible():
                b.click(timeout=timeout); return s
        except Exception: continue
    return None

# ---------------- LinkedIn ----------------
def linkedin(page, job, prefs, tailored, pdf_path, log, submit):
    model = prefs.get("llm_model", "sonnet")
    page.goto(job["url"], wait_until="domcontentloaded"); page.wait_for_timeout(3000)
    if "/login" in page.url or "authwall" in page.url: return "failed", "not logged in to LinkedIn"
    if page.locator("span.artdeco-inline-feedback__message:has-text('Applied'), .jobs-s-apply__applied-date, text=/Applied .* ago/").count():
        return "applied", "already applied on LinkedIn"
    easy = page.locator("button.jobs-apply-button:has-text('Easy Apply'), button:has-text('Easy Apply')").first
    if not easy.count():
        ext = page.locator("button.jobs-apply-button:has-text('Apply'), a:has-text('Apply on company website')").first
        return "needs_you", "external application (not Easy Apply); open the link and apply with the tailored PDF"
    easy.click(); page.wait_for_timeout(2000)
    modal = page.locator(".jobs-easy-apply-modal, div[role=dialog]").first
    for step in range(12):
        unanswered = _fill_generic_form(modal, page, prefs, tailored, job, model, log, pdf_path)
        if unanswered: log(f"  unanswered: {unanswered}")
        if modal.locator("button[aria-label='Submit application'], button:has-text('Submit application')").count():
            try:
                fu = modal.locator("label:has-text('Follow'), input#follow-company-checkbox")
                if fu.count() and modal.locator("input#follow-company-checkbox").is_checked(): modal.locator("label[for=follow-company-checkbox]").click()
            except Exception: pass
            if not submit: return "needs_you", "review mode: form filled, waiting on Submit. Switch on auto-submit or press Submit yourself."
            modal.locator("button[aria-label='Submit application'], button:has-text('Submit application')").first.click(); page.wait_for_timeout(3000)
            return "applied", "submitted via Easy Apply"
        nxt = _click_first(page, ["button[aria-label='Continue to next step']", "button[aria-label='Review your application']", "button:has-text('Next')", "button:has-text('Review')"])
        page.wait_for_timeout(1500)
        if page.locator(".artdeco-inline-feedback--error, [role=alert]:visible").count() and step > 0:
            errs = page.locator(".artdeco-inline-feedback--error").all_inner_texts()
            if errs: return "needs_you", f"form errors: {errs[:3]}"
        if not nxt: break
    return "needs_you", "could not reach Submit; finish manually"

# ---------------- Internshala ----------------
def internshala(page, job, prefs, tailored, pdf_path, log, submit):
    model = prefs.get("llm_model", "sonnet")
    page.goto(job["url"], wait_until="domcontentloaded"); page.wait_for_timeout(2500)
    if "/login" in page.url: return "failed", "not logged in to Internshala"
    if page.locator("text=Already applied, text=Applied, .already_applied").count(): return "applied", "already applied"
    if not _click_first(page, ["button#continue_button", "button:has-text('Apply now')", "a:has-text('Apply now')"]):
        return "needs_you", "Apply button not found"
    page.wait_for_timeout(3000)
    # resume upload (optional), cover letter, questions
    try:
        fi = page.locator("input[type=file]").first
        if fi.count(): fi.set_input_files(pdf_path); log("  uploaded resume"); page.wait_for_timeout(2000)
    except Exception: pass
    cover = page.locator("textarea#cover_letter, .ql-editor, textarea[name*=cover]").first
    if cover.count():
        try: cover.fill(tailored.get("cover_note", "")); log("  cover note filled")
        except Exception: cover.click(); page.keyboard.type(tailored.get("cover_note", ""))
    for ta in page.locator("textarea").all():
        try:
            if not ta.is_visible() or ta.input_value().strip(): continue
            qtxt = ta.locator("xpath=preceding::label[1] | xpath=ancestor::div[contains(@class,'form-group')][1]//label").first
            qtxt = qtxt.inner_text().strip() if qtxt.count() else _label_for(ta, page)
            if not qtxt: continue
            ans = _answer_for(qtxt, prefs, tailored, job, model); ta.fill(ans); log(f"  Q '{qtxt[:50]}' answered")
        except Exception as e: log(f"  q error: {e}")
    # availability radio -> Yes
    try:
        yes = page.locator("input[type=radio][value*='yes' i], label:has-text('Yes, I am available')").first
        if yes.count(): yes.click(force=True)
    except Exception: pass
    if not submit: return "needs_you", "review mode: form filled; press Submit yourself or enable auto-submit"
    if _click_first(page, ["input#submit", "button:has-text('Submit')", "input[type=submit]"]):
        page.wait_for_timeout(3000); return "applied", "submitted on Internshala"
    return "needs_you", "Submit not found"

# ---------------- Naukri ----------------
def naukri(page, job, prefs, tailored, pdf_path, log, submit):
    model = prefs.get("llm_model", "sonnet")
    page.goto(job["url"], wait_until="domcontentloaded"); page.wait_for_timeout(3000)
    if page.locator("#already-applied, span:has-text('Applied'), .already-applied").count(): return "applied", "already applied"
    if page.locator("button:has-text('Apply on company site'), #company-site-button").count():
        return "needs_you", "Apply on company site (external)"
    if not submit: return "needs_you", "review mode: Naukri applies with one click using your profile resume. Press Apply yourself or enable auto-submit."
    if not _click_first(page, ["#apply-button", "button:has-text('Apply')"]): return "needs_you", "Apply button not found (login?)"
    page.wait_for_timeout(3500)
    # chatbot-style screening questions
    for _ in range(8):
        bot = page.locator(".chatbot_Drawer, [class*=chatbot]").first
        if not bot.count() or not bot.is_visible(): break
        q = bot.locator(".botMsg, [class*=botMsg], .msg").last
        qt = q.inner_text().strip() if q.count() else ""
        radios = bot.locator("input[type=radio], .ssrc__radio-btn-container label, [class*=radio] label")
        if radios.count():
            lab = [r.inner_text().strip() for r in radios.all()]
            pick = next((i for i, l in enumerate(lab) if l.lower().startswith("yes")), 0)
            radios.nth(pick).click(force=True)
        else:
            inp = bot.locator("div[contenteditable=true], textarea, input[type=text]").first
            if inp.count():
                ans = _answer_for(qt, prefs, tailored, job, model) if qt else "Yes"
                inp.click(); inp.fill(ans) if inp.evaluate("e=>e.tagName") != "DIV" else page.keyboard.type(ans)
        _click_first(page, [".sendMsg", "div.sendMsg", "button:has-text('Save')", "button:has-text('Send')"])
        page.wait_for_timeout(1500)
    if page.locator("text=/successfully applied|You have applied|Applied/i").count(): return "applied", "applied on Naukri (profile resume)"
    return "applied", "apply clicked on Naukri; verify in your Naukri applied list"

# ---------------- Indeed ----------------
def indeed(page, job, prefs, tailored, pdf_path, log, submit):
    model = prefs.get("llm_model", "sonnet")
    page.goto(job["url"], wait_until="domcontentloaded"); page.wait_for_timeout(3000)
    if page.locator("text=Applied, [data-testid='applied-status']").count(): return "applied", "already applied"
    if page.locator("a:has-text('Apply on company site'), button:has-text('Apply on company site')").count():
        return "needs_you", "Apply on company site (external)"
    if not _click_first(page, ["#indeedApplyButton", "button:has-text('Apply now')"]): return "needs_you", "Apply button not found"
    page.wait_for_timeout(4000)
    for step in range(12):
        # resume step
        if page.locator("input[type=file]").count():
            try: page.locator("input[type=file]").first.set_input_files(pdf_path); log("  uploaded resume"); page.wait_for_timeout(2500)
            except Exception as e: log(f"  upload failed: {e}")
        elif page.locator("[data-testid='ResumeOptionsMenu'], text=Upload resume").count():
            _click_first(page, ["text=Upload resume", "[data-testid='ResumeOptionsMenu'] button"]); page.wait_for_timeout(1500); continue
        _fill_generic_form(page.locator("main, form, body").first, page, prefs, tailored, job, model, log, pdf_path)
        if page.locator("button:has-text('Submit your application'), button:has-text('Submit application')").count():
            if not submit: return "needs_you", "review mode: form filled, waiting on Submit"
            _click_first(page, ["button:has-text('Submit your application')", "button:has-text('Submit application')"]); page.wait_for_timeout(3000)
            return "applied", "submitted via Indeed Apply"
        if not _click_first(page, ["button:has-text('Continue')", "button[data-testid='continue-button']", "button:has-text('Review your application')", "button:has-text('Next')"]): break
        page.wait_for_timeout(2000)
    return "needs_you", "could not reach Submit; finish manually"

HANDLERS = {"linkedin": linkedin, "internshala": internshala, "naukri": naukri, "indeed": indeed}

def apply_job(ctx, job, prefs, log, submit):
    tailored = json.loads(job["tailored_json"] or "{}")
    page = ctx.new_page()
    try:
        status, note = HANDLERS[job["platform"]](page, job, prefs, tailored, job["tailored_pdf"], log, submit)
    except Exception as e:
        status, note = "failed", f"{type(e).__name__}: {str(e)[:200]}"
    shot = _shot(page, job) if status != "applied" else ""
    if status == "needs_you":
        # leave the tab open so the user can finish by hand
        pass
    else:
        try: page.close()
        except Exception: pass
    db.update_job(job["id"], status=status, note=note, screenshot=shot,
                  applied_at=db.now() if status == "applied" else None)
    log(f"{job['platform']} {job['company']} / {job['title']}: {status} - {note}")
    return status
