"""Bridge to Claude via the local `claude` CLI (uses the user's existing Claude Code login)."""
import subprocess, json, re, os, shutil
CLAUDE = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")

def _extract_json(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m: text = m.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0] or [0])
    text = text[start:]
    # trim trailing junk after the last closing bracket
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[:end+1])

def ask_json(prompt, model="sonnet", timeout=400):
    """Send a prompt, expect JSON back. Raises on failure."""
    full = prompt + "\n\nRespond with ONLY valid JSON. No prose, no markdown fences."
    cmd = [CLAUDE, "-p", full, "--output-format", "json", "--model", model,
           "--permission-mode", "plan", "--max-turns", "1"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=os.path.expanduser("~"))
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI failed ({r.returncode}): {r.stderr[:500]}")
    try:
        outer = json.loads(r.stdout)
        text = outer.get("result", r.stdout) if isinstance(outer, dict) else r.stdout
    except json.JSONDecodeError:
        text = r.stdout
    return _extract_json(text)

def ask_text(prompt, model="sonnet", timeout=300):
    cmd = [CLAUDE, "-p", prompt, "--output-format", "text", "--model", model,
           "--permission-mode", "plan", "--max-turns", "1"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=os.path.expanduser("~"))
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI failed ({r.returncode}): {r.stderr[:500]}")
    return r.stdout.strip()
