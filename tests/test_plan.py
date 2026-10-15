"""Tests for deadline clashes and the day-by-day study plan (with class times and the rest day)."""
from helpers import *  # noqa: F401,F403

import cc_brief
import cc_clash
import cc_plan
import cc_state

TZ = timezone(timedelta(hours=-7))      # Los Angeles in October


def thing(key, course, hours, **fields):
    """An open item due `hours` from now, big enough to count."""
    base = dict(key=key, course_id=course, course=course, name=key, deadline=NOW + timedelta(hours=hours),
                points=10, weight=5.0)
    return cc_model.blank_item(**dict(base, **fields))


class ClashTests(Case):
    def find(self, *items, **kw):
        return cc_clash.find(list(items), NOW, TZ, **kw)

    def test_two_courses_within_the_window(self):
        a, b = thing("a", "c1", 10), thing("b", "c2", 58)
        found = self.find(a, b)
        self.assertEqual([c["keys"] for c in found], [["a", "b"]])                     # exactly 48 hours apart still counts
        self.assertEqual(found[0]["text"], "Mon Oct 12 to Wed Oct 14: 2 deadlines from 2 courses within 48 hours")
        self.assertEqual(self.find(a, thing("b", "c2", 59)), [])
        self.assertEqual(self.find(a, thing("b", "c1", 20)), [], "one course alone is not a clash")
        self.assertEqual([c["keys"] for c in self.find(a, b, hours=24)], [])
        same_day = self.find(thing("a", "c1", 1), thing("b", "c2", 3))
        self.assertEqual(same_day[0]["label"], "Mon Oct 12")

    def test_overlapping_windows_become_separate_clashes_without_overlap(self):
        items = [thing("a", "c1", 0.5), thing("b", "c2", 40), thing("c", "c3", 50), thing("d", "c1", 52), thing("e", "c2", 300)]
        found = self.find(*items)
        self.assertEqual([c["keys"] for c in found], [["a", "b"], ["c", "d"]])
        self.assertTrue(all(c["end"] - c["start"] <= timedelta(hours=48) for c in found))
        chain = self.find(thing("a", "c1", 1), thing("b", "c1", 30), thing("c", "c2", 60))     # a alone, then b with c
        self.assertEqual([c["keys"] for c in chain], [["b", "c"]])

    def test_finished_and_small_items_drop_out(self):
        a = thing("a", "c1", 10)
        for dull in (thing("b", "c2", 20, done=True), thing("b", "c2", 20, status="missed"),
                     thing("b", "c2", 20, status="offline"),                      # attendance: nothing to hand in
                     thing("b", "c2", 20, weight=0.0), thing("b", "c2", 20, weight=0.3),
                     thing("b", "c2", 20, course_weighted=True, group_weight=0), thing("b", "c2", 20, points=0)):
            self.assertEqual(self.find(a, dull), [], dull)
        for real in (thing("b", "c2", 20, weight=None), thing("b", "c2", 20, weight=1.0),
                     thing("b", "c2", 20, manual=True, points=None, weight=None)):
            self.assertEqual(len(self.find(a, real)), 1, real)
        self.assertEqual(len(self.find(a, thing("b", "c2", 20, weight=0.3), min_percent=0)), 1)
        self.assertEqual(self.find(thing("a", "c1", -5), thing("b", "c2", 5)), [], "what is already past is not ahead")
        self.assertEqual(self.find(thing("a", "c1", 24 * 22), thing("b", "c2", 24 * 22 + 5)), [], "beyond the look-ahead")
        self.assertEqual(len(self.find(thing("a", "c1", 24 * 22), thing("b", "c2", 24 * 22 + 5), days=30)), 1)

    def test_exam_weeks(self):
        exam = thing("midterm", "c1", 30, status="offline", exam=True, points=None, weight=None)       # in class, on paper
        found = self.find(exam, thing("essay", "c2", 50), thing("final", "c3", 60, exam=True, manual=True))
        self.assertEqual((found[0]["keys"], found[0]["exams"]), (["midterm", "essay", "final"], 2))
        self.assertTrue(found[0]["text"].endswith("(including 2 exams)"))
        self.assertEqual(found[0]["courses"], ["c1", "c2", "c3"])

    def test_in_the_views(self):
        v = self.view()
        self.assertEqual([c["keys"] for c in v["clashes"]],
                         [["101:1006", "202:2002"], ["303:3003", "101:1008"], ["202:2003", "101:1011"]])
        self.assertTrue(self.item(v, "101:1011")["clash"] and not self.item(v, "101:1007")["clash"])
        text = cc_deadlines.render_text(v)
        self.assertIn("Summary: 3 overdue, 4 to do in the next 7 days, 2 already handed in, 3 deadline clashes ahead.", text)
        self.assertIn("CLASHES in the next 21 days (3)", text)
        self.assertIn("  * Tue Oct 20 to Wed Oct 21: 2 deadlines from 2 courses within 48 hours (including 1 exam)", text)
        self.assertIn("      CS 101 · Midterm (in class) — Wed Oct 21, 12:00 PM — 100 pts, Exams: 40% of grade, 2 items so far — exam", text)
        self.assertLess(text.index("CLASHES"), text.index("OVERDUE"))
        page = cc_deadlines.render_html(v)
        self.assertIn("Deadline clashes in the next 21 days", page)
        self.assertIn('<span class="tag hot">clash</span>', page)
        brief = cc_brief.build(make_payload(), self.cfg, at=NOW)
        self.assertEqual(len(brief["clashes"]), 2)                                  # only those in the coming week
        self.assertIn("CLASHES in the next 7 days", cc_brief.render_text(brief))
        self.assertIn("2 deadlines from 2 courses", cc_brief.notification(brief)[1])
        import cc_week
        self.assertIn("CLASHES in the next 21 days (3)", cc_week.render_text(cc_week.build_view(make_payload(), self.cfg, at=NOW)))
        self.cfg.update(clash_hours=12, clash_days=5)
        self.assertEqual(self.view()["clashes"], [])
        self.cfg.update(clash_hours=48, clash_days=21, clash_min_percent=30)        # only the really big ones
        self.assertEqual([c["keys"] for c in self.view()["clashes"]], [])
        cc_state.tick(self.cfg, "202:2002")
        self.cfg.update(clash_min_percent=1)
        self.assertEqual(len(self.view()["clashes"]), 2, "a ticked item no longer clashes")


