"""Commands for what the student tells us: done / undone (ticks), add / edit / confirm / remove
(own deadlines, and own dates or notes on Canvas items), marks (list it all), and ask / questions /
answer (things to ask a teacher)."""
import cc_deadlines
import cc_lang
import cc_model
import cc_state
import cc_store
import cc_time
from cc_cli import FAILED, OK, arg, command, say
from cc_lang import T, note

DETAILS = (arg("--kind", choices=cc_state.KINDS, help="what it is: exam, assignment, reading or other"),
           arg("--weight", type=float, metavar="PERCENT", help="how much of the course grade it is worth, e.g. 25"),
           arg("--points", type=float, help="how many points it is worth"),
           arg("--url", help="a link that goes with it"),
           arg("--place", help="where it happens, e.g. a room"),
           arg("--source", help="where the date came from, e.g. \"syllabus page 3\""),
           arg("--unconfirmed", action="store_true", help="the date is not certain yet"))


def _all_items(cfg):
    """Every item the student could mean, including old and hidden ones."""
    payload, _ = cc_store.latest_snapshot(cfg)
    if payload is None:
        state = cc_state.load(cfg)
        at = cc_time.now()
        tz = cc_time.zone(cfg)
        mine = cc_state.apply_ticks(cc_state.manual_items(state, at), state)
        for it in mine:
            it["closes_only"], it["deadline"] = False, it["due"]
            cc_deadlines._decorate(it, cc_time.local(at, tz), tz)
        return [], mine, []
    view = cc_deadlines.build_view(payload, cfg, show_all=True)
    items = [i for sec in view["sections"] for i in sec["items"]]
    return items, [], view["courses"]


def _describe(it):
    name = f"{it['course']} · {it['name']}" if it["course"] else it["name"]
    return name + (T(" (due {when})", when=it["due_text"]) if it.get("due_text") else "")


def _find(cfg, words):
    """(item, courses) for the words, or (None, courses) after saying why not."""
    items, extra, courses = _all_items(cfg)
    if len(words) == 1 and (words[0].startswith("mod:") or "#" in words[0]):
        # A task that exists only in the study plan: a week's module reading, one review or preparation session.
        return cc_model.blank_item(key=words[0], name=T("study-plan task ({key})", key=words[0])), courses
    try:
        return cc_state.find(items + extra, words), courses
    except LookupError as e:
        say(str(e))
        return None, courses


def _again(cfg, key):
    """The item as it stands after a change, for reading back to the student."""
    items, extra, _ = _all_items(cfg)
    return next((i for i in items + extra if i["key"] == key), None)


def _read_back(it):
    return " — ".join(x for x in [_describe(it), it.get("status_text"), it.get("worth_text")] + list(it.get("notes") or []) if x)


@command("done", "Tick an item off yourself (for work Canvas cannot see, or to clear a placeholder).",
         arg("words", nargs="+", help="the item's key, or a few words of its course and title"))
def cmd_done(args):
    cfg = cc_store.load_config()
    it, _ = _find(cfg, args.words)
    if it is None:
        return FAILED
    if it["done"] and it["status"] != "ticked":
        say(T("Canvas already shows this as {status}: {item}", status=it.get("status_text") or it["status"], item=_describe(it)))
        return OK
    cc_state.tick(cfg, it["key"], True)
    say(T("Marked done: {item}", item=_describe(it)), T("Undo with: coach.py undone {key}", key=it["key"]))
    return OK


@command("undone", "Remove your own tick from an item.",
         arg("words", nargs="+", help="the item's key, or a few words of its course and title"))
def cmd_undone(args):
    cfg = cc_store.load_config()
    it, _ = _find(cfg, args.words)
    if it is None:
        return FAILED
    cc_state.tick(cfg, it["key"], False)
    say(T("Tick removed: {item}", item=_describe(it)))
    return OK


@command("add", "Add a deadline that Canvas does not list (from a syllabus, an email, the lecture, a club or a job).",
         arg("title", nargs="+", help="what is due"),
         arg("--course", default="", help="the course it belongs to (a few words of its name), or any name of your own"),
         arg("--due", default="", help='when: 2026-10-20 or "2026-10-20 17:00" in your timezone; leave out if unknown'),
         arg("--note", default="", help="anything worth remembering"),
         *DETAILS)
