#!/usr/bin/env python3
"""Canvas Coach: read your Canvas and tell you what is due.

    python3 coach.py setup --host https://canvas.yourschool.edu
    python3 coach.py login --wait 300      # you log in, once, in the window that opens
    python3 coach.py deadlines             # collect if needed, print the list, write the page

Run `python3 coach.py --help` for every command. Read-only: nothing is ever sent to Canvas.
To add a command, drop a file named cc_cmd_<name>.py next to this one; see references/extending.md.
"""
import argparse
import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cc_browser  # noqa: E402
import cc_changes  # noqa: E402
import cc_deadlines  # noqa: E402
import cc_ingest  # noqa: E402
import cc_lang  # noqa: E402
import cc_model  # noqa: E402
import cc_store  # noqa: E402
import cc_time  # noqa: E402
from cc_cli import (BRIDGE_HINT, COMMANDS, FAILED, LOGIN_HINT, NEED_LOGIN, NEED_SETUP, OK, REFRESH_ARGS,  # noqa: E402,F401
                    arg, collect_now, command, describe, login_hint, no_data, open_file, refresh, remember_route, say,
                    write_page)
from cc_lang import T  # noqa: E402

PAGE_NAME = "Deadlines.html"


@command("setup", "Save your school's Canvas address (and optionally the data folder and timezone).",
         arg("--host", help="any Canvas page address, e.g. https://canvas.yourschool.edu"),
         arg("--root", help="folder for the page and data (default ~/CanvasCoach)"),
         arg("--timezone", help="e.g. America/Los_Angeles, or auto for this computer's timezone"))
def cmd_setup(args):
    cfg = cc_store.load_config()
    if args.host:
        cfg["host"] = cc_store.normalize_host(args.host)
    if args.root:
        cfg["root"] = str(Path(args.root).expanduser().resolve())
    if args.timezone:
        cfg["timezone"] = args.timezone
        cc_time.zone(cfg)
    cc_store.require_host(cfg)
    cc_store.save_config(cfg)
    cc_store.ensure_dirs(cfg)
    say(T("Saved. Canvas address: {host}", host=cfg["host"]), T("Data folder: {path}", path=cc_store.root(cfg)),
        T("Next: {hint}", hint=login_hint()))
    return OK


@command("login", "Open a browser window on Canvas so the student can log in (they type; nothing is stored by us).",
         arg("--wait", type=int, default=0, metavar="SECONDS", help="wait this long for the login, then collect"),
         arg("--restart", action="store_true", help="close an already open window and start again"))
def cmd_login(args):
    cfg = cc_store.load_config()
    if cc_browser.login_open(cfg, restart=args.restart):
        say(T("A browser window is now open on {host}. Log in there as you normally do.", host=cfg["host"]))
    else:
        say(T("The login window is already open."))
    if not args.wait:
        say(T("When it shows your Canvas dashboard, run: coach.py login --wait 60"))
        return OK
    name, where = cc_browser.login_wait(cfg, args.wait)
    if not name:
        say(T("Not logged in yet after {n} seconds. The window is still open, showing {where}.", n=args.wait, where=where)
            if where else T("Not logged in yet after {n} seconds. The window is still open.", n=args.wait),
            T("Finish logging in, then run: coach.py login --wait 300"),
            T("If no window is visible, run: coach.py login --restart --wait 300"))
        if where and where != cfg["host"]:
            say(T("If that page is already the Canvas dashboard, the address is different from the saved one. "
                  "Run: coach.py setup --host {where}   and then log in again.", where=where))
        return NEED_LOGIN
    say(T("Logged in as {name}. Collecting now; the window will close by itself.", name=name))
    payload, _ = collect_now(cfg)
    say(*describe(payload, cfg), T("Next: {hint}", hint="coach.py deadlines"))
    return OK


@command("logout", "Close the browser window and delete the saved Canvas login from this computer.")
def cmd_logout(args):
    cc_store.load_config()
    removed = cc_browser.forget()
    say(T("Saved login deleted ({n} file(s)). Your collected data was kept.", n=len(removed)))
    return OK


@command("collect", "Read Canvas now and save a snapshot.")
def cmd_collect(args):
    cfg = cc_store.load_config()
    payload, path = collect_now(cfg)
    say(*describe(payload, cfg), T("Saved: {path}", path=path))
    return OK


@command("deadlines", "Show what is overdue and coming up, and write the Deadlines page.",
         arg("--days", type=int, help="how many days 'next days' covers (default 7)"),
         arg("--all", action="store_true", help="also show old, finished and hidden items"),
         arg("--keys", action="store_true", help="show each item's key (for done / undone / show)"),
         *REFRESH_ARGS,
         arg("--json", action="store_true", help="print machine-readable JSON instead of text"),
         arg("--open", action="store_true", help="open the page in your browser afterwards"))
