"""Check a collected payload and save it as a snapshot.

Every route into the data folder passes through here, whether the payload came
from the scripted browser or was copied out of a browser tool by an AI agent.
"""
import copy
import json
import re
import zlib
from datetime import timedelta

import cc_store
import cc_text
import cc_time
from cc_lang import T

CLOCK_SLACK = timedelta(minutes=5)


class IngestError(Exception):
    pass


class NotLoggedIn(IngestError):
    pass


def parse_text(text):
    """Turn copied text into a payload dict, tolerating fences and wrappers."""
    t = (text or "").strip().lstrip("﻿")
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    if "[TRUNCATED" in t or "[BLOCKED" in t:
        raise IngestError(T("The browser tool cut the result short (it says TRUNCATED or BLOCKED). Collect again, one "
                            "course at a time: coach.py fetch-script --course <id>"))
    try:
        obj = json.loads(t)
    except ValueError:
        i, j = t.find("{"), t.rfind("}")
        if t.startswith("["):                    # the list of text pieces, with something stray after it
            i, j = 0, t.rfind("]")
        if i < 0 or j <= i:
            raise IngestError(T("That file has no JSON in it."))
        try:
            obj = json.loads(t[i:j + 1])
        except ValueError as e:
            raise IngestError(T("The JSON is incomplete or damaged ({why}). Collect again.", why=e))
    if isinstance(obj, list) and obj and all(isinstance(piece, str) for piece in obj):
        obj = "".join(obj)                       # fetch-script hands the result back in short pieces
    for _ in range(2):  # a JSON string holding JSON, or a tool's {"data": {"result": ...}} wrapper
        if isinstance(obj, str):
            try:
                obj = json.loads(obj)
            except ValueError:
                raise IngestError(T("The copied text is not a collector result."))
        if isinstance(obj, dict) and "format" not in obj and not obj.get("error"):
            inner = obj.get("data") if isinstance(obj.get("data"), dict) else obj
            if "result" in inner:
                obj = inner["result"]
    if not isinstance(obj, dict):
        raise IngestError(T("The copied text is not a collector result."))
    return obj


def _flag(value):
    return "1" if value is True else "0"


def _text(value):
    return value if isinstance(value, str) else ""


def _id(value):
    return "" if value is None else str(value)   # JavaScript's join() prints a missing value as ""


def checksum(payload):
    """(assignment count, crc32) over the fields that decide what is shown.

    Must stay identical to the block at the end of fetch.js. Text and true/false
    fields only, because numbers print differently in JavaScript and Python.
    """
    lines = []
    for c in payload.get("courses") or []:
        term = c.get("term") or {}
        roles = ",".join(_text(e.get("type")) for e in c.get("enrollments") or [])
        lines.append("|".join(["C", _id(c.get("id")), _text(term.get("end_at")), _text(c.get("end_at")),
                               _flag(c.get("access_restricted_by_date")),
                               _flag(c.get("apply_assignment_group_weights")), roles]))
    count = 0
    for cid, d in (payload.get("data") or {}).items():
        rows = d.get("assignments") if isinstance(d, dict) else None
        for a in rows if isinstance(rows, list) else []:
            s = a.get("submission") or {}
            count += 1
            lines.append("|".join([
                str(cid), _id(a.get("id")), _text(a.get("due_at")), _text(s.get("workflow_state")),
                _text(s.get("submitted_at")), _text(a.get("name")), _text(a.get("lock_at")),
                _text(a.get("unlock_at")), _flag(a.get("locked_for_user")),
                ",".join(a.get("submission_types") or []), _flag(s.get("excused")), _flag(s.get("missing")),
                _flag(s.get("redo_request")), _flag(a.get("omit_from_final_grade"))]))
    lines.sort()
    # Browsers turn a broken half of an emoji into U+FFFD before encoding; do the same.
    blob = "\n".join(lines).encode("utf-16", "surrogatepass").decode("utf-16", "replace").encode("utf-8")
    return count, format(zlib.crc32(blob) & 0xFFFFFFFF, "08x")


