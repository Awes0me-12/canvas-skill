"""Collect through a dedicated browser window, driven by the `agent-browser` CLI.

You log in to Canvas yourself, once, in a window this opens. The login is then
kept (encrypted) by agent-browser and reused quietly until the school expires it.
Nothing here ever types a username or password.
"""
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import cc_store
from cc_lang import T

def _session_name():
    """The name agent-browser keeps the saved login under.

    Whoever redirects the settings folder ($CANVAS_COACH_HOME: tests, trial runs) gets a session of
    their own, so that nothing done there - least of all `logout` - can reach the real saved login.
    """
    if os.environ.get("CANVAS_COACH_SESSION"):
        return os.environ["CANVAS_COACH_SESSION"]
    home = os.environ.get("CANVAS_COACH_HOME")
    if not home:
        return "canvas-coach"
    return "cc-other-" + hashlib.sha1(str(Path(home).expanduser().resolve()).encode()).hexdigest()[:10]


SESSION = _session_name()
WHOAMI = "/api/v1/users/self"
_HERE = Path(__file__).resolve().parent


class BrowserError(Exception):
    pass


class BrowserMissing(BrowserError):
    pass


class LoginNeeded(BrowserError):
    pass


def find_exe():
    return shutil.which(os.environ.get("CANVAS_COACH_AGENT_BROWSER") or "agent-browser")


def _env():
    """agent-browser encrypts the saved login when this key is set."""
    env = dict(os.environ)
    if "AGENT_BROWSER_ENCRYPTION_KEY" not in env:
        key_file = cc_store.home() / "browser.key"
        if not key_file.exists():
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_text(secrets.token_hex(32))
            os.chmod(key_file, 0o600)
        env["AGENT_BROWSER_ENCRYPTION_KEY"] = key_file.read_text().strip()
    return env


def _run(args, stdin=None, timeout=60, headed=False, restore=True):
    exe = find_exe()
    if not exe:
        raise BrowserMissing(T("The agent-browser command is not installed. Install it with: "
                               "npm i -g agent-browser && agent-browser install"))
    cmd = [exe, "--session", SESSION, "--json"]
    if restore:
        cmd.append("--restore")
    if headed:
        cmd.append("--headed")
    try:
        p = subprocess.run(cmd + list(args), input=stdin, capture_output=True, text=True,
                           timeout=timeout, env=_env())
    except subprocess.TimeoutExpired:
        raise BrowserError(T("The browser did not answer in time."))
    except OSError as e:
        raise BrowserMissing(T("Could not start agent-browser: {why}", why=e))
    reply = None
    for line in reversed(p.stdout.strip().splitlines()):
        try:
            reply = json.loads(line)
            break
        except ValueError:
            continue
    if not isinstance(reply, dict):
        raise BrowserError((p.stderr or p.stdout or T("agent-browser gave no answer")).strip()[:400])
    if not reply.get("success"):
        raise BrowserError(str(reply.get("error") or T("agent-browser failed"))[:400])
    return reply.get("data") or {}