def cmd_add(args):
    cfg = cc_store.load_config()
    due = cc_state.parse_when(args.due, cfg) if args.due else None
    payload, _ = cc_store.latest_snapshot(cfg)
    courses = cc_model.course_table(payload, cfg, cc_time.now()) if payload else []
    entry = cc_state.add_manual(cfg, courses, args.course, " ".join(args.title), due, args.note, kind=args.kind,
                                weight=args.weight, points=args.points, url=args.url, place=args.place,
                                source=args.source, confirmed=False if args.unconfirmed else None)
    when = cc_time.stamp(cc_time.local(due, cc_time.zone(cfg))) if due else T("no date")
    say(T("Added [{id}]: {name}, due {when}.", id=entry["id"], name=f"{entry['course'] or T('My own')} · {entry['title']}",
          when=when),
        T("Change it with: coach.py edit {id} ...   Remove it with: coach.py remove {id}", id=entry["id"]))
    if args.course and not entry["course_id"]:
        say(note(T("'{words}' did not match exactly one of your courses, so it is filed under that name as typed.",
                   words=args.course)))
    return OK


@command("edit", "Change a deadline you added, or put your own date or note on a Canvas item.",
         arg("words", nargs="+", help="its id (m1), its key, or a few words of its course and title"),
         arg("--title", help="a new title (own deadlines only)"),
         arg("--course", help="move it to another course (own deadlines only)"),
         arg("--due", help='the date: 2026-10-20 or "2026-10-20 17:00" in your timezone'),
         arg("--no-due", action="store_true", help="remove the date (on a Canvas item: go back to Canvas's own date)"),
         arg("--note", help='a note; "" removes it'),
         arg("--also", metavar="DATE", help="a second date another source gives; kept until you confirm which is right"),
         arg("--also-source", default="", help="where that second date came from"),
         *DETAILS)
def cmd_edit(args):
    cfg = cc_store.load_config()
    it, courses = _find(cfg, args.words)
    if it is None:
        return FAILED
    due = cc_state.parse_when(args.due, cfg) if args.due else None
    confirmed = False if args.unconfirmed else None
    if it["manual"]:
        also = (cc_state.parse_when(args.also, cfg), args.also_source) if args.also else None
        cc_state.edit_manual(cfg, it["id"], courses, course_text=args.course, due=due, clear_due=args.no_due, also=also,
                             title=args.title, note=args.note, kind=args.kind, weight=args.weight, points=args.points,
                             url=args.url, place=args.place, source=args.source, confirmed=confirmed)
    else:
        own_only = [flag for flag, value in (("--title", args.title), ("--course", args.course), ("--kind", args.kind),
                                             ("--weight", args.weight), ("--points", args.points), ("--url", args.url),
                                             ("--place", args.place), ("--also", args.also)) if value is not None]
        if own_only:
            say(T("{item} comes from Canvas, so only its date, note and source can be yours ({flags} cannot be set).",
                  item=_describe(it), flags=", ".join(own_only)))
            return FAILED
        if due is None and not args.no_due and args.note is None and args.source is None and confirmed is None:
            say(T("Say what to change: --due, --no-due, --note or --source."))
            return FAILED
        if confirmed is False and due is None and not it["own_due"]:
            say(T("{item} has no date of your own to confirm. Set one with: coach.py edit {key} --due ...",
                  item=_describe(it), key=it["key"]))
            return FAILED
        cc_state.set_override(cfg, it["key"], it["canvas_due"] if it["own_due"] else it["due"], due=due,
                              clear_due=args.no_due, note=args.note, source=args.source, confirmed=confirmed)
    now = _again(cfg, it["key"])
    say(T("Changed: {item}", item=_read_back(now) if now else _describe(it)))
    if now and not now["manual"] and now["own_due"]:
        say(note(T("The list now goes by your date. If Canvas changes its own date later, the change list will say so. "
                   "Back to Canvas's date: coach.py edit {key} --no-due", key=it["key"])))
    return OK


@command("confirm", "Settle a date that was not certain (or that two sources gave differently).",
         arg("words", nargs="+", help="its id (m1), its key, or a few words of its course and title"),
         arg("--due", help="the date that turned out to be right, if it is not the one shown"))
def cmd_confirm(args):
    cfg = cc_store.load_config()
    it, _ = _find(cfg, args.words)
    if it is None:
        return FAILED
    due = cc_state.parse_when(args.due, cfg) if args.due else None
    if it["alt_dates"] and due is None:
        tz = cc_time.zone(cfg)
        dates = [(it["due"], it["source"])] + list(it["alt_dates"])
        say(T("{item} has been given more than one date. Say which is right with --due:", item=_describe(it)),
            *[f"  {cc_time.local(when, tz).strftime('%Y-%m-%d %H:%M')}" + (f"  ({source})" if source else "")
              for when, source in dates if when])
        return FAILED
    if cc_state.confirm(cfg, it["key"], due, canvas_due=it["canvas_due"] if it["own_due"] else it["due"]) is None:
        say(T("{item} has no date of your own to confirm. Set one with: coach.py edit {key} --due ...",
              item=_describe(it), key=it["key"]))
        return FAILED
    now = _again(cfg, it["key"])
    say(T("Confirmed: {item}", item=_read_back(now) if now else _describe(it)))
    return OK


