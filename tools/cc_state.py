"""What the student told us: ticks, deadlines Canvas does not know about, their own dates and
notes on Canvas items, and questions still to ask a teacher.

Kept in <data folder>/.data/state.json:
  {"version": 2,
   "done": {"<item key>": "<when it was ticked, UTC>"},
   "manual_deadlines": [{"id": "m1", "course": "HIST 101", "course_id": "12345" or "", "title": "...",
                         "due": "<UTC>" or null, "note": "...",
                         "kind": "exam" | "assignment" | "reading" | "other", "weight": 25 or null (% of grade),
                         "points": 100 or null, "url": "", "place": "", "source": "",
                         "confirmed": true, "alt": [{"due": "<UTC>", "source": "TA email"}]}],
   "overrides": {"<Canvas item key>": {"due": "<UTC>" or null, "note": "", "source": "", "confirmed": true,
                                        "canvas_due": "<UTC>" or null}},     (canvas_due: what Canvas said back then)
   "questions": [{"id": "q1", "text": "...", "course": "", "course_id": "", "item": "<key>" or "",
                  "asked": "<UTC>", "answered": "<UTC>" or null, "answer": ""}],
   "plan": {...}                                                 (belongs to cc_plan.py)
   "baseline": "<snapshot file name>", "change_log": [...]}      (belong to cc_changes.py)

Entries written by an earlier version simply lack the newer fields; every reader fills in the default.
"""
import re
from datetime import datetime, time, timezone

import cc_model
import cc_store
import cc_text
import cc_time
from cc_lang import T

KINDS = ("exam", "assignment", "reading", "other")


def load(cfg):
    state = cc_store.load_state(cfg)
    if not isinstance(state, dict):
        state = {}
    state.setdefault("version", 2)
    for key, empty in (("done", dict), ("manual_deadlines", list), ("overrides", dict), ("questions", list)):
        if not isinstance(state.get(key), empty):
            state[key] = empty()
    return state


def save(cfg, state):
    cc_store.ensure_dirs(cfg)
    cc_store.save_state(cfg, state)


def parse_when(text, cfg):
    """'2026-10-20', '2026-10-20 17:00' or a full ISO time, in the student's timezone -> UTC.

    A date without a time means the end of that day (11:59 PM), the way Canvas does it.
    """
    text = (text or "").strip()
    tz = cc_time.zone(cfg)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?)?", text)
    if m:                                    # no zone written: the student's own clock is meant
        y, mo, d, h, mi = m.groups()
        try:
            naive = datetime.combine(datetime(int(y), int(mo), int(d)).date(),
                                     time(int(h), int(mi)) if h is not None else time(23, 59))
        except ValueError:
            raise ValueError(T("'{text}' is not a real date or time.", text=text))
        local = naive.replace(tzinfo=tz) if tz else naive.astimezone()
        return local.astimezone(timezone.utc)
    when = cc_time.parse_iso(text)
    if when is None or "T" not in text:
        raise ValueError(T("Cannot read the date '{text}'. Use 2026-10-20 or \"2026-10-20 17:00\".", text=text))
    return when


def _iso(when):
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if when else None


def _words(text):
    return " ".join(str(text or "").split())


def match_course(courses, text):
    """The course row a few typed words refer to (by id, label or name), or None."""
    words = (text or "").strip().lower()
    if not words:
        return None
    exact = [c for c in courses if words in (c["id"], c["label"].lower(), c["name"].lower())]
    loose = [c for c in courses if words in c["label"].lower() or words in c["name"].lower()]
    found = exact or [c for c in loose if not c["hidden"]] or loose
    return found[0] if len(found) == 1 else None