def is_running():
    exe = find_exe()
    if not exe:
        return False
    try:
        p = subprocess.run([exe, "session", "list"], capture_output=True, text=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return any(line.strip() == SESSION for line in p.stdout.splitlines())


def _marker():
    """Exists while a visible login window that we opened is up."""
    return cc_store.home() / "login-window.open"


def close():
    """Close the window. agent-browser saves the login as it goes."""
    _marker().unlink(missing_ok=True)
    if not is_running():
        return
    try:
        data = _run(["close"], timeout=30)
    except BrowserError:
        data = {}
    path = data.get("statePath")
    if path and os.path.exists(path):
        os.chmod(path, 0o600)
    for _ in range(40):  # reopening before the old one is gone leaves a stray browser behind
        if not is_running():
            break
        time.sleep(0.25)
    time.sleep(0.5)


def open_url(url, headed=False):
    return _run(["open", url], timeout=90, headed=headed)


def current_url():
    return str(_run(["get", "url"], timeout=20).get("url") or "")


def evaljs(js, timeout=45):
    return _run(["eval", "--stdin"], stdin=js, timeout=timeout).get("result")


def on_host(host):
    return (current_url() + "/").startswith(host + "/")


def whoami(host):
    """The logged-in user's name, or None. Only looks when the window is on Canvas itself."""
    if not on_host(host):
        return None
    js = ("(async()=>{try{const r=await fetch('" + WHOAMI + "',{credentials:'same-origin',"
          "headers:{Accept:'application/json'}});if(!r.ok)return null;"
          "const j=JSON.parse((await r.text()).replace(/^while\\(1\\);/,''));return j.name||j.short_name||'you';}"
          "catch(e){return null}})()")
    return evaljs(js)


def load_resources(cfg):
    """resources.json, without entries whose "option" setting is switched off."""
    raw = json.loads((_HERE / "resources.json").read_text(encoding="utf-8"))
    on = lambda rows: [r for r in rows or [] if not r.get("option") or cfg.get(r["option"])]
    return {"courses": raw.get("courses"), "per_course": on(raw.get("per_course")), "global": on(raw.get("global"))}


def collector_script(cfg, slim_days=0, only=None, resources=None):
    """(fetch.js, its settings as JSON). `resources` replaces resources.json for one-off requests."""
    settings = {"host": cfg["host"], "resources": resources or load_resources(cfg), "max_pages": 50,
                "skip_courses": [str(x) for x in cfg.get("ignore_courses") or []],
                "only_courses": [str(x) for x in only or []], "slim_days": slim_days}
    return (_HERE / "fetch.js").read_text(encoding="utf-8"), json.dumps(settings, ensure_ascii=True)


def run_collector(cfg, resources=None, only=None):
    """Run fetch.js in the open Canvas page and return its raw result dict.

    The run is started and then polled, because one long call can outlast the
    browser tool's own time limit. The result is read back in slices for the same reason.
    """
    source, settings = collector_script(cfg, resources=resources, only=only)
    start = (source + "\nwindow.__cc={state:'running'};\nwindow.__ccCollect(" + settings + ").then("
             "function(r){window.__cc={state:'done',json:JSON.stringify(r)}},"
             "function(e){window.__cc={state:'error',message:String(e&&e.message||e)}});\n'started'")
    evaljs(start)
    deadline = time.time() + int(cfg.get("collect_timeout") or 180)
    while True:
        state = evaljs("window.__cc?window.__cc.state:'none'")
        if state == "done":
            break
        if state == "error":
            raise BrowserError(T("The collector failed: {why}", why=str(evaljs("window.__cc.message"))))
        if state != "running":
            raise BrowserError(T("The Canvas page changed while collecting. Try again."))
        if time.time() > deadline:
            raise BrowserError(T("Collecting took too long and was stopped."))
        time.sleep(0.4)
    size = int(evaljs("window.__cc.json.length"))
    step = 200000
    text = "".join(evaljs(f"window.__cc.json.slice({i},{i + step})", timeout=90) for i in range(0, size, step))
    return json.loads(text)


def _quiet_relogin(host):
    """If the school still remembers this browser, visiting Canvas logs back in by itself."""
    open_url(host + "/")
    for _ in range(6):
        time.sleep(2)
        try:
            if whoami(host):
                return True
        except BrowserError:
            pass
    return False


def _origin(url):
    parts = url.split("/")
    return "/".join(parts[:3]) if len(parts) >= 3 and parts[0] in ("http:", "https:") else ""


def _moved_canvas(result):
    """If the school sent us to a different address that is itself a logged-in Canvas, say so."""
    origin = _origin(str(result.get("origin") or ""))
    if origin and whoami(origin):
        raise BrowserError(T("Your school's Canvas is actually at {origin}. Run: coach.py setup --host {origin}", origin=origin))


def collect(cfg, resources=None, only=None):
    """Open Canvas, run the collector, close. Returns the collector's result dict.

    With no arguments it reads everything in resources.json. Pass `resources` for a
    one-off request (see fetch_extra in cc_cli.py).
    """
    host = cc_store.require_host(cfg)
    try:
        open_url(host + WHOAMI)
        result = run_collector(cfg, resources, only)
        # Landing on another site (usually the school's login page) also means "not logged in here".
        if result.get("error") in ("unauthenticated", "wrong_origin"):
            if _quiet_relogin(host):
                open_url(host + WHOAMI)
                result = run_collector(cfg, resources, only)
            elif result.get("error") == "wrong_origin":
                _moved_canvas(result)
                result = {"error": "unauthenticated", "detail": f"redirected to {result.get('origin')}"}
        return result
    finally:
        close()


def download_files(cfg, jobs, timeout=600):
    """Download Canvas files whose address only works while logged in. Returns [True or False] per job.

    jobs: [(address on the student's Canvas, destination path)]. The file is fetched by the browser
    itself, exactly as if the student had clicked the link, so the login never leaves the browser.
    Raises LoginNeeded when the saved login has expired.
    """
    host = cc_store.require_host(cfg)
    done = []
    try:
        open_url(host + WHOAMI)
        if not whoami(host) and not (_quiet_relogin(host) and open_url(host + WHOAMI) is not None and whoami(host)):
            raise LoginNeeded(T("Canvas says you are not logged in."))
        for address, dest in jobs:
            if not str(address).startswith(host + "/"):
                done.append(False)
                continue
            link = ("(function(){var a=document.getElementById('cc-dl')||document.createElement('a');a.id='cc-dl';"
                    "a.href=" + json.dumps(str(address)) + ";a.textContent='file';a.setAttribute('download','');"
                    "document.body.appendChild(a);return 'ready'})()")
            try:
                evaljs(link)
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                _run(["download", "#cc-dl", str(dest)], timeout=timeout)
                done.append(Path(dest).exists())
            except BrowserError:
                done.append(False)
        return done
    finally:
        close()


def login_open(cfg, restart=False):
    """Show a real browser window on Canvas for the user to log in. Returns True if it opened one."""
    host = cc_store.require_host(cfg)
    if is_running():
        alive = False
        if not restart and _marker().exists():   # ours, and visible: but is the browser really still there?
            try:
                alive = current_url().startswith("http")
            except BrowserError:
                alive = False
        if alive:
            return False
        close()
    open_url(host + "/", headed=True)
    _marker().parent.mkdir(parents=True, exist_ok=True)
    _marker().write_text("open\n")
    return True


def login_wait(cfg, seconds):
    """Wait until the window is logged in to Canvas. Returns (name or None, the site the window is on)."""
    host = cc_store.require_host(cfg)
    deadline = time.time() + seconds
    while True:
        try:
            name = whoami(host)
        except BrowserError:
            name = None
        if name or time.time() >= deadline:
            break
        time.sleep(2)
    try:
        where = _origin(current_url())
    except BrowserError:
        where = ""
    return name, where


def forget():
    """Close the window and delete the saved login."""
    close()
    removed = []
    base = Path("~/.agent-browser").expanduser()
    for folder, pattern in ((base / "sessions", SESSION + "-*"), (base, SESSION + ".*")):
        if folder.is_dir():
            for f in folder.glob(pattern):
                if f.is_file():
                    f.unlink()
                    removed.append(str(f))
    key = cc_store.home() / "browser.key"
    if key.exists():
        key.unlink()
    return removed
