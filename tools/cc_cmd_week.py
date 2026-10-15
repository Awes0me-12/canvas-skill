"""Commands: week (this week's modules, deadlines and news) and changes (what moved on Canvas)."""
import json

import cc_changes
import cc_deadlines
import cc_state
import cc_store
import cc_time
import cc_week
from cc_cli import data_age as cc_cli_age
from cc_cli import NEED_LOGIN, OK, REFRESH_ARGS, arg, command, login_hint, no_data, open_file, refresh, say, write_page
from cc_lang import T, warning

PAGE_NAME = "This Week.html"


@command("week", "This week per course: the current module, what is due, and recent announcements.",
         *REFRESH_ARGS,
         arg("--json", action="store_true", help="print machine-readable JSON instead of text"),
         arg("--open", action="store_true", help="open the page in your browser afterwards"))
def cmd_week(args):
    cfg = cc_store.load_config()
    payload, banner, code = refresh(cfg, args.force, args.offline)
    if payload is None:
        return no_data(banner, code)
    view = cc_week.build_view(payload, cfg)
    changes, since = cc_changes.since_baseline(cfg, payload)
    page = write_page(cfg, PAGE_NAME, cc_week.render_html(view, banner, cc_changes.render_html(changes, since)))
    if args.json:
        print(json.dumps(dict(cc_deadlines.jsonable(view), banner=banner, page=str(page),
                              changes=changes, changes_since=since), ensure_ascii=False, indent=1))
    else:
        say(cc_week.render_text(view, banner, cc_changes.render_text(changes, since)), T("Page: {path}", path=page))
        if code == NEED_LOGIN:
            say(login_hint())
    if args.open:
        open_file(page)
    return code


@command("changes", "What changed on Canvas since you last looked: new work, moved dates, grades, announcements.",
         *REFRESH_ARGS,
         arg("--keep", action="store_true", help="do not mark these changes as seen"),
         arg("--log", action="store_true", help="show the changes already reported before"),
         arg("--json", action="store_true", help="print machine-readable JSON instead of text"))
def cmd_changes(args):
    cfg = cc_store.load_config()
    if args.log:
        log = cc_state.load(cfg).get("change_log") or []
        tz = cc_time.zone(cfg)
        when = lambda text: cc_time.stamp(cc_time.local(cc_time.parse_iso(text), tz)) if cc_time.parse_iso(text) else "?"
        say(*[f"{when(entry.get('at'))}  {entry.get('text')}" for entry in log if isinstance(entry, dict)]
            or [T("Nothing has been reported yet.")])
        return OK
    payload, banner, code = refresh(cfg, args.force, args.offline)
    if payload is None:
        return no_data(banner, code)
    changes, since = cc_changes.since_baseline(cfg, payload, advance=not args.keep)
    if args.json:
        print(json.dumps({"since": since, "changes": changes, "banner": banner}, ensure_ascii=False, indent=1))
        return code
    if banner:
        say(warning(banner))
    say(*cc_changes.render_text(changes, since, limit=50) or
        [T("Nothing changed on Canvas since {since}.", since=since) if since
         else T("Nothing to compare yet: this is the first look.")])
    say(T("Data collected {when}.", when=cc_cli_age(payload, cfg)))
    return code