def _checked(details):
    """The optional details of an own deadline, cleaned; raises ValueError for a value that makes no sense."""
    out = {}
    for key, value in details.items():
        if value is None:
            continue
        if key == "kind":
            if value not in KINDS:
                raise ValueError(T("The kind must be one of: {kinds}.", kinds=", ".join(KINDS)))
            out[key] = value
        elif key == "weight":
            if not 0 <= float(value) <= 100:
                raise ValueError(T("The weight is a percentage of the course grade, between 0 and 100."))
            out[key] = float(value)
        elif key == "points":
            if float(value) < 0:
                raise ValueError(T("Points cannot be negative."))
            out[key] = float(value)
        elif key == "url":
            if value and not cc_text.web_link(value):
                raise ValueError(T("The link must start with https:// or http://"))
            out[key] = str(value).strip()
        elif key in ("place", "source", "note", "title"):
            out[key] = _words(value)
        elif key == "confirmed":
            out[key] = bool(value)
    return out


def add_manual(cfg, courses, course_text, title, due, note="", **details):
    """Remember a deadline Canvas does not list. `due` is a UTC datetime or None. Returns the entry.

    details: kind, weight (% of grade), points, url, place, source, confirmed.
    """
    title = _words(title)
    if not title:
        raise ValueError(T("Say what is due: the title is empty."))
    extra = _checked(details)
    course = match_course(courses, course_text)
    with cc_store.state_lock(cfg):
        state = load(cfg)
        used = [int(e["id"][1:]) for e in state["manual_deadlines"] if re.fullmatch(r"m\d+", str(e.get("id")))]
        entry = {"id": f"m{max(used, default=0) + 1}", "course": course["label"] if course else _words(course_text),
                 "course_id": course["id"] if course else "", "title": title, "due": _iso(due), "note": _words(note)}
        entry.update(extra)
        state["manual_deadlines"].append(entry)
        save(cfg, state)
    return entry


def _entry(state, entry_id):
    wanted = str(entry_id).split(":")[-1]
    return next((e for e in state["manual_deadlines"] if isinstance(e, dict) and e.get("id") == wanted), None)


def edit_manual(cfg, entry_id, courses=(), course_text=None, due=None, clear_due=False, also=None, **details):
    """Change an own deadline in place. Returns the entry, or None when there is no such id.

    `also` is (UTC datetime, source): a second date from another source. It is kept beside the first
    one and the entry counts as not confirmed until `confirm` settles which is right.
    """
    changes = _checked(details)
    if "title" in changes and not changes["title"]:
        raise ValueError(T("Say what is due: the title is empty."))
    with cc_store.state_lock(cfg):
        state = load(cfg)
        entry = _entry(state, entry_id)
        if entry is None:
            return None
        entry.update(changes)
        if course_text is not None:
            course = match_course(courses, course_text)
            entry["course"], entry["course_id"] = (course["label"], course["id"]) if course else (_words(course_text), "")
        if clear_due:
            entry["due"] = None
        elif due is not None:
            entry["due"] = _iso(due)
        if also:
            when, source = also
            others = [a for a in entry.get("alt") or [] if isinstance(a, dict) and a.get("due") != _iso(when)]
            if _iso(when) != entry.get("due"):
                entry["alt"] = others + [{"due": _iso(when), "source": _words(source)}]
                entry["confirmed"] = False
        save(cfg, state)
    return entry


def remove_manual(cfg, entry_id):
    wanted = entry_id.split(":")[-1]
    with cc_store.state_lock(cfg):
        state = load(cfg)
        keep = [e for e in state["manual_deadlines"] if e.get("id") != wanted]
        removed = len(keep) != len(state["manual_deadlines"])
        state["manual_deadlines"] = keep
        state["done"].pop("m:" + wanted, None)
        if removed:
            save(cfg, state)
    return removed


def set_override(cfg, key, canvas_due, due=None, clear_due=False, note=None, source=None, confirmed=None):
    """The student's own date and/or note on a Canvas item. `canvas_due` is what Canvas says right now,
    kept so that a later change on Canvas can be pointed out. Returns the stored record."""
    with cc_store.state_lock(cfg):
        state = load(cfg)
        record = state["overrides"].get(key) if isinstance(state["overrides"].get(key), dict) else {}
        if clear_due:
            record.pop("due", None)
            record.pop("canvas_due", None)
            record.pop("confirmed", None)
        elif due is not None:
            record["due"], record["canvas_due"] = _iso(due), _iso(canvas_due)
            record["confirmed"] = True if confirmed is None else bool(confirmed)
        elif confirmed is not None and record.get("due"):
            record["confirmed"] = bool(confirmed)
        if note is not None:
            record["note"] = _words(note)
        if source is not None:
            record["source"] = _words(source)
        record = {k: v for k, v in record.items() if v not in ("", None) or k == "canvas_due"}
        if record.get("due") or record.get("note"):
            state["overrides"][key] = record
        else:
            state["overrides"].pop(key, None)
            record = {}
        save(cfg, state)
    return record