@command("remove", "Remove a deadline you added yourself.",
         arg("id", help="its id, like m1 (shown by: coach.py marks)"))
def cmd_remove(args):
    cfg = cc_store.load_config()
    if cc_state.remove_manual(cfg, args.id):
        say(T("Removed {id}.", id=args.id))
        return OK
    say(T("There is no own deadline called {id}. See: coach.py marks", id=args.id))
    return FAILED


@command("ask", "Note a question to ask a teacher or TA later.",
         arg("text", nargs="+", help="the question"),
         arg("--course", default="", help="the course it is about"),
         arg("--about", nargs="+", metavar="WORD", help="the item it is about (key or a few words)"))
def cmd_ask(args):
    cfg = cc_store.load_config()
    key, courses = "", []
    if args.about:
        it, courses = _find(cfg, args.about)
        if it is None:
            return FAILED
        key = it["key"]
    else:
        payload, _ = cc_store.latest_snapshot(cfg)
        courses = cc_model.course_table(payload, cfg, cc_time.now()) if payload else []
    entry = cc_state.add_question(cfg, " ".join(args.text), courses, args.course or (it["course"] if key else ""), key)
    say(T("Noted [{id}]: {text}", id=entry["id"], text=entry["text"]),
        T("When you have the answer: coach.py answer {id} <what they said>", id=entry["id"]))
    return OK


@command("questions", "List the questions you still want to ask.",
         arg("--all", action="store_true", help="also the ones already answered"))
def cmd_questions(args):
    cfg = cc_store.load_config()
    say(*_question_lines(cfg, args.all))
    return OK


@command("answer", "Close a question once you have the answer.",
         arg("id", help="its id, like q1"),
         arg("text", nargs="*", help="what the answer was"),
         arg("--remove", action="store_true", help="delete the question instead of keeping it with its answer"))
def cmd_answer(args):
    cfg = cc_store.load_config()
    entry = cc_state.answer_question(cfg, args.id, " ".join(args.text), remove=args.remove)
    if entry is None:
        say(T("There is no question called {id}. See: coach.py questions", id=args.id))
        return FAILED
    say(T("Removed {id}.", id=args.id) if args.remove else T("Closed [{id}]: {text}", id=entry["id"], text=entry["text"]))
    return OK


def _question_lines(cfg, everything=False):
    tz = cc_time.zone(cfg)
    day = lambda text: cc_time.day(cc_time.local(cc_time.parse_iso(text), tz)) if cc_time.parse_iso(text) else "?"
    lines = [T("Questions to ask:")]
    for q in cc_state.questions(cc_state.load(cfg), everything):
        line = f"  [{q['id']}] " + (f"{q['course']}: " if q.get("course") else "") + q["text"] + " " + T("(noted {day})", day=day(q.get("asked")))
        if q.get("answered"):
            line += " " + T("— answered {day}: {answer}", day=day(q["answered"]), answer=q.get("answer") or T("(no answer written down)"))
        lines.append(line)
    return lines if len(lines) > 1 else lines + [T("  none")]


@command("marks", "List what you told Canvas Coach: your own deadlines, dates, notes, ticks and questions.")
def cmd_marks(args):
    cfg = cc_store.load_config()
    state = cc_state.load(cfg)
    items, extra, _ = _all_items(cfg)
    by_key = {i["key"]: i for i in items + extra}
    lines = [T("Deadlines you added:")]
    for e in state["manual_deadlines"]:
        it = by_key.get(f"m:{e.get('id')}")
        lines.append(f"  [{e.get('id')}] " + (_read_back(it) if it else str(e.get("title"))))
    if len(lines) == 1:
        lines.append(T("  none"))
    lines.append(T("Your own dates and notes on Canvas items:"))
    mine = [by_key[key] for key in state["overrides"] if key in by_key]
    lines += [f"  {_describe(i)} — " + cc_lang.join(i.get("own_notes") or [], "clauses") + f"  [key {i['key']}]" for i in mine] \
        or [T("  none")]
    gone = [key for key in state["overrides"] if key not in by_key]
    if gone:
        lines.append(T("  ({n} more belong to items that are no longer on Canvas: {keys})", n=len(gone), keys=", ".join(gone)))
    lines.append(T("Items you ticked off:"))
    lines += [f"  {_describe(by_key[key]) if key in by_key else key}  [key {key}]" for key in state["done"]] or [T("  none")]
    lines += _question_lines(cfg)
    say(*lines)
    return OK
