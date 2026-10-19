"""One persistent, logged-in browser profile shared by discovery and applying."""
import os, threading, contextlib
from . import db
PROFILE = os.path.join(db.ROOT, "browser_profile")
_lock = threading.Lock()

@contextlib.contextmanager
def open_context(headless=False):
    from playwright.sync_api import sync_playwright
    with _lock, sync_playwright() as p:
        kwargs = dict(headless=headless, viewport={"width": 1280, "height": 900},
                      args=["--disable-blink-features=AutomationControlled"], slow_mo=120)
        try:
            ctx = p.chromium.launch_persistent_context(PROFILE, channel="chrome", **kwargs)
        except Exception:
            ctx = p.chromium.launch_persistent_context(PROFILE, **kwargs)
        try: yield ctx
        finally:
            try: ctx.close()
            except Exception: pass

LOGIN_PAGES = {
    "linkedin": "https://www.linkedin.com/login",
    "indeed": "https://secure.indeed.com/auth",
    "naukri": "https://www.naukri.com/nlogin/login",
    "internshala": "https://internshala.com/login/user",
}

def login_session(platforms, wait_minutes=10):
    """Open the persistent profile with login tabs; user logs in manually; closes when all tabs closed or timeout."""
    with open_context(headless=False) as ctx:
        pages = []
        for p in platforms:
            pg = ctx.new_page(); pg.goto(LOGIN_PAGES[p]); pages.append(pg)
        for pg in ctx.pages:
            if pg not in pages and pg.url == "about:blank":
                try: pg.close()
                except Exception: pass
        import time
        deadline = time.time() + wait_minutes * 60
        while time.time() < deadline and len(ctx.pages) > 0:
            time.sleep(2)

def login_status():
    """Quick check of which platforms look logged in (cookie heuristics)."""
    with open_context(headless=True) as ctx:
        names = {c["name"] + "@" + c["domain"] for c in ctx.cookies()}
        return {
            "linkedin": any(n.startswith("li_at@") for n in names),
            "indeed": any(n.startswith("SHOE@") or n.startswith("PPID@") for n in names),
            "naukri": any(n.startswith("nauk_at@") or n.startswith("NKWAP@") for n in names),
            "internshala": any(n.startswith("PHPSESSID@") and "internshala" in n for n in names),
        }