def confirm(cfg, key, due=None, canvas_due=None):
    """Settle a date: mark it confirmed, optionally choosing `due` (UTC) as the right one. Returns the
    stored record, or None when `key` is neither an own deadline nor a Canvas item with an own date."""
    with cc_store.state_lock(cfg):
        state = load(cfg)
        if str(key).startswith("m:"):
            entry = _entry(state, key)
            if entry is None:
                return None
            dates = entry_dates(entry)
            if due is not None:
                entry["due"] = _iso(due)
            elif len({d for d, _ in dates}) > 1:
                raise ValueError(T("This deadline has been given more than one date; say which is right with --due."))
            elif dates:                          # the one date there is, wherever it was written down
                entry["due"] = _iso(dates[0][0])
                if dates[0][1] and not entry.get("source"):
                    entry["source"] = dates[0][1]
            entry["confirmed"] = True
            entry.pop("alt", None)
            save(cfg, state)
            return entry
        record = state["overrides"].get(key)
        if not isinstance(record, dict) or not (record.get("due") or due):
            return None
        if due is not None:
            record["due"] = _iso(due)
        record["confirmed"], record["canvas_due"] = True, _iso(canvas_due)
        save(cfg, state)
        return record


def tick(cfg, key, on=True):
    with cc_store.state_lock(cfg):
        state = load(cfg)
        if on:
            state["done"][key] = _iso(cc_time.now())
        else:
            state["done"].pop(key, None)
        save(cfg, state)


def add_question(cfg, text, courses=(), course_text="", item_key=""):
    """Note something to ask a teacher or TA. Returns the entry."""
    text = _words(text)
    if not text:
        raise ValueError(T("Say what to ask: the question is empty."))
    course = match_course(courses, course_text)
    with cc_store.state_lock(cfg):
        state = load(cfg)
        used = [int(q["id"][1:]) for q in state["questions"] if isinstance(q, dict) and re.fullmatch(r"q\d+", str(q.get("id")))]
        entry = {"id": f"q{max(used, default=0) + 1}", "text": text,
                 "course": course["label"] if course else _words(course_text), "course_id": course["id"] if course else "",
                 "item": item_key or "", "asked": _iso(cc_time.now()), "answered": None, "answer": ""}
        state["questions"].append(entry)
        save(cfg, state)
    return entry


def answer_question(cfg, question_id, answer="", remove=False):
    """Close a question (keeping the answer), or delete it. Returns the entry, or None when there is no such id."""
    with cc_store.state_lock(cfg):
        state = load(cfg)
        entry = next((q for q in state["questions"] if isinstance(q, dict) and q.get("id") == question_id), None)
        if entry is None:
            return None
        if remove:
            state["questions"] = [q for q in state["questions"] if q is not entry]
        else:
            entry["answered"], entry["answer"] = _iso(cc_time.now()), _words(answer)
        save(cfg, state)
    return entry


def questions(state, everything=False):
    rows = [q for q in state.get("questions") or [] if isinstance(q, dict) and q.get("id") and q.get("text")]
    return rows if everything else [q for q in rows if not q.get("answered")]


def entry_kind(entry):
    kind = entry.get("kind")
    if kind in KINDS:
        return kind
    return "exam" if cc_model.looks_like_exam(entry.get("title")) else "other"   # written before kinds existed