def cmd_deadlines(args):
    cfg = cc_store.load_config()
    if args.days is not None and not 1 <= args.days <= 366:
        raise ValueError(T("--days must be between 1 and 366."))
    payload, banner, code = refresh(cfg, args.force, args.offline)
    if payload is None:
        return no_data(banner, code)
    view = cc_deadlines.build_view(payload, cfg, days=args.days, show_all=args.all)
    changes, since = cc_changes.since_baseline(cfg, payload)
    page = write_page(cfg, PAGE_NAME, cc_deadlines.render_html(view, banner, cc_changes.render_html(changes, since)))
    if args.json:
        print(json.dumps(dict(cc_deadlines.jsonable(view), banner=banner, page=str(page), changes=changes,
                              changes_since=since), ensure_ascii=False, indent=1))
    else:
        say(cc_deadlines.render_text(view, banner, keys=args.keys, changes=cc_changes.render_text(changes, since)),
            T("Page: {path}", path=page))
        if code == NEED_LOGIN:
            say(login_hint())
    if args.open:
        open_file(page)
    return code


@command("status", "Show the setup, whether the browser tool is installed, and how old the data is.")
def cmd_status(args):
    cfg = cc_store.load_config()
    tz = cc_time.zone(cfg)
    here = cc_time.local(cc_time.now(), tz)
    lines = [T("Canvas address:  {value}", value=cfg.get("host") or T("(not set; run coach.py setup --host ...)")),
             T("Data folder:     {value}", value=cc_store.root(cfg)),
             T("Timezone:        {value}", value=T("{zone} (now {now})", zone=cfg.get("timezone"),
                                                 now=f"{cc_time.stamp(here)} {cc_time.zone_label(here)}")),
             T("Language:        {value}", value=cc_lang.lang()),
             T("Browser tool:    {value}", value=T("agent-browser found") if cc_browser.find_exe()
               else T("agent-browser NOT installed (see SKILL.md, fallback route)")),
             T("Login window:    {value}", value=T("open") if cc_browser.is_running() else T("closed"))]
    payload, path = cc_store.latest_snapshot(cfg) if cfg.get("host") else (None, None)
    if payload:
        collected = cc_time.parse_iso(payload.get("collected_at"))
        age = max(0, (cc_time.now() - collected).total_seconds() / 60)
        lines.append(T("Last collection: {value}", value=T("{when} ({relative})", when=cc_time.stamp(
            cc_time.local(collected, tz)), relative=cc_time.age_text(age))))
        lines += ["                 " + line for line in describe(payload, cfg)]
        lines.append(T("Snapshots kept:  {value}", value=T("{n} in {path}", n=len(cc_store.list_snapshots(cfg)),
                                                         path=path.parent)))
    else:
        lines.append(T("Last collection: {value}", value=T("none yet")))
    say(*lines)
    return OK


@command("courses", "List the courses Canvas returned, with their ids and whether they are shown.")
def cmd_courses(args):
    cfg = cc_store.load_config()
    payload, _ = cc_store.latest_snapshot(cfg)
    if not payload:
        say(T("No data yet. Run: coach.py collect"))
        return NEED_LOGIN
    for c in cc_model.course_table(payload, cfg, cc_time.now()):
        count = len(((payload.get("data") or {}).get(c["id"]) or {}).get("assignments") or [])
        state = T("hidden: {why}", why=c["hidden"]) if c["hidden"] else T("shown, {n} assignment(s)", n=count)
        if not c["hidden"] and not c["collected"]:
            state = T("shown, but not collected yet (collect again)")
        if c["errors"]:
            state += T(", could not be fully read")
        say(f"{c['id']:>10}  {c['label']}  |  {c['name'] or T('(no name)')}  |  {c['term'] or T('no term')}  |  {state}")
    say("", T("Hide a course: coach.py config ignore <id>     Show a hidden one: coach.py config include <id>"),
        T("Rename a course: coach.py config name <id> <label>"))
    return OK


@command("config", "Show or change settings: show | set KEY VALUE | ignore ID | include ID | name ID LABEL | reset KEY",
         arg("action", choices=["show", "set", "ignore", "include", "name", "reset"]),
         arg("values", nargs="*"))
