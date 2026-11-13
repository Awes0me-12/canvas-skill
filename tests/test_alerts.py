"""Tests for phone alerts: which work gets a calendar event, when it rings, and how the calendar is kept in step."""
from helpers import *  # noqa: F401,F403

import cc_alerts
import cc_state


class Calendar:
    """Stands in for the Calendar app: remembers what it was asked to do."""

    def __init__(self):
        self.events, self.calls, self.broken = {}, [], False

    def __call__(self, script, *argv):
        if self.broken:
            raise cc_alerts.AlertError("no permission")
        if script == cc_alerts.CREATE:
            uid = f"uid-{len(self.calls)}"
            self.events[uid] = {"calendar": argv[0], "title": argv[1], "notes": argv[2], "length": argv[9],
                                "alarms": list(argv[10:])}
            self.calls.append(("create", argv[1]))
            return uid
        for uid in argv[1:]:
            self.events.pop(uid, None)
        self.calls.append(("delete", len(argv) - 1))
        return ""

    def titles(self):
        return sorted(e["title"] for e in self.events.values())


class AlertTests(Case):
    def wanted(self, payload=None, at=NOW):
        view = cc_deadlines.build_view(payload or make_payload(), self.cfg, at=at)
        return cc_alerts.wanted(view, self.cfg, at)

    def test_only_work_that_matters_gets_an_event(self):
        # Open work due within seven days and worth 2% or more by itself; finished work and small things are left out.
        self.assertEqual(list(self.wanted()), ["101:1006", "202:2002", "303:3003", "101:1008"])
        self.cfg["alert_min_percent"] = 10
        self.assertEqual(list(self.wanted()), ["202:2002", "303:3003"])
        # An exam always counts, whatever the threshold; a longer look ahead brings it in.
        self.cfg.update(alert_min_percent=100, alert_days=10)
        self.assertEqual(list(self.wanted()), ["101:1011"])

    def test_ordinary_work_rings_a_day_and_three_hours_before_the_deadline(self):
        events = self.wanted()
        late = events["303:3003"]                               # due Friday noon
        self.assertEqual((late["end"] - late["start"], late["minutes"]), (timedelta(hours=1), [-23 * 60, -2 * 60]))
        self.assertEqual(late["title"], "Discussion: week 7 — WRIT 150")
        self.assertEqual(late["notes"], "due Fri Oct 16, 12:00 PM; 10 pts, Participation: 30% of grade, 2 items so far")
        # HW 6 is due in five hours: the day-before alarm is already past, so only the last call is left.
        self.assertEqual(events["101:1006"]["minutes"], [-2 * 60])
        self.assertEqual(cc_alerts.lines([events["101:1006"]], self.cfg),
                         ["  HW 6 — CS 101: Mon Oct 12, 4:00 PM to 5:00 PM; rings Mon Oct 12, 2:00 PM"])
        # An exam: an hour from its start, with alarms a day and three hours before.
        self.cfg["alert_days"] = 10
        exam = self.wanted()["101:1011"]
        self.assertEqual((exam["start"], exam["end"] - exam["start"], exam["minutes"]),
                         (NOW + timedelta(days=9), timedelta(hours=1), [-24 * 60, -3 * 60]))

    def test_work_open_for_one_day_rings_when_it_opens_and_three_hours_before_it_closes(self):
        payload, row = self.one(due=timedelta(days=1, hours=5))
        row.update(unlock_at=sample_data.iso(NOW + timedelta(days=1, hours=-4)), is_quiz_assignment=True)
        payload = Case.resign(payload)
        quiz = self.wanted(payload)["101:1"]                    # opens tomorrow 8:00, closes 17:00
        self.assertEqual((quiz["start"], quiz["end"]), (NOW + timedelta(hours=20), NOW + timedelta(hours=29)))
        self.assertEqual(quiz["minutes"], [0, 6 * 60])
        # Looked at after it has opened: only the alarm before it closes is left.
        self.assertEqual(self.wanted(payload, at=NOW + timedelta(hours=21))["101:1"]["minutes"], [6 * 60])
        # Open for two hours only: the alarm at opening is all there is.
        row["due_at"] = sample_data.iso(NOW + timedelta(hours=22))
        self.assertEqual(self.wanted(Case.resign(payload))["101:1"]["minutes"], [0])

    def test_a_date_that_is_not_confirmed_is_marked(self):
        courses = cc_model.course_table(make_payload(), self.cfg, NOW)
        cc_state.add_manual(self.cfg, courses, "cs 101", "Oral exam", NOW + timedelta(days=2), kind="exam", confirmed=False)
        self.assertEqual(self.wanted()["m:m1"]["title"], "Oral exam — CS 101 (?)")

    def test_the_calendar_is_kept_in_step(self):
        calendar = Calendar()
        sync = lambda payload=None, at=NOW: cc_alerts.sync(
            self.cfg, cc_deadlines.build_view(payload or make_payload(), self.cfg, at=at), at=at, runner=calendar)
        first = sync()
        self.assertEqual((first["added"], first["updated"], first["removed"], first["kept"], first["error"]), (4, 0, 0, 0, ""))
        self.assertEqual(calendar.titles(), ["Discussion: week 7 — WRIT 150", "HW 6 — CS 101", "Problem Set 2 — MATH 202",
                                             "Quiz 3 — CS 101"])
        self.assertEqual({e["calendar"] for e in calendar.events.values()}, {"Canvas Coach"})
        self.assertEqual(cc_alerts.summary(first), "Phone alerts: 4 added, 0 changed, 0 removed, 0 as before.")
        # Nothing changed: the calendar is not touched, and there is nothing to say.
        calls = len(calendar.calls)
        again = sync()
        self.assertEqual((again["kept"], len(calendar.calls), cc_alerts.summary(again)), (4, calls, ""))
        # Ticked as done: its event goes.
        cc_state.tick(self.cfg, "303:3003")
        self.assertEqual(sync()["removed"], 1)
        self.assertNotIn("Discussion: week 7 — WRIT 150", calendar.titles())
        # A deadline moves: the old event is replaced by one at the new time.
        payload = make_payload()
        row = next(a for a in payload["data"]["202"]["assignments"] if a["id"] == 2002)
        row["due_at"] = sample_data.iso(NOW + timedelta(days=2))
        moved = sync(Case.resign(payload))
        self.assertEqual((moved["updated"], moved["added"], moved["removed"], len(calendar.events)), (1, 0, 0, 3))
        # The Calendar app refuses: the briefing gets a warning, and what is recorded stays as it was.
        calendar.broken = True
        before = cc_state.load(self.cfg)["alerts"]
        failed = sync(at=NOW + timedelta(days=1))
        self.assertEqual(cc_alerts.summary(failed), "WARNING: the phone alerts could not be updated: no permission")
        self.assertEqual(cc_state.load(self.cfg)["alerts"], before)
        self.assertEqual(cc_alerts.clear(self.cfg, runner=calendar), (0, "no permission"))
        # Switched off: every event made here is deleted, and nothing else.
        calendar.broken = False
        self.assertEqual(cc_alerts.clear(self.cfg, runner=calendar), (3, ""))
        self.assertEqual((calendar.events, cc_state.load(self.cfg)["alerts"]), ({}, {}))
        self.assertEqual(cc_alerts.clear(self.cfg, runner=calendar), (0, ""))

    def test_a_damaged_record_is_ignored(self):
        state = cc_state.load(self.cfg)
        state["alerts"] = {"101:1006": "yes", "x": {"uid": 3}}
        cc_state.save(self.cfg, state)
        calendar = Calendar()
        result = cc_alerts.sync(self.cfg, self.view(), at=NOW, runner=calendar)
        self.assertEqual((result["added"], result["removed"]), (4, 0))

    def test_commands(self):
        log = Path(self.env["CANVAS_COACH_ALERTS_LOG"])
        run_cli(self.env, "setup", "--host", HOST)
        cc_store.save_snapshot(self.cfg, make_payload())
        code, out = run_cli(self.env, "alerts", "--offline")
        self.assertEqual((code, out.strip()), (0, "Phone alerts are off.\nSwitch them on with: coach.py alerts on"))
        self.assertEqual(run_cli(self.env, "brief", "--offline")[0], 0)
        self.assertFalse(log.exists(), "nothing is written to the calendar before it is switched on")
        # The Calendar app says no: it stays off.
        code, out = run_cli(dict(self.env, CANVAS_COACH_ALERTS_LOG="fail"), "alerts", "on", "--offline")
        self.assertEqual((code, cc_store.load_config()["alerts"]), (1, False), out)
        code, out = run_cli(self.env, "alerts", "on", "--offline")
        self.assertEqual(code, 0, out)
        for needle in ('Phone alerts are on. They are events in the calendar "Canvas Coach"', "listed under iCloud",
                       "Phone alerts: 4 added, 0 changed, 0 removed, 0 as before.",
                       "Work that rings in the next 7 days (exams, and work worth 2% of the grade or more):",
                       "  HW 6 — CS 101: Mon Oct 12, 4:00 PM to 5:00 PM; rings Mon Oct 12, 2:00 PM"):
            self.assertIn(needle, out)
        self.assertTrue(cc_store.load_config()["alerts"])
        made = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(c["op"], c["argv"][0], c["argv"][1]) for c in made][:2],
                         [("create", "Canvas Coach", "HW 6 — CS 101"), ("create", "Canvas Coach", "Problem Set 2 — MATH 202")])
        self.assertEqual(made[0]["argv"][9:], ["60", "-120"])
        # The briefing keeps it in step: with nothing new it says nothing; after a tick it removes the event.
        code, out = run_cli(self.env, "brief", "--offline")
        self.assertNotIn("Phone alerts", out)
        run_cli(self.env, "done", "303:3003")
        self.assertIn("Phone alerts: 0 added, 0 changed, 1 removed, 3 as before.", run_cli(self.env, "brief", "--offline")[1])
        code, out = run_cli(self.env, "alerts", "show", "--offline")
        self.assertEqual((code, out.count(" — ")), (0, 3), out)
        self.assertIn("Phone alerts: nothing to change.", run_cli(self.env, "alerts", "sync", "--offline")[1])
        code, out = run_cli(self.env, "alerts", "off")
        self.assertEqual(code, 0, out)
        self.assertIn('Phone alerts are off; 3 event(s) were removed from the calendar "Canvas Coach".', out)
        self.assertFalse(cc_store.load_config()["alerts"])
        self.assertEqual(cc_state.load(self.cfg)["alerts"], {})
        self.assertEqual(run_cli(self.env, "config", "set", "alert_min_percent", "5")[0], 0)
        self.assertEqual(run_cli(self.env, "config", "set", "alert_days", "0")[0], 1)
