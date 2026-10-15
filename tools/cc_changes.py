"""What changed on Canvas between two collections.

"Since when" is the baseline: the snapshot the student last looked at. Looking at the list
(deadlines, week, changes) moves the baseline forward; the background briefing does not, so
nothing it reports is lost before the student has seen it.
"""
from datetime import timedelta

import cc_model
import cc_state
import cc_store
import cc_text
import cc_time
from cc_lang import T

LOG_LIMIT = 100


def _stamp(when, tz):
    return cc_time.stamp(cc_time.local(when, tz)) if when else T("no date")


def _rows(payload, cid, name):
    """A course's rows for one resource, or None when that resource was not collected for it."""
    data = (payload.get("data") or {}).get(cid)
    rows = data.get(name) if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else None


def _by_id(rows):
    return {cc_model.sid(r.get("id")): r for r in rows or [] if isinstance(r, dict)}


def _complete(payload, cid):
    """True when this snapshot holds the course's whole assignment list (read, and not cut off)."""
    return _rows(payload, cid, "assignments") is not None and \
        f"assignments:{cid}" not in [str(t) for t in payload.get("truncated") or []]


def _slim_cutoff(payload):
    """Fallback-route collections leave out finished work due before this time; None for full collections."""
    slim = payload.get("slim")
    when = cc_time.parse_iso(payload.get("collected_at"))
    if not isinstance(slim, dict) or not when or not isinstance(slim.get("days"), (int, float)):
        return None
    return when - timedelta(days=slim["days"])


def _left_out(item, cutoff):
    """Whether a slim collection would have dropped this item on purpose (so its absence means nothing)."""
    return bool(cutoff and item["done"] and item["due"] and item["due"] < cutoff)