def _is(value, *types):
    return value is None or isinstance(value, types)


def _check_shape(payload):
    """Refuse anything the views could trip over later."""
    bad = IngestError(T("The payload is damaged (a field has the wrong type). Collect again."))
    if not _is(payload.get("user"), dict) or not isinstance(payload.get("courses"), list) \
            or not isinstance(payload.get("data"), dict):
        raise IngestError(T("The payload is missing its course list or data. Collect again."))
    for key in ("errors", "truncated"):
        if not isinstance(payload.get(key, []), list):
            raise bad
    if not _is(payload.get("global"), dict) or not _is(payload.get("slim"), dict):
        raise bad
    if not all(isinstance(e, dict) for e in payload.get("errors", [])):
        raise bad
    for c in payload["courses"]:
        if not isinstance(c, dict) or not _is(c.get("term"), dict) or not _is(c.get("enrollments"), list) \
                or not all(isinstance(e, dict) for e in c.get("enrollments") or []):
            raise bad
    for d in payload["data"].values():
        if not isinstance(d, dict):
            raise bad
        for name in ("assignments", "assignment_groups"):
            if not _is(d.get(name), list) or not all(isinstance(x, dict) for x in d.get(name) or []):
                raise bad
        for a in d.get("assignments") or []:
            if not all(_is(a.get(k), str) for k in ("name", "due_at", "lock_at", "unlock_at", "html_url")) \
                    or not _is(a.get("submission"), dict) or not _is(a.get("submission_types"), list) \
                    or not _is(a.get("lock_info"), dict) or not _is(a.get("points_possible"), int, float) \
                    or not all(isinstance(t, str) for t in a.get("submission_types") or []):
                raise bad
            sub = a.get("submission") or {}
            if not _is(sub.get("submitted_at"), str) or not _is(sub.get("workflow_state"), str) \
                    or not _is(sub.get("score"), int, float):
                raise bad
        for g in d.get("assignment_groups") or []:
            if not _is(g.get("group_weight"), int, float) or not _is(g.get("points_total"), int, float):
                raise bad


def raise_for_error(payload):
    """Turn the collector's own failure reports into exceptions."""
    if not isinstance(payload, dict):
        raise IngestError(T("Not a collector result."))
    err = payload.get("error")
    if err == "unauthenticated":
        raise NotLoggedIn(T("Canvas says you are not logged in."))
    if err == "wrong_origin":
        raise IngestError(T("The collector ran on {got}, not on {expected}. Open a Canvas page first, or fix the address "
                            "with: coach.py setup --host <address>", got=payload.get("origin"), expected=payload.get("expected")))
    if err == "canvas_unavailable":
        raise IngestError(T("Canvas is not answering right now ({why}). Try again in a few minutes.",
                            why=payload.get("detail") or T("no detail")))
    if err:
        raise IngestError(T("The collector failed: {why}", why=f"{err} {payload.get('detail') or ''}".strip()))


def validate(payload, cfg):
    """Raise unless the payload is complete, undamaged and from the right site."""
    raise_for_error(payload)
    if payload.get("format") != cc_store.FORMAT:
        raise IngestError(T("Unknown payload format {got}; expected {expected}.", got=repr(payload.get("format")),
                            expected=cc_store.FORMAT))
    host = cfg.get("host")
    if host and payload.get("host") != host:
        raise IngestError(T("This data is from {got}, but you are set up for {expected}.", got=payload.get("host"),
                            expected=host))
    collected = cc_time.parse_iso(payload.get("collected_at"))
    if collected is None:
        raise IngestError(T("The payload has no valid collection time."))
    if collected > cc_time.now() + CLOCK_SLACK:
        raise IngestError(T("The payload claims to be collected in the future. Collect again."))
    _check_shape(payload)
    n, crc = checksum(payload)
    check = payload.get("check") or {}
    if check.get("assignments") != n or check.get("crc") != crc:
        raise IngestError(T(
            "Integrity check failed: expected {n1} assignments with code {crc1}, found {n2} with code {crc2}. "
            "The copy was changed or cut off on the way. Collect again"
            " (one course at a time with --course if the result is too long to copy).",
            n1=check.get("assignments"), crc1=check.get("crc"), n2=n, crc2=crc))
    return payload