def entry_dates(entry):
    """[(UTC datetime, source)] an own deadline has been given, earliest first: the main date and any others."""
    dates = [(cc_time.parse_iso(entry.get("due")), _words(entry.get("source")))]
    dates += [(cc_time.parse_iso(a.get("due")), _words(a.get("source"))) for a in entry.get("alt") or [] if isinstance(a, dict)]
    return sorted([d for d in dates if d[0]], key=lambda d: d[0])


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def manual_items(state, at):
    """The student's own deadlines, shaped like the items cc_model builds from Canvas."""
    out = []
    for e in state.get("manual_deadlines") or []:
        if not isinstance(e, dict) or not e.get("id") or not e.get("title"):
            continue
        dates = entry_dates(e)
        confirmed = e.get("confirmed") is not False
        # While two sources disagree, the earlier date is the safe one to plan by.
        due = dates[0][0] if dates and not confirmed else cc_time.parse_iso(e.get("due")) or (dates[0][0] if dates else None)
        kind = entry_kind(e)
        past = bool(due and due < at)
        source = next((s for d, s in dates if d == due), "") if not confirmed else _words(e.get("source"))
        out.append(cc_model.blank_item(
            key=f"m:{e['id']}", course_id=str(e.get("course_id") or "manual"), course=e.get("course") or T("My own"),
            id=str(e["id"]), name=str(e["title"]), kind=kind, manual=True, note=str(e.get("note") or ""),
            # An exam is over once its time has passed; other work stays open until it is ticked.
            status=("offline" if kind == "exam" else "overdue") if past else "todo", due=due,
            exam=kind == "exam", confirmed=confirmed, alt_dates=[d for d in dates if d[0] != due] if not confirmed else [],
            place=_words(e.get("place")), source=source, url=cc_text.web_link(e.get("url")),
            points=_number(e.get("points")), own_weight=_number(e.get("weight")), weight=_number(e.get("weight"))))
    return out


def apply_overrides(items, state, at):
    """Put the student's own dates and notes onto the Canvas items they belong to."""
    overrides = state.get("overrides") or {}
    for it in items:
        record = overrides.get(it["key"])
        if not isinstance(record, dict):
            continue
        if record.get("note"):
            it["note"] = str(record["note"])
        if record.get("source"):
            it["source"] = str(record["source"])
        mine = cc_time.parse_iso(record.get("due"))
        if not mine:
            continue
        # Canvas has changed its own date since the student set theirs: worth a second look.
        it["canvas_moved"] = "canvas_due" in record and cc_time.parse_iso(record.get("canvas_due")) != it["due"]
        if mine == it["due"]:
            continue                                   # Canvas says the same now; nothing to override
        it["canvas_due"], it["due"], it["own_due"] = it["due"], mine, True
        it["confirmed"] = record.get("confirmed") is not False
        if not it["done"] and it["status"] in ("todo", "overdue"):
            it["status"] = "overdue" if mine < at else "todo"
    return items


def apply_ticks(items, state):
    """Mark the items the student ticked as done, unless Canvas already shows them done."""
    ticks = state.get("done") or {}
    for it in items:
        if it["key"] in ticks and not it["done"]:
            it["done"], it["status"] = True, "ticked"
            it["submitted_at"] = cc_time.parse_iso(ticks[it["key"]])
    return items


def find(items, words):
    """The one item the words point at: its key, or text found in 'course title'. Raises LookupError."""
    query = " ".join(words).strip().lower() if not isinstance(words, str) else words.strip().lower()
    if not query:
        raise LookupError(T("Say which item: its key, or a few words of its title."))
    by_key = [i for i in items if i["key"].lower() == query or i["key"].lower() == "m:" + query]
    if by_key:
        return by_key[0]
    parts = query.split()
    hits = [i for i in items if all(p in f"{i['course']} {i['name']}".lower() for p in parts)]
    exact = [i for i in hits if i["name"].lower() == query]
    hits = exact or hits
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise LookupError(T("Nothing matches '{query}'. Run: coach.py deadlines --all --keys", query=query))
    listing = "; ".join(f"{i['course']} · {i['name']} [key {i['key']}]" for i in hits[:8])
    raise LookupError(T("'{query}' matches {n} items. Use a key: {listing}", query=query, n=len(hits), listing=listing))