def diff(old, new, cfg, at=None):
    """Changes from snapshot `old` to snapshot `new`, most important first.

    Each is {"kind", "course", "text", "url", "key"}. Only courses shown now are compared, and
    a resource is only compared where both snapshots have it, so a first collection of
    something new is never reported as "everything changed".
    """
    at = at or cc_time.now()
    tz = cc_time.zone(cfg)
    out = []

    def add(kind, course, text, url="", key=""):
        out.append({"kind": kind, "course": course, "text": text, "url": url, "key": key})

    before = {i["key"]: i for i in cc_model.items(old, cfg, at)}
    after = {i["key"]: i for i in cc_model.items(new, cfg, at)}
    courses = [c for c in cc_model.course_table(new, cfg, at) if not c["hidden"] and c["collected"]]
    labels = {c["id"]: c["label"] for c in courses}
    # A course is compared only when BOTH snapshots hold its complete list: one failed or cut-off
    # request must never read as "everything was removed".
    both = {c["id"] for c in courses if _complete(old, c["id"]) and _complete(new, c["id"])}
    old_cut, new_cut = _slim_cutoff(old), _slim_cutoff(new)
    me = cc_model.sid((new.get("user") or {}).get("id"))
    my_names = {(new.get("user") or {}).get(k) for k in ("name", "short_name")} - {None, ""}
    own_dates = {key: cc_time.parse_iso(r.get("due")) for key, r in (cc_state.load(cfg).get("overrides") or {}).items()
                 if isinstance(r, dict) and cc_time.parse_iso(r.get("due"))}
    # Hidden text is only compared when both collections looked for it; otherwise "none found" means nothing.
    looked = all("hidden_text" in (p.get("flags") or []) for p in (old, new))

    for key, it in after.items():
        if it["course_id"] not in both:
            continue
        was = before.get(key)
        name = f"{it['course']} · {it['name']}"
        if was is None:
            if not _left_out(it, old_cut):
                add("new", it["course"], T("New: {name}, due {when}", name=name, when=_stamp(it["due"], tz)) if it["due"]
                    else T("New: {name}, no due date", name=name), it["url"], key)
            continue
        if was["due"] != it["due"]:
            text = T("Due date changed: {name}: was {old}, now {new}", name=name, old=_stamp(was["due"], tz),
                     new=_stamp(it["due"], tz))
            if key in own_dates and own_dates[key] != it["due"]:
                text += T("; you had set your own date for it ({when}), so check which is right, then: coach.py confirm {key}",
                          when=_stamp(own_dates[key], tz), key=key)
            add("due", it["course"], text, it["url"], key)
        if was["lock"] != it["lock"]:
            add("closes", it["course"], T("Closing date changed: {name}: was {old}, now {new}", name=name,
                                          old=_stamp(was["lock"], tz), new=_stamp(it["lock"], tz)), it["url"], key)
        if was["done"] and not it["done"]:
            add("reopened", it["course"], T("No longer counted as done: {name}", name=name)
                + (T(" (your teacher asked you to redo it)") if it["redo"] else ""), it["url"], key)
        elif not was["done"] and it["done"] and it["status"] in ("submitted", "graded"):
            add("handed_in", it["course"], T("Canvas now shows it handed in: {name}", name=name), it["url"], key)
        if it["score"] is not None and it["score"] != was["score"] and not it["pending_review"]:
            points = f"/{it['points']:g}" if isinstance(it["points"], (int, float)) and it["points"] else ""
            add("graded", it["course"], T("Graded: {name}: {score}", name=name, score=f"{it['score']:g}{points}")
                + (T(" (was {old})", old=f"{was['score']:g}") if was["score"] is not None else ""), it["url"], key)
        if isinstance(was["points"], (int, float)) and isinstance(it["points"], (int, float)) and was["points"] != it["points"]:
            add("points", it["course"], T("Points changed: {name}: was {old}, now {new}", name=name,
                                          old=f"{was['points']:g}", new=f"{it['points']:g}"), it["url"], key)
        if looked and it["hidden_text"] and not was["hidden_text"]:
            add("hidden", it["course"], T("Text that is invisible on the Canvas page has appeared in the instructions of "
                                          "{name}; see: coach.py show {key}", name=name, key=key), it["url"], key)
    for key, was in before.items():
        if key not in after and was["course_id"] in both and not _left_out(was, new_cut):
            add("removed", was["course"], T("Removed from Canvas: {name}", name=f"{was['course']} · {was['name']}"), "", key)

    for cid in sorted(both):
        label = labels[cid]
        g_old, g_new = _by_id(_rows(old, cid, "assignment_groups")), _by_id(_rows(new, cid, "assignment_groups"))
        for gid, g in g_new.items():
            if gid in g_old and g_old[gid].get("group_weight") != g.get("group_weight"):
                add("weight", label, T("Grade weight changed: {name}: was {old}%, now {new}%",
                                       name=f"{label} · {cc_text.one_line(g.get('name'))}",
                                       old=f"{g_old[gid].get('group_weight') or 0:g}", new=f"{g.get('group_weight') or 0:g}"))
        m_old, m_new = _rows(old, cid, "modules"), _rows(new, cid, "modules")
        if m_old is not None and m_new is not None:
            seen = {cc_model.sid(i.get("id")) for m in m_old for i in m.get("items") or []}
            for m in m_new:
                fresh = [i for i in m.get("items") or [] if cc_model.sid(i.get("id")) not in seen and i.get("type") != "SubHeader"]
                if fresh:
                    names = ", ".join(cc_text.one_line(i.get("title")) for i in fresh[:3]) + (
                        T(" and {n} more", n=len(fresh) - 3) if len(fresh) > 3 else "")
                    add("material", label, T("New in {module}: {names}", names=names,
                                             module=f"{label} · {cc_text.one_line(m.get('name'))}"),
                        fresh[0].get("html_url") or "")
        s_old, s_new = _rows(old, cid, "submissions"), _rows(new, cid, "submissions")
        if s_old is not None and s_new is not None:
            had = {cc_model.sid(c.get("id")) for s in s_old for c in s.get("submission_comments") or []}
            titles = {i["id"]: i["name"] for i in after.values() if i["course_id"] == cid}
            for s in s_new:
                for c in s.get("submission_comments") or []:
                    if cc_model.sid(c.get("id")) not in had and not is_mine(c, me, my_names):
                        title = titles.get(cc_model.sid(s.get("assignment_id")), T("an assignment"))
                        add("feedback", label, T("New comment on {name} from {who}: {text}", name=f"{label} · {title}",
                                                 who=cc_text.one_line(c.get("author_name")),
                                                 text=cc_text.excerpt(c.get("comment"), 120)))

    a_old, a_new = (old.get("global") or {}).get("announcements"), (new.get("global") or {}).get("announcements")
    if isinstance(a_old, list) and isinstance(a_new, list):
        had = set(_by_id(a_old))
        was_there = set(old.get("data") or {})        # a course first collected now has no "new" announcements
        for a in a_new:
            cid = str(a.get("context_code") or "").replace("course_", "")
            if cc_model.sid(a.get("id")) not in had and cid in labels and cid in was_there:
                add("announcement", labels[cid], T("New announcement in {course}: {title}", course=labels[cid],
                                                   title=cc_text.one_line(a.get("title"))), a.get("html_url") or "")
    c_old, c_new = (old.get("global") or {}).get("conversations"), (new.get("global") or {}).get("conversations")
    if cfg.get("inbox") and isinstance(c_old, list) and isinstance(c_new, list):     # only while the student wants it read
        last = {cid: c.get("last_message_at") for cid, c in _by_id(c_old).items()}
        for c in c_new:
            if last.get(cc_model.sid(c.get("id"))) != c.get("last_message_at"):
                who = ", ".join(cc_text.one_line(p.get("name")) for p in c.get("participants") or []
                                if cc_model.sid(p.get("id")) != me) or T("someone")
                add("message", "", T("New inbox message from {who}: {subject}", who=who,
                                     subject=cc_text.one_line(c.get("subject")) or T("(no subject)")))

    order = ["due", "closes", "removed", "reopened", "new", "hidden", "graded", "feedback", "announcement", "message",
             "material", "weight", "points", "handed_in"]
    out.sort(key=lambda c: order.index(c["kind"]))
    return out


