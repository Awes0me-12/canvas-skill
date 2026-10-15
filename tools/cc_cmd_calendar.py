"""Commands: export (write the deadlines as a calendar file) and feed (check Canvas's own calendar link)."""
import json

import cc_deadlines
import cc_ical
import cc_store
from cc_cli import (FAILED, NEED_LOGIN, OK, REFRESH_ARGS, arg, command, data_age, login_hint, no_data, open_file,
                    refresh, say)
from cc_lang import T, warning


@command("export", "Write the deadlines to a calendar file (.ics) you can add to Apple or Google Calendar.",
         arg("--all", action="store_true", help="include finished and old items too"),
         arg("--alarm", type=int, metavar="HOURS", help="add a reminder this many hours before each deadline"),
         *REFRESH_ARGS,
         arg("--open", action="store_true", help="open the file afterwards (your calendar app will offer to add it)"))
def cmd_export(args):
    cfg = cc_store.load_config()
    if args.alarm is not None and not 1 <= args.alarm <= 24 * 14:
        raise ValueError(T("--alarm must be between 1 and 336 hours."))
    payload, banner, code = refresh(cfg, args.force, args.offline)
    if payload is None:
        return no_data(banner, code)
    view = cc_deadlines.build_view(payload, cfg, show_all=args.all)
    text, count = cc_ical.export(view, cfg["host"], include_done=args.all, alarm_hours=args.alarm)
    path = cc_store.root(cfg) / cc_ical.FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    if banner:
        say(warning(banner))
    say(T("Wrote {n} deadline(s) to {path}", n=count, path=path), T("Data collected {when}.", when=data_age(payload, cfg)),
        T("To use it: double-click the file to add the events to Apple Calendar, or import it in Google Calendar."),
        T("Adding it again later updates the same events instead of duplicating them."))
    if code == NEED_LOGIN:
        say(login_hint())
    if args.open:
        open_file(path)
    return code


@command("feed", "Check Canvas's own calendar link for new or moved deadlines (works without logging in).",
         arg("--json", action="store_true", help="print machine-readable JSON instead of text"))
def cmd_feed(args):
    cfg = cc_store.load_config()
    payload, _ = cc_store.latest_snapshot(cfg)
    if not cfg.get("calendar_feed") or payload is None:
        say(T("No calendar link is saved yet. It is saved automatically at the next successful collection."))
        return FAILED
    try:
        notes = cc_ical.cross_check(cc_ical.parse(cc_ical.fetch(cfg["calendar_feed"])), payload, cfg, limit=200)
    except cc_ical.FeedError as e:
        say(T("Problem: {why}", why=e))
        return FAILED
    if args.json:
        print(json.dumps({"notes": notes}, ensure_ascii=False, indent=1))
    else:
        say(*notes or [T("The Canvas calendar agrees with the last collection.")])
        say(T("Compared with the collection of {when}.", when=data_age(payload, cfg)))
    return OK
