"""What every command shares: the command registry, exit codes, and "get me fresh data".

A command is a function decorated with @command. Built-in ones live in coach.py; any file
named cc_cmd_<something>.py in this folder is loaded automatically, so a new feature can be
added by dropping in one file (see references/extending.md).
"""
import os
import subprocess
import sys

import cc_browser
import cc_ingest
import cc_lang
import cc_model
import cc_store
import cc_time
from cc_lang import N, T

OK, FAILED, NEED_LOGIN, NEED_SETUP, NEED_BRIDGE = 0, 1, 2, 3, 4
LOGIN_HINT = N("Run: coach.py login --wait 300   (a window opens; the student logs in there themselves)")
BRIDGE_HINT = N("This student uses the fallback route. Refresh with: coach.py fetch-script, run it in their "
                "logged-in Canvas tab, coach.py ingest <file>, then coach.py deadlines --offline (see SKILL.md).")
STALE_NOTE = N(" This is from the last successful collection, so it may be out of date.")
COMMANDS = {}


def login_hint():
    return T(LOGIN_HINT)


def command(name, help_text, *arguments):
    """Register a subcommand. Each argument is (flags, argparse-options); build them with arg()."""
    def register(fn):
        if name in COMMANDS and COMMANDS[name][0].__module__ != fn.__module__:
            raise ValueError(f"the command '{name}' already exists (in {COMMANDS[name][0].__module__})")
        COMMANDS[name] = (fn, help_text.replace("%", "%%"), arguments)
        return fn
    return register


def arg(*flags, **options):
    return flags, options


# The three switches every data-reading command takes.
REFRESH_ARGS = (arg("--force", action="store_true", help="collect even if the last collection is recent"),
                arg("--offline", action="store_true", help="do not collect; use the last snapshot"))


def say(*lines):
    print("\n".join(lines))


def describe(payload, cfg):
    """One or two lines about what a collection contains."""
    data = payload.get("data") or {}
    count = sum(len(d.get("assignments") or []) for d in data.values() if isinstance(d, dict))
    when = cc_time.local(cc_time.parse_iso(payload.get("collected_at")), cc_time.zone(cfg))
    lines = [T("Collected {courses} course(s) and {n} assignment(s) at {when}.", courses=len(data), n=count,
               when=cc_time.stamp(when))]
    problems = [e for e in payload.get("errors") or [] if e.get("resource") in ("assignments", "assignment_groups")]
    if problems:
        import cc_deadlines
        labels = {c["id"]: c["label"] for c in cc_model.course_table(payload, cfg, cc_time.now())}
        lines.append(T("Could not read: {what}.", what=cc_lang.join((
            T("{resource} for {course} ({why})", resource=cc_deadlines.resource_word(e.get("resource")),
              course=labels.get(str(e.get("course_id")), "Canvas"), why=e.get("message")) for e in problems), "clauses")))
    return lines


def remember_route(cfg, route):
    """Note which way data last came in, so the next refresh goes the same way."""
    if cfg.get("collector") != route:
        cfg["collector"] = route
        cc_store.update_config(collector=route)


def collect_now(cfg):
    """Collect through the dedicated browser and save a snapshot. Returns (payload, path)."""
    cc_store.require_host(cfg)
    result = cc_ingest.ingest([cc_browser.collect(cfg)], cfg)
    remember_route(cfg, "browser")
    return result


def fetch_extra(cfg, requests):
    """Read a few more Canvas addresses right now, outside the regular collection.

    requests: [{"name": "page", "path": "/api/v1/courses/1/pages/x", "keep": [...] or None}, ...]
    Returns ({name: what Canvas answered}, [errors]). Needs the scripted browser route.
    Raises NotLoggedIn / BrowserError / IngestError like a collection does.
    """
    cc_store.require_host(cfg)
    resources = {"courses": None, "per_course": [], "global": [dict({"keep": None}, **r) for r in requests]}
    result = cc_browser.collect(cfg, resources=resources, only=["-"])
    cc_ingest.raise_for_error(result)
    if result.get("host") != cfg["host"]:
        raise cc_ingest.IngestError(T("The answer came from {got}, not {expected}.", got=result.get("host"),
                                      expected=cfg["host"]))
    return result.get("global") or {}, result.get("errors") or []