def is_mine(comment, my_id, my_names):
    """Whether the student wrote this comment. Canvas shows the display name, so the id is what counts."""
    author = cc_model.sid(comment.get("author_id"))
    return author == my_id if author and my_id else comment.get("author_name") in my_names


def _baseline(cfg, state):
    """(payload, path) of the snapshot the student last looked at; the oldest readable one if that is gone.

    (None, None) only when no baseline was ever set.
    """
    wanted = state.get("baseline")
    if not wanted:
        return None, None
    snaps = cc_store.list_snapshots(cfg)
    for path in [p for p in snaps if p.name == wanted] + snaps:
        try:
            payload = cc_store.read_json(path)
        except cc_store.DataError:
            continue
        if isinstance(payload, dict):
            return payload, path
    return None, None


def _safe_to_move(old, new, cfg):
    """False while the newest snapshot lacks a course list the baseline has: moving on would hide its changes."""
    if old is None:
        return True
    shown = [c["id"] for c in cc_model.course_table(new, cfg, cc_time.now()) if not c["hidden"]]
    return not any(_complete(old, cid) and not _complete(new, cid) for cid in shown)


def since_baseline(cfg, payload, advance=True):
    """(changes, "since <time>" text). Moves the baseline to the newest snapshot when `advance`.

    The very first call only sets the baseline and reports nothing.
    """
    with cc_store.state_lock(cfg):
        state = cc_state.load(cfg)
        _, newest = cc_store.latest_snapshot(cfg)
        old, old_path = _baseline(cfg, state)
        changes, since = [], ""
        if old is not None and newest is not None:
            when = cc_time.parse_iso(old.get("collected_at"))
            since = cc_time.stamp(cc_time.local(when, cc_time.zone(cfg))) if when else ""
            if old_path != newest:
                changes = diff(old, payload, cfg)
        first_time = not state.get("baseline")
        move = newest is not None and (first_time or (advance and _safe_to_move(old, payload, cfg)))
        if move and state.get("baseline") != newest.name:
            state["baseline"] = newest.name
            if changes:
                log = state.get("change_log") if isinstance(state.get("change_log"), list) else []
                now = cc_time.now().strftime("%Y-%m-%dT%H:%M:%SZ")
                state["change_log"] = (log + [{"at": now, "text": c["text"]} for c in changes])[-LOG_LIMIT:]
            cc_state.save(cfg, state)
    return changes, since


def render_text(changes, since, limit=12, more="coach.py changes --log"):
    """Lines for the top of a list. Empty when nothing changed. `more` names the command that shows the rest."""
    if not changes:
        return []
    lines = [T("CHANGED on Canvas since {since} ({n})", since=since, n=len(changes)) if since
             else T("CHANGED on Canvas ({n})", n=len(changes))]
    lines += [f"  * {c['text']}" for c in changes[:limit]]
    if len(changes) > limit:
        lines.append(T("  ... and {n} more ({more})", n=len(changes) - limit, more=more))
    return lines


def render_html(changes, since, limit=12):
    if not changes:
        return ""
    e = cc_text.escape
    rows = "".join(
        f'<li><a href="{e(cc_text.web_link(c["url"]))}" target="_blank" rel="noopener">{e(c["text"])}</a></li>'
        if cc_text.web_link(c["url"])
        else f"<li>{e(c['text'])}</li>" for c in changes[:limit])
    more = "<li>" + e(T("and {n} more", n=len(changes) - limit)) + "</li>" if len(changes) > limit else ""
    title = T("Changed on Canvas since {since}", since=since) if since else T("Changed on Canvas")
    return f'<div class="changes"><h2>{e(title)}</h2><ul>{rows}{more}</ul></div>'