def cmd_config(args):
    cfg = cc_store.load_config()
    action, values = args.action, args.values
    if action == "show":
        shown = {k: ("(saved)" if k in cc_store.SECRET_KEYS and v else v) for k, v in cfg.items()}
        print(json.dumps(shown, ensure_ascii=False, indent=1))
        return OK
    if action in ("ignore", "include") and len(values) == 1:
        mine, other = ("ignore_courses", "include_courses") if action == "ignore" else ("include_courses", "ignore_courses")
        cfg[mine] = sorted({str(x) for x in cfg.get(mine) or []} | {values[0]})
        cfg[other] = [str(x) for x in cfg.get(other) or [] if str(x) != values[0]]
    elif action == "name" and len(values) >= 2:
        cfg.setdefault("course_names", {})[values[0]] = " ".join(values[1:])
    elif action == "reset" and len(values) == 1 and values[0] in cc_store.DEFAULTS:
        cfg[values[0]] = cc_store.DEFAULTS[values[0]]
    elif action == "set" and len(values) >= 2 and values[0] in cc_store.DEFAULTS:
        key, value = values[0], " ".join(values[1:])
        if key == "host":
            cfg[key] = cc_store.normalize_host(value)
        elif key in cc_store.INT_KEYS:
            low, high = cc_store.INT_KEYS[key]
            if not value.strip().lstrip("-").isdigit() or not low <= int(value) <= high:
                raise ValueError(T("{key} must be a whole number between {low} and {high}.", key=key, low=low, high=high))
            cfg[key] = int(value)
        elif key in cc_store.BOOL_KEYS:
            if value.lower() not in ("on", "off", "true", "false", "yes", "no"):
                raise ValueError(T("{key} must be on or off.", key=key))
            cfg[key] = value.lower() in ("on", "true", "yes")
            if key == "inbox" and not cfg[key]:
                cleaned = cc_store.forget_resource(cfg, "conversations")
                say(T("The inbox will no longer be read, and its lines were removed from {n} earlier collection(s).",
                      n=cleaned))
        elif key == "week1_start":
            if value.lower() != "auto" and cc_time.parse_iso(value + "T00:00:00Z") is None:
                raise ValueError(T("week1_start must be a date like 2026-09-28, or auto."))
            cfg[key] = "" if value.lower() == "auto" else value
        elif key == "brief_time":
            raise ValueError(T("Change the briefing time with: coach.py schedule install --time HH:MM"))
        elif key == "class_times":
            raise ValueError(T("Set class times with: coach.py classes set <course> <days> <time>"))
        elif key in cc_store.SECRET_KEYS:
            raise ValueError(T("{key} is saved automatically and cannot be set by hand.", key=key))
        elif key in cc_store.CHOICES:
            value = cc_lang.ALIASES.get(value.lower(), value.lower()) if key == "language" else value
            if value not in cc_store.CHOICES[key]:
                raise ValueError(T("{key} must be one of: {choices}.", key=key, choices=", ".join(cc_store.CHOICES[key])))
            cfg[key] = value
        elif key == "rest_days":
            days = [v.strip().lower()[:3] for v in value.replace(",", " ").split() if v.strip().lower() != "none"]
            if any(d not in cc_time.DAY_CODES for d in days):
                raise ValueError(T("rest_days takes day names like: fri sat sun (or none)."))
            cfg[key] = [d for d in cc_time.DAY_CODES if d in days]
        elif key in cc_store.LIST_KEYS:
            cfg[key] = [v.strip() for v in value.split(",") if v.strip()]
        elif key in ("course_names", "version"):
            raise ValueError(T("Use: coach.py config name <course id> <label>"))
        else:
            cfg[key] = value
        cc_time.zone(cfg)
    else:
        raise ValueError(T("Usage: coach.py config show | set KEY VALUE | ignore ID | include ID | "
                           "name ID LABEL | reset KEY     Keys: {keys}",
                           keys=", ".join(k for k in cc_store.DEFAULTS if k != "version")))
    cc_store.save_config(cfg)
    cc_lang.use(cfg.get("language"))
    say(T("Saved."))
    return OK


@command("fetch-script", "Fallback route: print the collector JavaScript for an AI agent to run in a logged-in Canvas tab.",
         arg("--full", action="store_true", help="keep old finished work too (bigger result)"),
         arg("--everything", action="store_true", help="also modules, feedback and announcements (much bigger result)"),
         arg("--course", action="append", metavar="ID", help="collect only this course (repeatable)"))