def fetch_files(cfg, jobs):
    """Download files that need the Canvas login, through the logged-in browser. Returns [True/False] per job."""
    try:
        return cc_browser.download_files(cfg, jobs)
    except cc_browser.LoginNeeded as e:
        raise cc_ingest.NotLoggedIn(str(e))


def uncollected(payload, cfg):
    """Courses that should be on the list but were never read (for example, just un-ignored)."""
    return [c for c in cc_model.course_table(payload, cfg, cc_time.now()) if not c["hidden"] and not c["collected"]]


def refresh(cfg, force=False, offline=False):
    """The newest data, collecting first when it is stale. Returns (payload, banner, code).

    payload is None only when nothing was ever collected. A non-empty banner means the
    refresh did not happen and the payload is the previous collection: show the banner.
    code is the exit code the command should end with (OK when fresh).
    """
    cc_store.require_host(cfg)
    payload, _ = cc_store.latest_snapshot(cfg)
    banner, code = "", OK
    collected = cc_time.parse_iso(payload.get("collected_at")) if payload else None
    age = (cc_time.now() - collected).total_seconds() / 60 if collected else None
    stale = (force or age is None or age < 0 or age > int(cfg.get("fresh_minutes") or 10)
             or bool(uncollected(payload, cfg)))
    if stale and not offline:
        if cfg.get("collector") == "bridge":
            banner, code = T("Not refreshed: {why}", why=T(BRIDGE_HINT)), NEED_BRIDGE
        else:
            try:
                payload, _ = collect_now(cfg)
            except cc_ingest.NotLoggedIn:
                banner, code = T("Could not refresh: {why}", why=T("you are not logged in to Canvas.")), NEED_LOGIN
            except cc_browser.BrowserMissing as e:
                banner, code = T("Could not refresh: {why}", why=T(
                    "{problem} Or use the fallback route in SKILL.md (fetch-script + ingest).", problem=e)), FAILED
            except (cc_browser.BrowserError, cc_ingest.IngestError) as e:
                banner, code = T("Could not refresh: {why}", why=e), FAILED
    if payload is None:
        banner = banner or T("No data yet.")
        code = code or NEED_LOGIN
    elif banner:
        banner += T(STALE_NOTE) + calendar_backup(cfg, payload)
    return payload, banner, code


def calendar_backup(cfg, payload):
    """When Canvas could not be read, the private calendar link can still reveal new or moved deadlines."""
    try:
        import cc_ical
        notes = cc_ical.backup_notes(cfg, payload)
    except Exception:
        notes = []
    return T(" Checked the Canvas calendar link instead: {notes}", notes=" ".join(notes)) if notes else ""


def data_age(payload, cfg):
    """'Mon Oct 12, 4:04 AM (5 min ago)': when this data was collected, for the last line of an answer."""
    when = cc_time.parse_iso(payload.get("collected_at"))
    if not when:
        return T("at an unknown time")
    minutes = max(0.0, (cc_time.now() - when).total_seconds() / 60)
    return T("{when} ({relative})", when=cc_time.stamp(cc_time.local(when, cc_time.zone(cfg))),
             relative=cc_time.age_text(minutes))


def no_data(banner, code):
    """Say why there is nothing to show, and return the exit code."""
    say(banner, {NEED_BRIDGE: "", FAILED: T("Nothing was collected before, so there is nothing to show.")}.get(code, login_hint()))
    return code


def no_such_course(words, table):
    """Say that the typed words do not pick out one course, list the choices, and return the exit code."""
    say(T("'{words}' does not match exactly one course. Your courses:", words=words),
        *[f"  {c['label']}" for c in table if not c["hidden"]])
    return FAILED


def open_file(path):
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    elif os.name == "nt":
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def write_page(cfg, name, html_text):
    """Save a page into the data folder and return its path."""
    path = cc_store.root(cfg) / name
    cc_store.ensure_dirs(cfg)
    path.write_text(html_text, encoding="utf-8")
    return path