def _join_lists(a, b):
    """Two lists of rows as one, without repeating a row that has the same id."""
    seen = {str(x.get("id")) for x in a if isinstance(x, dict) and x.get("id") is not None}
    return a + [x for x in b if not (isinstance(x, dict) and str(x.get("id")) in seen)]


def merge(payloads):
    """Combine partial collections (for example one per course) into one payload."""
    if len(payloads) == 1:
        return payloads[0]
    ordered = sorted(payloads, key=lambda p: p.get("collected_at") or "")
    out = copy.deepcopy(ordered[-1])
    out.setdefault("errors", [])
    out.setdefault("truncated", [])
    if not isinstance(out.get("global"), dict):
        out["global"] = {}
    seen = {str(c.get("id")) for c in out["courses"]}
    for p in reversed(ordered[:-1]):
        for c in p.get("courses") or []:
            if str(c.get("id")) not in seen:
                out["courses"].append(c)
                seen.add(str(c.get("id")))
        for cid, d in (p.get("data") or {}).items():
            if cid not in out["data"]:
                out["data"][cid] = d
                out["errors"] += [e for e in p.get("errors") or [] if str(e.get("course_id")) == cid]
        for name, value in (p.get("global") or {}).items():
            mine = out["global"].get(name)
            if isinstance(mine, list) and isinstance(value, list):
                out["global"][name] = _join_lists(mine, value)   # each part saw only its own courses
            else:
                out["global"].setdefault(name, value)
        out["truncated"] += [t for t in p.get("truncated") or [] if t not in out["truncated"]]
    out["collected_at"] = ordered[0].get("collected_at")  # only as fresh as the oldest part
    out["merged_parts"] = len(ordered)
    n, crc = checksum(out)
    out["check"] = {"assignments": n, "crc": crc}
    return out


def _keep_private_out(payload, cfg):
    """The calendar link is a password in disguise: keep it in the settings file, not in every snapshot."""
    profile = (payload.get("global") or {}).get("profile")
    if not isinstance(profile, dict):
        return
    link = (profile.pop("calendar", None) or {}).get("ics") if isinstance(profile.get("calendar"), dict) else None
    profile.pop("calendar", None)
    if isinstance(link, str) and link.startswith(str(cfg.get("host")) + "/") and cfg.get("calendar_feed") != link:
        cfg["calendar_feed"] = link
        cc_store.update_config(calendar_feed=link)


INSPECT = "_inspect"            # where fetch.js puts text it found suspicious (see "inspect" in resources.json)
KNOWN_FLAGS = ("hidden_text",)


def _judge_hidden_text(payload):
    """Turn the instruction text the collector set aside into a plain yes/no, and throw the text away.

    The collector only sets text aside when it sees a sign of hiding; whether anything really is
    invisible is decided here, by the same reader that `show` uses. Nothing but the flag is saved,
    and payload["flags"] records that this collection looked at all.
    """
    for d in (payload.get("data") or {}).values():
        for rows in d.values() if isinstance(d, dict) else []:
            for row in rows if isinstance(rows, list) else []:
                aside = row.pop(INSPECT, None) if isinstance(row, dict) else None
                for flag, text in aside.items() if isinstance(aside, dict) else []:
                    if flag in KNOWN_FLAGS:
                        row[flag] = cc_text.has_hidden_text(text if isinstance(text, str) else "")
    flags = payload.get("flags")
    payload["flags"] = [f for f in flags if f in KNOWN_FLAGS] if isinstance(flags, list) else []
    return payload


def ingest(payloads, cfg):
    """Validate each payload, merge, save. Returns (payload, snapshot_path)."""
    for p in payloads:
        validate(p, cfg)
    payload = merge(payloads)
    _keep_private_out(payload, cfg)
    _judge_hidden_text(payload)
    return payload, cc_store.save_snapshot(cfg, payload)