class PlanTests(Case):
    def plan(self, payload=None, at=NOW, **kw):
        return cc_plan.build(payload or make_payload(), self.cfg, at=at, **kw)

    def musts(self, plan):
        return [(d["must"] or {}).get("key") for d in plan["days"]]

    def also(self, plan, n):
        return [t["key"] for t in plan["days"][n]["also"]]

    def test_one_must_do_a_day_by_the_rules(self):
        p = self.plan()
        self.assertEqual([d["label"] for d in p["days"]][::6], ["Mon Oct 12", "Sun Oct 18"])
        # Rule 1: the overdue homework is today's must-do; what is due today and tomorrow stands beside it.
        self.assertEqual(self.musts(p), ["101:1005", None, "303:3003", None, "101:1008", None, "202:2003"])
        self.assertEqual(p["days"][0]["must"]["kind"], "overdue")
        self.assertEqual(self.also(p, 0), ["101:1006", "202:2002"])
        self.assertIn("due the same day, 5:00 PM", p["days"][0]["also"][0]["why"])
        # Rule 3: heavy work (its group is 30% of the grade) goes two days before its deadline.
        discussion = p["days"][2]["must"]
        self.assertEqual((discussion["kind"], discussion["moved"]), ("work", False))
        self.assertIn("due Fri Oct 16, 12:00 PM; it weighs a lot (10 pts, Participation: 30% of grade, 2 items so far), "
                      "so it starts a day early", discussion["why"])
        # Rule 4: the in-class midterm on Wed Oct 21 gets a review session three days before.
        self.assertEqual(self.also(p, 6), ["101:1011#review3"])
        review = p["days"][6]["also"][0]
        self.assertEqual((review["title"], review["why"]), ("Review for Midterm (in class)", "the exam is in 3 days (Wed Oct 21, 12:00 PM)"))
        self.assertEqual([i["key"] for i in p["days"][1]["due"]], ["202:2002"])
        self.assertIsNone(p["ahead"])                          # everything that can be started is on the plan
        self.assertEqual(self.plan(days=3)["ahead"]["key"], "101:1008")        # a shorter plan leaves the quiz for later
        text = cc_plan.render_text(p, keys=True)
        for needle in ("Study plan, Mon Oct 12 to Sun Oct 18, as of Mon Oct 12, 12:00 PM",
                       "TODAY, Mon Oct 12", "  Must do: [overdue] CS 101 · HW 5 (late, still open) — was due Sat Oct 10, 12:00 PM "
                       "and can still be handed in  [key 101:1005]", "  Also:    [due today] CS 101 · HW 6",
                       "  Must do: nothing; a free day.", "  Due this day: MATH 202 · Problem Set 2 (12:00 PM)",
                       "No class times are saved for CS 101, MATH 202, WRIT 150", "No rest day is set."):
            self.assertIn(needle, text)
        page = cc_plan.render_html(p)
        self.assertIn('<span class="tag must">must do</span><span class="tag">overdue</span>', page)
        json.dumps(cc_deadlines.jsonable(p))

    def test_work_spreads_to_earlier_free_days_and_fixed_work_keeps_its_day(self):
        payload, row = self.one(due=timedelta(days=4))
        twin = dict(row, id=2, name="Twin", html_url=f"{HOST}/courses/101/assignments/2")
        opens_late = dict(row, id=3, name="Opens late", due_at=sample_data.iso(NOW + timedelta(days=4, hours=6)),
                          unlock_at=sample_data.iso(NOW + timedelta(days=4)), is_quiz_assignment=True)
        payload["data"]["101"]["assignments"] += [twin, opens_late]
        p = self.plan(Case.resign(payload))
        # Both are heavy (Homework is 30%) and due Friday noon: one takes Wednesday, the other moves to Tuesday.
        self.assertEqual(self.musts(p)[:5], [None, "101:2", "101:1", "101:3#prep", "101:3"])
        self.assertTrue(p["days"][1]["must"]["moved"] and not p["days"][2]["must"]["moved"])
        self.assertIn("(brought forward from Wed Oct 14, which already has a must-do)", cc_plan.render_text(p))
        # The quiz only opens on its due day: it cannot move, and the day before is for preparing.
        prep, quiz = p["days"][3]["must"], p["days"][4]["must"]
        self.assertEqual((prep["title"], prep["kind"], quiz["kind"], quiz["fixed"]), ("Prepare for Opens late", "prep", "work", True))
        self.assertIn("it opens Fri Oct 16, 12:00 PM", quiz["why"])

    def test_rest_day_is_the_lightest_candidate_and_stays_chosen(self):
        self.cfg["rest_days"] = ["fri", "sat", "sun"]
        p = self.plan()
        self.assertEqual(p["rest"], ["Sat Oct 17"])            # Friday and Sunday both have work; Saturday has none
        self.assertEqual(self.musts(p)[4:], ["101:1008", None, "202:2003"])
        self.assertIn("Sat Oct 17 — rest day", cc_plan.render_text(p))
        self.assertEqual(cc_state.load(self.cfg)["plan"]["rest"], {"2026-W42": "2026-10-17"})
        # A day chosen by hand is kept, and work that would have landed there moves earlier.
        p = self.plan(rest="sun")
        self.assertEqual((p["rest"], self.musts(p)[5:]), (["Sun Oct 18"], ["202:2003", None]))
        self.assertTrue(p["days"][5]["must"]["moved"])
        self.assertEqual(self.also(p, 6), ["101:1011#review3"])
        self.assertIn("(cannot wait for another day)", cc_plan.render_text(p))
        self.assertEqual(self.plan()["rest"], ["Sun Oct 18"], "the choice is remembered")
        self.assertEqual(self.plan(rest="none")["rest"], [])
        self.assertEqual(self.plan(rest="auto")["rest"], ["Sat Oct 17"])
        later = self.plan(at=NOW + timedelta(days=6))           # Sunday: this week's rest day is behind us
        self.assertEqual(later["rest"], ["Sat Oct 24"], "next week's is only pencilled in")
        self.assertNotIn("2026-W43", cc_state.load(self.cfg)["plan"]["rest"])

    def test_todays_must_do_holds_all_day_and_is_carried_over(self):
        first = self.plan()
        self.assertEqual(cc_state.load(self.cfg)["plan"]["days"],
                         {"2026-10-12": {"must": "101:1005", "title": "CS 101 · HW 5 (late, still open)"}})
        # A second, heavier overdue item turns up at noon: the morning's choice still leads.
        courses = cc_model.course_table(make_payload(), self.cfg, NOW)
        cc_state.add_manual(self.cfg, courses, "cs 101", "Late lab", NOW - timedelta(hours=1), weight=50)
        again = self.plan()
        self.assertEqual((self.musts(again)[0], self.also(again, 0)[0]), ("101:1005", "m:m1"))
        cc_state.remove_manual(self.cfg, "m1")
        # Done: the plan says so and offers the next thing.
        cc_state.tick(self.cfg, "101:1005")
        done = self.plan()
        self.assertEqual((done["days"][0]["must_done"], self.musts(done)[0]), ("CS 101 · HW 5 (late, still open)", "101:1006"))
        text = cc_plan.render_text(done)
        self.assertIn("  Must-do done: CS 101 · HW 5 (late, still open)", text)
        self.assertIn("  Next:    [due today] CS 101 · HW 6", text)
        # Wednesday's must-do (the discussion) is left undone: on Thursday it is carried over.
        wednesday, thursday = NOW + timedelta(days=2), NOW + timedelta(days=3)
        for key in ("101:1006", "202:2002"):                    # handed in meanwhile, so nothing is overdue by then
            cc_state.tick(self.cfg, key)
        self.assertEqual(self.musts(self.plan(at=wednesday))[0], "303:3003")
        carried = self.plan(at=thursday)["days"][0]["must"]
        self.assertEqual((carried["key"], carried["kind"]), ("303:3003", "carried"))
        self.assertTrue(carried["why"].startswith("carried over from Wed Oct 14; due the next day"))
        self.assertNotIn("m:", json.dumps(first["days"][0]["must"]["key"]))
        must = cc_plan.todays_must(make_payload(), self.cfg, at=thursday)
        self.assertEqual((must["key"], must["short"]), ("303:3003", "Discussion: week 7 (WRIT 150)"))

    def test_module_reading_goes_before_the_first_class_of_the_week(self):
        self.cfg["class_times"] = {"101": [{"days": ["wed", "fri"], "time": "10:00", "kind": "lecture"},
                                           {"days": ["thu"], "time": "14:00", "kind": "section"}],
                                   "999": [{"days": ["mon"], "time": "09:00", "kind": "lecture"}], "202": "nonsense"}
        p = self.plan()
        reading = p["days"][1]["must"]
        self.assertEqual((reading["key"], reading["kind"]), ("mod:101:503", "material"))
        self.assertEqual(reading["title"], "Go through the module Week 3 (10/12 - 10/18): Week 3 notes, Lecture 3 slides, Practice site")
        self.assertEqual(reading["why"], "CS 101 meets Wed Oct 14, 10:00 AM")
        self.assertEqual([d["classes"] for d in p["days"]][2:5], [["CS 101 10:00 AM"], ["CS 101 section 2:00 PM"], ["CS 101 10:00 AM"]])
        self.assertIn("No class times are saved for MATH 202, WRIT 150", cc_plan.render_text(p))
        cc_state.tick(self.cfg, "mod:101:503")                      # read it: no more nudging
        self.assertIsNone(self.plan()["days"][1]["must"])
        cc_state.tick(self.cfg, "mod:101:503", False)
        after_class = self.plan(at=NOW + timedelta(days=2, hours=1))          # Wednesday after the lecture
        self.assertNotIn("mod:101:503", [t["key"] for d in after_class["days"] for t in [d["must"]] + d["also"] if t])
        monday_next = [t["key"] for d in self.plan(at=NOW + timedelta(days=5))["days"] for t in [d["must"]] + d["also"] if t]
        self.assertIn("mod:101:504", monday_next, "next week's module, the day before next week's first class")

    def test_an_unsure_date_is_marked_wherever_it_is_planned_by(self):
        courses = cc_model.course_table(make_payload(), self.cfg, NOW)
        cc_state.add_manual(self.cfg, courses, "writ", "Oral exam", NOW + timedelta(days=1, hours=2), kind="exam", confirmed=False)
        cc_state.add_manual(self.cfg, courses, "writ", "Draft", NOW + timedelta(days=5), weight=20, confirmed=False)
        p = self.plan()
        whys = {t["key"]: t["why"] for d in p["days"] for t in [d["must"]] + d["also"] if t}
        self.assertTrue(whys["m:m1#review1"].endswith("; its date is not confirmed"), whys)
        self.assertTrue(whys["m:m2"].endswith("; its date is not confirmed"), whys)
        self.assertIn("WRIT 150 · Oral exam (?) (2:00 PM)", cc_plan.render_text(p))
        v = self.view()
        clash = "\n".join(cc_deadlines.clash_lines(v))
        self.assertIn("WRIT 150 · Oral exam (?) — Tue Oct 13, 2:00 PM — exam — date not confirmed", clash)
        brief = cc_brief.build(make_payload(), self.cfg, at=NOW)
        self.assertIn("Oral exam (WRIT 150) Tue Oct 13 2:00 PM (?)", [cc_brief._short(i) for i in brief["soon"]])

    def test_single_sessions_can_be_ticked_and_undated_work_is_named(self):
        keys = lambda p: [t["key"] for d in p["days"] for t in [d["must"]] + d["also"] if t]
        self.assertIn("101:1011#review3", keys(self.plan()))
        cc_state.tick(self.cfg, "101:1011#review3")                 # that session is done; the exam is still ahead
        self.assertNotIn("101:1011#review3", keys(self.plan()))
        self.assertFalse(self.item(self.view(), "101:1011")["done"])
        self.assertIn("101:1011#review2", keys(self.plan(at=NOW + timedelta(days=1))))
        text = cc_plan.render_text(self.plan(), keys=True)
        self.assertIn("1 open item(s) have no date on Canvas, so they are not on this plan: CS 101 · Reflection (no date).", text)
        run_cli(self.env, "setup", "--host", HOST)
        cc_store.save_snapshot(self.cfg, make_payload())
        code, out = run_cli(self.env, "done", "101:1011#review2")
        self.assertEqual(code, 0, out)
        self.assertIn("Marked done: study-plan task (101:1011#review2)", out)
        self.assertEqual(sorted(cc_state.load(self.cfg)["done"]), ["101:1011#review2", "101:1011#review3"])
        self.assertIn("[key 101:1011#review2]", run_cli(self.env, "marks")[1])

    def test_exam_review_sessions(self):
        courses = cc_model.course_table(make_payload(), self.cfg, NOW)
        cc_state.add_manual(self.cfg, courses, "writ", "Reading quiz", NOW + timedelta(days=2, hours=3), kind="exam")
        p = self.plan()
        self.assertEqual(self.also(p, 0)[-1], "m:m1#review2")                 # today is taken by the overdue homework
        last = p["days"][1]["must"]
        self.assertEqual((last["key"], last["kind"], last["why"]), ("m:m1#review1", "review1", "the exam is the next day (Wed Oct 14, 3:00 PM)"))
        self.assertIn("m:m1", [i["key"] for i in p["days"][2]["due"]])
        self.assertNotIn("m:m1", [t["key"] for d in p["days"] for t in [d["must"]] + d["also"] if t], "an exam is not handed in")

    def test_damaged_records_and_odd_settings_do_not_break_the_plan(self):
        cc_store.ensure_dirs(self.cfg)
        for junk in ({"plan": "yes"}, {"plan": {"days": [1, 2], "rest": "sat"}},
                     {"plan": {"days": {"2026-10-11": "x", "yesterday": {"must": "303:3003"}, "2026-10-12": {"must": 5}},
                               "rest": {"2026-W42": 7}}}):
            cc_state.save(self.cfg, junk)
            self.assertEqual(self.musts(self.plan())[0], "101:1005", junk)
        self.cfg.update(class_times={"101": [{"days": "wed", "time": "10"}, {"days": ["wed"], "time": "ten:00"}, 5]},
                        rest_days=["someday", "sat"], plan_days=1)
        one = self.plan()
        self.assertEqual((len(one["days"]), one["days"][0]["classes"], one["rest"]), (1, [], []))
        self.assertIn("Study plan, Mon Oct 12 to Mon Oct 12", cc_plan.render_text(one))
        empty = make_payload()
        empty["data"], empty["courses"], empty["errors"] = {}, [], []
        nothing = self.plan(Case.resign(empty), days=3)
        self.assertEqual(self.musts(nothing), [None, None, None])
        self.assertIn("Must do: nothing; a free day.", cc_plan.render_text(nothing))

    def test_class_time_words(self):
        self.assertEqual([cc_plan.parse_days(x) for x in ("TR", "MWF", "thu,tue", "Monday wed", "tue/thu", "Tues Thurs", "tu th")],
                         [["tue", "thu"], ["mon", "wed", "fri"], ["tue", "thu"], ["mon", "wed"], ["tue", "thu"]] + [["tue", "thu"]] * 2)
        self.assertEqual([cc_plan.parse_clock(x) for x in ("15:30", "3:30pm", "9am", "12am", "12 PM", "8", "09:05")],
                         ["15:30", "15:30", "09:00", "00:00", "12:00", "08:00", "09:05"])
        for bad in ("", "soon", "tuesday-ish"):
            with self.assertRaises(ValueError):
                cc_plan.parse_days(bad)
        for bad in ("25:00", "13pm", "noon", "9:75", ""):
            with self.assertRaises(ValueError):
                cc_plan.parse_clock(bad)

    def test_commands(self):
        run_cli(self.env, "setup", "--host", HOST)
        cc_store.save_snapshot(self.cfg, make_payload())
        self.assertEqual(run_cli(self.env, "config", "set", "rest_days", "Fri, sat sunday")[0], 0)
        self.assertEqual(cc_store.load_config()["rest_days"], ["fri", "sat", "sun"])
        self.assertEqual(run_cli(self.env, "config", "set", "rest_days", "someday")[0], 1)
        code, out = run_cli(self.env, "classes", "set", "cs", "101", "MWF", "10am")
        self.assertEqual((code, out.strip()), (0, "Saved: CS 101 meets Mon, Wed, Fri at 10:00 (lecture)."))
        self.assertEqual(run_cli(self.env, "classes", "set", "cs 101", "thu", "2pm", "--section")[0], 0)
        code, out = run_cli(self.env, "classes")
        self.assertIn("  CS 101: Mon, Wed, Fri 10:00 (lecture)\n  CS 101: Thu 14:00 (section)\nNot set yet: MATH 202, WRIT 150", out)
        self.assertEqual(run_cli(self.env, "classes", "set", "nothing", "mon", "9am")[0], 1)
        self.assertEqual(run_cli(self.env, "classes", "set", "cs 101", "mon", "late")[0], 1)
        self.assertEqual(run_cli(self.env, "classes", "set", "cs 101")[0], 1)
        code, out = run_cli(self.env, "plan", "--offline", "--keys")
        self.assertEqual(code, 0, out)
        for needle in ("TODAY, Mon Oct 12", "  Classes: CS 101 10:00 AM", "Sat Oct 17 — rest day", "[key 101:1005]", "Study Plan.html"):
            self.assertIn(needle, out)
        self.assertTrue((Path(self.env["CANVAS_COACH_ROOT"]) / "Study Plan.html").exists())
        plan = json.loads(run_cli(self.env, "plan", "--offline", "--json", "--rest", "friday")[1])
        self.assertEqual([d["rest"] for d in plan["days"]], [False] * 4 + [True, False, False])
        self.assertEqual(run_cli(self.env, "plan", "--offline", "--rest", "whenever")[0], 1)
        self.assertEqual(run_cli(self.env, "plan", "--offline", "--days", "40")[0], 1)
        code, out = run_cli(self.env, "brief", "--offline")
        self.assertIn("Must-do today: CS 101 · HW 5 (late, still open)\n  Why: was due Sat Oct 10, 12:00 PM and can still be handed in", out)
        code, out = run_cli(self.env, "done", "mod:101:504")
        self.assertEqual(code, 0, out)
        self.assertIn("mod:101:504", cc_state.load(self.cfg)["done"])
        self.assertEqual(run_cli(self.env, "classes", "clear", "cs 101")[0], 0)
        self.assertEqual(cc_store.load_config()["class_times"], {})

    def test_the_briefing_leads_with_the_must_do(self):
        payload = make_payload()
        brief = cc_brief.build(payload, self.cfg, at=NOW)
        brief["must"] = cc_plan.todays_must(payload, self.cfg, brief["view"], at=NOW)
        title, message = cc_brief.notification(brief)
        self.assertTrue(message.startswith("Must-do today: HW 5 (late, still open) (CS 101); "), message)
        self.assertEqual(message.count("HW 5"), 1, "the must-do is not repeated in the list after it")
        quiet = make_payload()                                    # only a rest of the term with nothing near
        quiet["data"] = {"303": quiet["data"]["303"]}
        quiet["courses"] = [c for c in quiet["courses"] if c["id"] == 303]
        quiet["errors"] = []
        b = cc_brief.build(Case.resign(quiet), self.cfg, at=NOW, hours=24)
        b["must"] = cc_plan.todays_must(quiet, self.cfg, b["view"], at=NOW)
        self.assertIsNone(b["must"])
        self.assertIsNone(cc_brief.notification(b))
        b["must"] = cc_plan.todays_must(quiet, self.cfg, at=NOW + timedelta(days=2))
        self.assertEqual(cc_brief.notification(b)[0], "Canvas: today's plan")


if __name__ == "__main__":
    unittest.main(verbosity=1)