def cmd_fetch_script(args):
    cfg = cc_store.load_config()
    cc_store.require_host(cfg)
    resources = cc_browser.load_resources(cfg)
    # A hand-copied result must stay short and must not carry the private calendar link.
    resources["global"] = [r for r in resources["global"] if args.everything and r["name"] != "profile"]
    if not args.everything:
        resources["per_course"] = [r for r in resources["per_course"] if r["name"] in ("assignments", "assignment_groups")]
    for r in resources["per_course"]:
        r.pop("inspect", None)                   # no instruction text travels through a hand-made copy
    source, settings = cc_browser.collector_script(cfg, slim_days=0 if args.full else 30, only=args.course,
                                                   resources=resources)
    # Browser script tools cut long text and deeply nested answers short, so the result is handed back
    # as a flat list of short text pieces; `ingest` joins them again.
    print(source + "\n(JSON.stringify(await window.__ccCollect(" + settings + ")).match(/[\\s\\S]{1,600}/g))")
    return OK


@command("ingest", "Fallback route: check and save collector results that were copied into files.",
         arg("files", nargs="+", help="file(s) holding the JSON the collector returned; - reads standard input"))
def cmd_ingest(args):
    cfg = cc_store.load_config()
    cc_store.require_host(cfg)
    payloads = []
    for name in args.files:
        text = sys.stdin.read() if name == "-" else Path(name).expanduser().read_text(encoding="utf-8")
        payloads.append(cc_ingest.parse_text(text))
    payload, path = cc_ingest.ingest(payloads, cfg)
    remember_route(cfg, "bridge")
    say(*describe(payload, cfg), T("Saved: {path}", path=path), T("Next: {hint}", hint="coach.py deadlines --offline"))
    return OK


@command("paths", "Print where the settings, data and page live.")
def cmd_paths(args):
    cfg = cc_store.load_config()
    say(T("Skill folder: {path}", path=Path(__file__).resolve().parent.parent),
        T("Settings:     {path}", path=cc_store.config_path()),
        T("Data folder:  {path}", path=cc_store.root(cfg)),
        T("Page:         {path}", path=cc_store.root(cfg) / PAGE_NAME),
        T("Snapshots:    {path}", path=cc_store.raw_dir(cfg)))
    return OK


class Parser(argparse.ArgumentParser):
    """argparse exits with 2 on a typo; here 2 means "not logged in", so use 1."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(FAILED, f"Problem: {message}\n")


def load_plugins():
    """Import every cc_cmd_*.py next to this file; each registers its own commands.

    A broken add-on must not take the deadline list down with it, so it is skipped with a warning.
    """
    for path in sorted(HERE.glob("cc_cmd_*.py")):
        before = dict(COMMANDS)
        try:
            importlib.import_module(path.stem)
        except (Exception, SystemExit) as e:
            COMMANDS.clear()
            COMMANDS.update(before)          # drop whatever it registered before it failed
            print(f"Note: the add-on {path.name} could not be loaded ({type(e).__name__}: {e}).", file=sys.stderr)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_plugins()
    parser = Parser(prog="coach.py", description="Canvas Coach: your Canvas deadlines, read-only.")
    sub = parser.add_subparsers(dest="command", metavar="command")
    for name, (fn, help_text, arguments) in COMMANDS.items():
        try:
            p = Parser(prog=f"coach.py {name}", description=help_text)
            for flags, options in arguments:
                p.add_argument(*flags, **options)
            p.format_help()                  # catches a bad help text now instead of at --help
        except Exception as e:               # one add-on's mistake must not break the other commands
            print(f"Note: the command '{name}' ({fn.__module__}) is defined wrongly and was skipped "
                  f"({type(e).__name__}: {e}).", file=sys.stderr)
            continue
        p = sub.add_parser(name, help=help_text, description=help_text)
        for flags, options in arguments:
            p.add_argument(*flags, **options)
        p.set_defaults(run=fn)
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return OK
    try:
        return args.run(args)
    except cc_ingest.NotLoggedIn as e:
        say(f"{e}", login_hint())
        return NEED_LOGIN
    except cc_store.ConfigError as e:
        say(f"{e}")
        return NEED_SETUP
    except cc_browser.BrowserMissing as e:
        say(f"{e}", T("Or use the fallback route described in SKILL.md (fetch-script + ingest)."))
        return FAILED
    except (cc_browser.BrowserError, cc_ingest.IngestError, cc_store.DataError, ValueError, OSError) as e:
        say(T("Problem: {why}", why=e))
        return FAILED
    except Exception as e:  # a bug or a damaged file: say so plainly instead of dumping a traceback
        say(T("Problem: unexpected error ({why}). Run coach.py status and report this message.",
              why=f"{type(e).__name__}: {e}"))
        return FAILED


if __name__ == "__main__":
    sys.exit(main())
