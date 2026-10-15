"""Shared test equipment: sample payloads, a pinned clock and an isolated settings/data folder.

New test files should start with:  from helpers import *   (see tests/test_coach.py).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(HERE))

import cc_deadlines  # noqa: E402
import cc_ingest  # noqa: E402
import cc_model  # noqa: E402
import cc_store  # noqa: E402
import cc_text  # noqa: E402
import cc_time  # noqa: E402
import mock_canvas  # noqa: E402
import sample_data  # noqa: E402

REAL_DATA_EXISTED = (Path.home() / "CanvasCoach").exists()
HOST = "https://canvas.example.edu"
NOW = datetime(2026, 10, 12, 19, 0, tzinfo=timezone.utc)  # Mon Oct 12, 12:00 PM in Los Angeles
RESOURCES = json.loads((TOOLS / "resources.json").read_text(encoding="utf-8"))


def pick(obj, keys):
    """Python twin of pick() in fetch.js."""
    if not keys or not isinstance(obj, (dict, list)):
        return obj
    if isinstance(obj, list):
        return [pick(x, keys) for x in obj]
    out, nested = {}, {}
    for key in keys:
        head, _, rest = key.partition(".")
        if not rest:
            if key in obj:
                out[key] = obj[key]
        else:
            nested.setdefault(head, []).append(rest)
    for head, rest in nested.items():
        if head in obj and head not in out:
            out[head] = pick(obj[head], rest)
    return out


def make_payload(now=NOW, host=HOST, inbox=False):
    """What fetch.js would return for the sample data."""
    s = sample_data.build(now, host)
    keep = {r["name"]: r["keep"] for r in RESOURCES["per_course"]}
    gkeep = {r["name"]: r["keep"] for r in RESOURCES["global"]}
    courses = []
    for c in s["courses"]:
        c = dict(c)
        score = s["scores"].get(c["id"])
        if score and c.get("enrollments"):
            c["enrollments"] = [dict(c["enrollments"][0], computed_current_score=score[0],
                                     computed_current_grade=score[1], computed_final_score=score[0] - 20,
                                     computed_final_grade="C")]
        courses.append(pick(c, RESOURCES["courses"]["keep"]))
    payload = {"format": cc_store.FORMAT, "host": host, "collected_at": sample_data.iso(now),
               "user": pick(s["user"], ["id", "name", "short_name"]), "courses": courses,
               "data": {}, "global": {}, "errors": [], "truncated": [], "flags": ["hidden_text"]}
    shown = []
    for c in s["courses"]:
        cid = str(c["id"])
        if c.get("access_restricted_by_date"):
            continue
        shown.append(c["id"])
        payload["data"][cid] = {}
        if c["id"] == 505:
            payload["errors"] += [{"resource": r, "course_id": cid, "status": 401, "message": "HTTP 401 unauthorized"}
                                  for r in keep]
            continue
        rows = [pick(a, keep["assignments"]) for a in s["assignments"][c["id"]]]
        for row, a in zip(rows, s["assignments"][c["id"]]):       # what the collector and cc_ingest leave behind
            if cc_text.has_hidden_text(a.get("description")):
                row["hidden_text"] = True
        groups = [pick(g, keep["assignment_groups"]) for g in s["groups"][c["id"]]]
        for g in groups:
            mine = [a for a in rows if a["assignment_group_id"] == g["id"] and not a["omit_from_final_grade"]]
            g["points_total"] = sum(a["points_possible"] or 0 for a in mine)
            g["assignment_count"] = len(mine)
        modules = []
        for m in s["modules"].get(c["id"], []):
            m = dict(m)
            if "items" not in m:                                  # the collector's "expand" step
                m["items"] = s["long_module_items"][m["id"]]
            modules.append(pick(m, keep["modules"]))
        payload["data"][cid] = {"assignments": rows, "assignment_groups": groups, "modules": modules,
                                "submissions": [pick(x, keep["submissions"]) for x in s["submissions"].get(c["id"], [])]}
    codes = {f"course_{cid}" for cid in shown}
    payload["global"]["announcements"] = [pick(a, gkeep["announcements"]) for a in s["announcements"]
                                          if a["context_code"] in codes]
    if inbox:
        payload["global"]["conversations"] = [pick(x, gkeep["conversations"]) for x in s["conversations"]]
    payload["global"]["profile"] = {"time_zone": "America/Los_Angeles"}   # the calendar link is kept out of snapshots
    n, crc = cc_ingest.checksum(payload)
    payload["check"] = {"assignments": n, "crc": crc}
    return payload


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = {"CANVAS_COACH_HOME": str(self.tmp / "home"), "CANVAS_COACH_NOW": sample_data.iso(NOW),
                    "CANVAS_COACH_ROOT": str(self.tmp / "data folder"),   # never the real data folder
                    "CANVAS_COACH_SESSION": f"cc-test-{os.getpid()}"}     # nor the real saved login
        self._old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        self.addCleanup(self._restore_env)
        self.cfg = dict(cc_store.DEFAULTS, host=HOST, root=self.env["CANVAS_COACH_ROOT"],
                        timezone="America/Los_Angeles")

    def _restore_env(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def view(self, payload=None, **kw):
        return cc_deadlines.build_view(payload or make_payload(), self.cfg, at=NOW, **kw)

    def keys(self, view, section):
        return [i["key"] for s in view["sections"] if s["key"] == section for i in s["items"]]

    def item(self, view, key):
        return next(i for s in view["sections"] for i in s["items"] if i["key"] == key)

    def one(self, due=None, lock=None, types=("online_upload",), cid="101", **sub):
        """A payload holding a single assignment (id 1) in one course, to probe one situation."""
        p = make_payload()
        row = {"id": 1, "name": "Probe", "due_at": sample_data.iso(NOW + due) if due is not None else None,
               "lock_at": sample_data.iso(NOW + lock) if lock is not None else None, "unlock_at": None,
               "points_possible": 10, "html_url": f"{HOST}/courses/{cid}/assignments/1",
               "assignment_group_id": 11, "submission_types": list(types), "locked_for_user": False,
               "omit_from_final_grade": False,
               "submission": dict({"workflow_state": "unsubmitted", "submitted_at": None, "score": None,
                                   "missing": False, "excused": False, "late": False}, **sub)}
        p["data"] = {cid: {"assignments": [row], "assignment_groups": p["data"][cid]["assignment_groups"]}}
        p["courses"] = [c for c in p["courses"] if str(c["id"]) == cid]
        p["errors"] = []
        return self.resign(p), row

    @staticmethod
    def resign(p):
        p["check"] = dict(zip(("assignments", "crc"), cc_ingest.checksum(p)))
        return p

    def status(self, **kw):
        p, row = self.one(**kw)
        return cc_model.classify(row, NOW)


def run_cli(env, *args, stdin=None):
    """Run coach.py as a user would. Returns (exit code, everything it printed)."""
    full = dict(os.environ, **env, CANVAS_COACH_AGENT_BROWSER="/nonexistent/agent-browser")
    full.setdefault("CANVAS_COACH_SESSION", f"cc-test-{os.getpid()}")   # a test must never reach the real saved login
    p = subprocess.run([sys.executable, str(TOOLS / "coach.py"), *args], capture_output=True, text=True,
                       input=stdin, env=full)
    return p.returncode, p.stdout + p.stderr
