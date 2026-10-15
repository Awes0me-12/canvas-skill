"""Commands: brief (the short summary, optionally as a desktop notification) and schedule (run it daily)."""
import os
from pathlib import Path

import cc_brief
import cc_changes
import cc_schedule
import cc_state
import cc_store
import cc_time
from cc_cli import FAILED, NEED_LOGIN, OK, REFRESH_ARGS, arg, command, login_hint, say
from cc_lang import N, T


def _guarded(path):
    """The protected folder (Documents, Desktop or Downloads) that `path` lies in, or None.

    Compared by identity, not by spelling, so ~/documents and symlinks are caught too.
    """
    protected = [Path.home() / name for name in ("Documents", "Desktop", "Downloads")]
    path = Path(path).expanduser().absolute()
    for candidate in [path, *path.parents]:
        for folder in protected:
            try:
                if candidate.exists() and folder.exists() and os.path.samefile(candidate, folder):
                    return folder
            except OSError:
                continue
    return None


@command("brief", "The short version: today's must-do, what is due in the next 48 hours, what is overdue, what changed.",
         arg("--hours", type=int, default=48, help="how far ahead to look (default 48)"),
         arg("--notify", action="store_true", help="also show it as a desktop notification (macOS)"),
         arg("--scheduled", action="store_true", help="used by the daily schedule: do nothing unless today's briefing is due"),
         *REFRESH_ARGS)
def cmd_brief(args):
    cfg = cc_store.load_config()
    if not 1 <= args.hours <= 24 * 14:
        raise ValueError(T("--hours must be between 1 and 336."))
    if args.scheduled:
        now_local = cc_time.local(cc_time.now(), cc_time.zone(cfg))
        if not cfg.get("host") or not cc_schedule.due(cfg.get("brief_time"), now_local, cc_state.load(cfg).get("briefing")):
            return OK                                   # not time yet, or already done today: stay silent
    code = _brief(cfg, args)
    if args.scheduled:
        # Logged out counts as delivered (the notification says so); a network failure is tried again later.
        with cc_store.state_lock(cfg):
            state = cc_state.load(cfg)
            state["briefing"] = cc_schedule.after_run(state.get("briefing"), now_local, code != FAILED)
            cc_state.save(cfg, state)
    return code


def _brief(cfg, args):
    from cc_cli import refresh
    payload, banner, code = refresh(cfg, args.force, args.offline)
    if payload is None:
        say(banner, login_hint() if code == NEED_LOGIN else "")
        if args.notify and code == NEED_LOGIN:
            cc_brief.notify(T("Canvas Coach: please log in"),
                            T("Ask your assistant to check Canvas; a login window will open."))
        return code
    brief = cc_brief.build(payload, cfg, hours=args.hours)
    try:                                     # today's must-do leads the briefing; a plan problem must not silence it
        import cc_plan
        brief["must"] = cc_plan.todays_must(payload, cfg, brief["view"])
    except Exception:
        brief["must"] = None
    # The briefing never marks changes as seen: the student has not looked at the full list yet.
    changes, since = cc_changes.since_baseline(cfg, payload, advance=False)
    say(cc_brief.render_text(brief, cc_changes.render_text(changes, since, limit=8, more="coach.py changes"), banner))
    if args.notify:
        note = cc_brief.notification(brief, changes, banner, logged_out=code == NEED_LOGIN)
        if note:
            shown = cc_brief.notify(*note)
            say(T("Notification shown.") if shown else T("Notification could not be shown on this computer."))
        else:
            say(T("Nothing urgent, so no notification was shown."))
    return code


@command("schedule", "Run the briefing by itself every day: schedule show | install --time HH:MM | remove",
         arg("action", nargs="?", default="show", choices=["show", "install", "remove"]),
         arg("--time", default="08:00", help="24-hour time of day for the daily briefing (default 08:00)"))
def cmd_schedule(args):
    try:
        if args.action == "install":
            cc_store.require_host(cc_store.load_config())
            hour, minute = cc_schedule.parse_time(args.time)
            places = {N("data folder"): cc_store.root(cc_store.load_config()), N("skill folder"): Path(__file__).resolve(),
                      N("settings folder"): cc_store.home()}
            for what, where in places.items():
                inside = _guarded(where)
                if inside:
                    say(T("Problem: {why}", why=T(
                        "the {what} ({where}) is inside {inside}. macOS does not let a background job read "
                        "Documents, Desktop or Downloads without asking, so the briefing would hang on a permission prompt.",
                        what=T(what), where=where, inside=inside)),
                        T("Move it out first (for example to ~/CanvasCoach; then: coach.py setup --root ~/CanvasCoach)."))
                    return FAILED
            cc_store.update_config(brief_time=f"{hour:02d}:{minute:02d}")
            cc_schedule.install(Path(__file__).resolve().parent / "coach.py")
            say(T("Scheduled: a Canvas briefing every day at {time}, shown as a notification when "
                  "something needs attention.", time=f"{hour:02d}:{minute:02d}"),
                T("If this Mac is asleep or off at that time, the briefing comes soon after it is back."),
                T("Its log: {path}", path=Path(cc_schedule.LOG).expanduser()), T("Stop it with: coach.py schedule remove"))
            return OK
        if args.action == "remove":
            cc_store.load_config()
            say(T("The daily briefing is switched off.") if cc_schedule.remove() else T("There was no schedule to remove."))
            return OK
        info = cc_schedule.status()
        cfg = cc_store.load_config()
        when = cfg.get("brief_time") or "08:00"
        if not info["installed"]:
            say(T("No automatic briefing is scheduled."), T("Start one with: coach.py schedule install --time 08:00"))
        elif not info["loaded"] or not info["current"]:
            say(T("A briefing is set for {time} every day, but its job needs renewing. Run: "
                  "coach.py schedule install --time {time}", time=when))
        else:
            last = cc_state.load(cfg).get("briefing") if cfg.get("host") else None
            said = T("A briefing is scheduled every day at {time}.", time=when)
            if isinstance(last, dict) and last.get("day"):
                said += (T(" Last one: {day}.", day=last.get("day")) if last.get("done")
                         else T(" Last one: {day} (did not get through).", day=last.get("day")))
            else:
                said += T(" None has run yet.")
            say(said)
        return OK
    except cc_schedule.ScheduleError as e:
        say(T("Problem: {why}", why=e))
        return FAILED
