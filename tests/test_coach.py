"""Tests for Canvas Coach. No login and no network beyond 127.0.0.1.

    python3 tests/test_coach.py        this file
    python3 tests/run_all.py           every test file
"""
from helpers import *  # noqa: F401,F403


class TimeTests(Case):
    def test_parse_and_now(self):
        self.assertEqual(cc_time.now(), NOW)
        self.assertEqual(cc_time.parse_iso("2026-10-12T19:00:00Z"), NOW)
        self.assertEqual(cc_time.parse_iso("2026-10-12T12:00:00-07:00"), NOW)
        for bad in (None, "", "soon", 5):
            self.assertIsNone(cc_time.parse_iso(bad))

    def test_relative_words(self):
        tz = cc_time.zone(self.cfg)
        at = cc_time.local(NOW, tz)
        rel = lambda **kw: cc_time.relative(cc_time.local(NOW + timedelta(**kw), tz), at)
        self.assertEqual(rel(minutes=20), "in 20 min")
        self.assertEqual(rel(hours=5), "today, in 5 h")
        self.assertEqual(rel(hours=14), "tomorrow")          # 2 AM the next day
        self.assertEqual(rel(days=3), "in 3 days")
        self.assertEqual(rel(hours=-2), "2 h ago")
        self.assertEqual(rel(hours=-13), "yesterday")
        self.assertEqual(rel(days=-5), "5 days ago")

    def test_daylight_saving_and_midnight(self):
        tz = cc_time.zone(self.cfg)
        winter = cc_time.local(cc_time.parse_iso("2026-12-01T19:00:00Z"), tz)
        self.assertEqual(cc_time.stamp(winter), "Tue Dec 1, 11:00 AM")   # PST, not PDT
        midnight = cc_time.local(cc_time.parse_iso("2026-10-14T07:00:00Z"), tz)
        self.assertEqual(cc_time.clock(midnight), "12:00 AM")
        self.assertIn("Tuesday night", cc_time.midnight_note(midnight))
        self.assertEqual(cc_time.midnight_note(winter), "")

    def test_relative_across_clock_changes(self):
        tz = cc_time.zone(self.cfg)
        both = lambda a, b: cc_time.relative(cc_time.local(cc_time.parse_iso(b), tz),
                                             cc_time.local(cc_time.parse_iso(a), tz))
        # 1 Nov 2026: clocks go back at 09:00 UTC. 1:30 AM PDT -> 1:15 AM PST is 45 real minutes later.
        self.assertEqual(both("2026-11-01T08:30:00Z", "2026-11-01T09:15:00Z"), "in 45 min")
        # 8 Mar 2026: clocks go forward at 10:00 UTC. 1:45 AM PST -> 3:15 AM PDT is 30 real minutes later.
        self.assertEqual(both("2026-03-08T09:45:00Z", "2026-03-08T10:15:00Z"), "in 30 min")
        self.assertEqual(both("2026-11-01T08:30:00Z", "2026-11-01T10:00:00Z"), "today, in 1 h")

    def test_bad_timezone(self):
        with self.assertRaises(ValueError):
            cc_time.zone({"timezone": "Mars/Olympus"})
        self.assertIsNone(cc_time.zone({"timezone": "auto"}))


class StoreTests(Case):
    def test_normalize_host(self):
        n = cc_store.normalize_host
        self.assertEqual(n("https://Canvas.School.edu/courses/12?x=1"), "https://canvas.school.edu")
        self.assertEqual(n("canvas.school.edu"), "https://canvas.school.edu")
        self.assertEqual(n("http://127.0.0.1:8765/login"), "http://127.0.0.1:8765")
        for bad in ("", "http://canvas.school.edu", "ftp://x.edu", "https://"):
            with self.assertRaises(cc_store.ConfigError):
                n(bad)

    def test_snapshots_keep_order_and_prune(self):
        total = cc_store.KEEP_SNAPSHOTS + 5
        for i in range(total):
            p = make_payload()
            p["collected_at"] = sample_data.iso(NOW - timedelta(minutes=total - 1 - i))
            cc_store.save_snapshot(self.cfg, p)
        snaps = cc_store.list_snapshots(self.cfg)
        self.assertEqual(len(snaps), cc_store.KEEP_SNAPSHOTS)
        latest, _ = cc_store.latest_snapshot(self.cfg)
        self.assertEqual(latest["collected_at"], sample_data.iso(NOW))
        prev, _ = cc_store.previous_snapshot(self.cfg)
        self.assertLess(prev["collected_at"], latest["collected_at"])
        self.assertTrue((cc_store.data_dir(self.cfg) / "state.json").exists())

    def test_same_second_snapshots_do_not_overwrite(self):
        a = cc_store.save_snapshot(self.cfg, make_payload())
        b = cc_store.save_snapshot(self.cfg, make_payload())
        self.assertNotEqual(a, b)
        self.assertEqual(cc_store.list_snapshots(self.cfg), [a, b])

    def test_bad_snapshot_files_cannot_shadow_good_ones(self):
        good = cc_store.save_snapshot(self.cfg, make_payload())
        future = make_payload()
        future["collected_at"] = "2099-01-01T00:00:00Z"
        cc_store.save_snapshot(self.cfg, future)                      # sorts last by name
        (cc_store.raw_dir(self.cfg) / "20990102T000000.json").write_text("{ not json", encoding="utf-8")
        (cc_store.raw_dir(self.cfg) / ".tmp-crash.json").write_text("{}", encoding="utf-8")
        payload, path = cc_store.latest_snapshot(self.cfg)
        self.assertEqual(path, good)
        self.assertEqual(cc_store.previous_snapshot(self.cfg), (None, None))


class IngestTests(Case):
    def test_good_payload_passes(self):
        self.assertEqual(cc_ingest.validate(make_payload(), self.cfg)["check"]["assignments"], 23)

    def test_damage_is_caught(self):
        p = make_payload()
        p["data"]["101"]["assignments"][5]["due_at"] = "2026-10-20T06:59:59Z"   # a changed date
        with self.assertRaises(cc_ingest.IngestError):
            cc_ingest.validate(p, self.cfg)
        p = make_payload()
        p["data"]["202"]["assignments"].pop()                                    # a lost row
        with self.assertRaises(cc_ingest.IngestError):
            cc_ingest.validate(p, self.cfg)
        p = make_payload()
        p["data"]["101"]["assignments"][5]["submission"]["workflow_state"] = "submitted"
        with self.assertRaises(cc_ingest.IngestError):
            cc_ingest.validate(p, self.cfg)

    def test_check_covers_every_field_that_decides_what_is_shown(self):
        def changed(edit):
            p = make_payload()
            edit(p)
            with self.assertRaises(cc_ingest.IngestError, msg=edit.__doc__):
                cc_ingest.validate(p, self.cfg)
        row = lambda p: p["data"]["101"]["assignments"][5]
        changed(lambda p: row(p)["submission"].update(excused=True))
        changed(lambda p: row(p)["submission"].update(missing=True))
        changed(lambda p: row(p).update(name="HW 9"))
        changed(lambda p: row(p).update(lock_at="2026-10-01T00:00:00Z"))
        changed(lambda p: row(p).update(unlock_at="2027-01-01T00:00:00Z"))
        changed(lambda p: row(p).update(submission_types=["on_paper"]))
        changed(lambda p: row(p).update(locked_for_user=True))
        changed(lambda p: row(p).update(omit_from_final_grade=True))
        changed(lambda p: p["courses"].pop(0))                                    # a course row lost
        changed(lambda p: p["courses"][0]["term"].update(end_at="2020-01-01T00:00:00Z"))
        changed(lambda p: p["courses"][0]["enrollments"][0].update(type="ta"))

    def test_wrong_types_are_refused_not_crashed_on(self):
        edits = [lambda p: p.update(user="x"), lambda p: p.update(data={"101": []}),
                 lambda p: p["data"]["101"].update(assignments={"a": 1}),
                 lambda p: p["data"]["101"]["assignments"].append("x"),
                 lambda p: p["data"]["101"]["assignments"][0].update(due_at=5),
                 lambda p: p["data"]["101"]["assignments"][0].update(submission=[1]),
                 lambda p: p["data"]["101"]["assignments"][0].update(submission_types="online_upload"),
                 lambda p: p["data"]["101"]["assignments"][0].update(points_possible="ten"),
                 lambda p: p["courses"].append(None), lambda p: p["courses"][0].update(term="Fall"),
                 lambda p: p.update(errors=["x"]), lambda p: p.update(courses=None)]
        for edit in edits:
            p = make_payload()
            edit(p)
            with self.assertRaises(cc_ingest.IngestError):
                cc_ingest.validate(p, self.cfg)

    def test_future_collection_time_is_refused(self):
        p = make_payload()
        p["collected_at"] = sample_data.iso(NOW + timedelta(hours=2))
        with self.assertRaises(cc_ingest.IngestError):
            cc_ingest.validate(p, self.cfg)
        p["collected_at"] = sample_data.iso(NOW + timedelta(minutes=2))   # small clock difference is fine
        cc_ingest.validate(p, self.cfg)

    def test_outage_is_not_called_a_logout(self):
        with self.assertRaises(cc_ingest.IngestError) as caught:
            cc_ingest.validate({"error": "canvas_unavailable", "status": 503, "detail": "HTTP 503 not JSON"}, self.cfg)
        self.assertNotIsInstance(caught.exception, cc_ingest.NotLoggedIn)
        self.assertIn("not answering", str(caught.exception))

    def test_wrong_site_and_errors(self):
        with self.assertRaises(cc_ingest.IngestError):
            cc_ingest.validate(make_payload(host="https://evil.example.com"), self.cfg)
        with self.assertRaises(cc_ingest.NotLoggedIn):
            cc_ingest.validate({"error": "unauthenticated", "status": 401}, self.cfg)
        with self.assertRaises(cc_ingest.IngestError):
            cc_ingest.validate({"error": "wrong_origin", "origin": "https://sso.x", "expected": HOST}, self.cfg)
        with self.assertRaises(cc_ingest.IngestError):
            cc_ingest.validate(dict(make_payload(), format="other/9"), self.cfg)

    def test_parse_text_is_forgiving(self):
        raw = json.dumps(make_payload())
        for text in (raw, "```json\n" + raw + "\n```", "Result:\n" + raw + "\nDone.",
                     json.dumps(raw), json.dumps({"success": True, "data": {"result": raw}}),
                     json.dumps({"success": True, "data": {"lifecycle": {"reused": True}, "origin": HOST,
                                                           "result": json.loads(raw)}, "error": None}),
                     json.dumps({"result": json.loads(raw)})):
            self.assertEqual(cc_ingest.validate(cc_ingest.parse_text(text), self.cfg)["host"], HOST)
        pieces = [raw[i:i + 600] for i in range(0, len(raw), 600)]          # how fetch-script hands it back
        for text in (json.dumps(pieces), json.dumps(pieces, indent=2), json.dumps(pieces) + "\n\nTab Context: ..."):
            self.assertEqual(cc_ingest.validate(cc_ingest.parse_text(text), self.cfg)["check"]["assignments"], 23)
        cut = raw.replace('"unsubmitted"', '"[TRUNCATED: Max depth exceeded]"', 1)
        with self.assertRaises(cc_ingest.IngestError) as caught:
            cc_ingest.parse_text(cut)
        self.assertIn("one course at a time", str(caught.exception))
        for bad in ("", "no json here", raw[: len(raw) // 2], "[1, 2]"):
            with self.assertRaises(cc_ingest.IngestError):
                cc_ingest.parse_text(bad)

    def test_merge_parts_per_course(self):
        full = make_payload()
        parts = []
        for i, cid in enumerate(("101", "202", "303", "505", "606")):
            p = make_payload()
            p["collected_at"] = sample_data.iso(NOW + timedelta(minutes=i))
            p["data"] = {cid: p["data"][cid]}
            p["errors"] = [e for e in p["errors"] if e["course_id"] == cid]
            n, crc = cc_ingest.checksum(p)
            p["check"] = {"assignments": n, "crc": crc}
            parts.append(p)
        merged, path = cc_ingest.ingest(parts, self.cfg)
        self.assertEqual(merged["check"], full["check"])
        self.assertEqual(merged["collected_at"], sample_data.iso(NOW))   # as old as the oldest part
        self.assertEqual(len(merged["errors"]), len(RESOURCES["per_course"]))
        self.assertTrue(path.exists())
        self.assertEqual(self.keys(self.view(merged), "week"), self.keys(self.view(full), "week"))

    def test_merge_joins_global_lists(self):
        a, b = make_payload(), make_payload()
        a["collected_at"] = sample_data.iso(NOW - timedelta(minutes=1))
        a["global"] = {"announcements": [{"id": 1}, {"id": 2}], "profile": {"x": 1}}
        b["global"] = {"announcements": [{"id": 2}, {"id": 3}]}
        merged = cc_ingest.merge([a, b])
        self.assertEqual([x["id"] for x in merged["global"]["announcements"]], [2, 3, 1])
        self.assertEqual(merged["global"]["profile"], {"x": 1})


class ModelTests(Case):
    def test_status_of_every_sample_assignment(self):
        cfg = dict(self.cfg, include_courses=["606"])
        got = {i["key"]: i["status"] for i in cc_model.items(make_payload(), cfg, NOW)}
        self.assertEqual(got, {
            "101:1001": "graded", "101:1002": "graded", "101:1003": "excused", "101:1004": "missed",
            "101:1005": "overdue", "101:1006": "todo", "101:1007": "submitted", "101:1008": "todo",
            "101:1009": "graded", "101:1010": "todo", "101:1011": "offline", "101:1012": "offline",
            "101:1013": "offline", "101:1014": "todo",
            "202:2001": "graded", "202:2002": "todo", "202:2003": "todo", "202:2004": "offline",
            "303:3001": "graded", "303:3002": "submitted", "303:3003": "todo", "303:3004": "missed",
            "606:6001": "overdue"})

    def test_a_grade_alone_is_not_a_hand_in(self):
        graded = dict(workflow_state="graded")
        ago, ahead = timedelta(days=-2), timedelta(days=3)
        # A teacher typed 0 for work that never arrived: still owed.
        self.assertEqual(self.status(due=ago, score=0, **graded), "overdue")
        self.assertEqual(self.status(due=ahead, score=0, **graded), "todo")
        # Wrong file handed in, teacher set it to Missing: still owed.
        self.assertEqual(self.status(due=ago, score=0, missing=True, submitted_at="2026-10-09T00:00:00Z", **graded),
                         "overdue")
        # Real marks without a Canvas hand-in (given in person), paper exams and outside tools count as done.
        self.assertEqual(self.status(due=ago, score=8, **graded), "graded")
        self.assertEqual(self.status(due=ago, score=0, types=("on_paper",), **graded), "graded")
        self.assertEqual(self.status(due=ago, score=0, types=("external_tool",), **graded), "graded")
        self.assertEqual(self.status(due=ago, score=9, submitted_at="2026-10-09T00:00:00Z", **graded), "graded")
        # The teacher asked for it to be done again.
        self.assertEqual(self.status(due=ahead, redo_request=True, submitted_at="2026-10-09T00:00:00Z",
                                     workflow_state="submitted"), "todo")
        self.assertEqual(self.status(due=ago, missing=True, types=("on_paper",)), "missed")
        self.assertEqual(self.status(due=ahead, types=("on_paper",)), "offline")

    def test_module_lock_is_not_a_closed_assignment(self):
        p, row = self.one(due=timedelta(days=-2))
        row["locked_for_user"] = True
        self.assertEqual(cc_model.classify(row, NOW), "missed")
        row["lock_info"] = {"context_module": {"id": 5}}
        self.assertEqual(cc_model.classify(row, NOW), "overdue")
        view = self.view(self.resign(p))
        self.assertIn("locked until earlier module items are finished", self.item(view, "101:1")["notes"])

    def test_new_quizzes_are_quizzes(self):
        self.assertEqual(cc_model.kind_of({"submission_types": ["external_tool"], "is_quiz_lti_assignment": True}), "quiz")
        self.assertEqual(cc_model.kind_of({"submission_types": ["external_tool"]}), "external tool")

    def test_weights(self):
        w = {i["key"]: i["weight"] for i in cc_model.items(make_payload(), self.cfg, NOW)}
        self.assertAlmostEqual(w["101:1006"], 30 * 10 / 80)       # homework group: 8 counted items of 10
        self.assertAlmostEqual(w["101:1011"], 40 * 100 / 200)     # exams
        self.assertAlmostEqual(w["303:3002"], 70 * 100 / 200)
        self.assertAlmostEqual(w["202:2002"], 100 * 50 / 300)     # unweighted course: share of all points
        self.assertEqual(w["101:1013"], 0.0)                      # omitted from the final grade

    def test_weight_unknown_cases(self):
        f = cc_model.weight_percent
        self.assertIsNone(f({"points_possible": 0}, True, {}, 100))
        self.assertIsNone(f({"points_possible": 10, "assignment_group_id": 9}, True, {}, 100))
        self.assertIsNone(f({"points_possible": 10}, False, {}, 0))
        groups = {"1": {"group_weight": 50, "points_total": 0}}
        self.assertIsNone(f({"points_possible": 10, "assignment_group_id": 1}, True, groups, 100))

    def test_group_totals_counted_when_collector_did_not(self):
        p = make_payload()
        for g in p["data"]["101"]["assignment_groups"]:
            del g["points_total"]
        w = {i["key"]: i["weight"] for i in cc_model.items(p, self.cfg, NOW)}
        self.assertAlmostEqual(w["101:1006"], 3.75)

    def test_course_table(self):
        rows = {c["id"]: c for c in cc_model.course_table(make_payload(), self.cfg, NOW)}
        self.assertEqual(rows["101"]["hidden"], "")
        self.assertIn("not open", rows["404"]["hidden"])
        self.assertIn("term ended", rows["606"]["hidden"])
        self.assertEqual(len(rows["505"]["errors"]), len(RESOURCES["per_course"]))
        cfg = dict(self.cfg, ignore_courses=[202], include_courses=["606"], course_names={"101": "Coding"})
        rows = {c["id"]: c for c in cc_model.course_table(make_payload(), cfg, NOW)}
        self.assertEqual(rows["202"]["hidden"], "ignored by you")
        self.assertEqual(rows["606"]["hidden"], "")
        self.assertEqual(rows["101"]["label"], "Coding")

    def test_only_student_courses_are_shown(self):
        p = make_payload()
        p["courses"][1]["enrollments"] = [{"type": "ta", "enrollment_state": "active"}]
        rows = {c["id"]: c for c in cc_model.course_table(p, self.cfg, NOW)}
        self.assertIn("not a student", rows["202"]["hidden"])
        self.assertNotIn("202", {i["course_id"] for i in cc_model.items(p, self.cfg, NOW)})
        rows = {c["id"]: c for c in cc_model.course_table(p, dict(self.cfg, include_courses=["202"]), NOW)}
        self.assertEqual(rows["202"]["hidden"], "")
        p["courses"][1]["enrollments"] = [{"type": "TaEnrollment"}, {"type": "StudentEnrollment"}]
        self.assertEqual({c["id"]: c for c in cc_model.course_table(p, self.cfg, NOW)}["202"]["hidden"], "")

    def test_ended_term_with_current_work_stays_visible(self):
        p = make_payload()
        p["data"]["606"]["assignments"][0]["due_at"] = sample_data.iso(NOW + timedelta(days=4))
        rows = {c["id"]: c for c in cc_model.course_table(p, self.cfg, NOW)}
        self.assertEqual(rows["606"]["hidden"], "")
        p = make_payload()                       # a later course end date beats an earlier term end date
        p["courses"][5]["end_at"] = sample_data.iso(NOW + timedelta(days=30))
        self.assertEqual({c["id"]: c for c in cc_model.course_table(p, self.cfg, NOW)}["606"]["hidden"], "")

    def test_foreign_links_are_dropped(self):
        p = make_payload()
        p["data"]["101"]["assignments"][5]["html_url"] = "https://evil.example.com/x"
        p["data"]["101"]["assignments"][6]["html_url"] = "javascript:alert(1)"
        p["check"] = dict(zip(("assignments", "crc"), cc_ingest.checksum(p)))
        urls = {i["key"]: i["url"] for i in cc_model.items(p, self.cfg, NOW)}
        self.assertEqual(urls["101:1006"], "")
        self.assertEqual(urls["101:1007"], "")
        self.assertTrue(urls["101:1008"].startswith(HOST + "/"))


class ViewTests(Case):
    def test_sections(self):
        v = self.view()
        self.assertEqual(self.keys(v, "overdue"), ["101:1004", "303:3004", "101:1005"])
        self.assertEqual(self.keys(v, "week"),
                         ["101:1006", "202:2002", "303:3002", "101:1007", "303:3003", "101:1008"])
        self.assertEqual(self.keys(v, "later"), ["202:2003", "101:1011", "101:1010", "101:1012", "202:2004"])
        self.assertEqual(self.keys(v, "undated"), ["101:1014"])
        self.assertEqual(self.keys(v, "done"), [])
        self.assertEqual(v["counts"], {"overdue": 3, "check_elsewhere": 0, "week_todo": 4, "week_done": 2,
                                       "later": 5, "undated": 1, "clashes": 3})
        self.assertEqual(v["next"], "101:1006")
        self.assertFalse(v["stale"])

    def test_wording(self):
        v = self.view()
        hw6 = self.item(v, "101:1006")
        self.assertEqual(hw6["due_text"], "Mon Oct 12, 5:00 PM")
        self.assertEqual(hw6["relative"], "today, in 5 h")
        self.assertEqual(hw6["worth_text"], "10 pts, Homework: 30% of grade, 8 items so far")
        self.assertEqual(self.item(v, "202:2002")["worth_text"], "50 pts of 300 posted so far")   # unweighted course
        self.assertEqual(self.item(self.view(show_all=True), "101:1013")["worth_text"], "not counted in grade")
        self.assertEqual(self.item(v, "101:1011")["status_text"], "no upload (in class or on paper)")
        self.assertIn("opens Sat Oct 17, 12:00 PM", self.item(v, "101:1010")["notes"])
        self.assertIn("quiz", self.item(v, "101:1008")["notes"])
        self.assertIn("locked until earlier module items are finished", self.item(v, "101:1014")["notes"])
        self.assertEqual(self.item(v, "101:1004")["status_text"], "missed, now closed")

    def test_available_until_is_a_deadline(self):
        p, _ = self.one(lock=timedelta(days=2))                       # no due date, closes in two days
        v = self.view(p)
        self.assertEqual(self.keys(v, "week"), ["101:1"])
        self.assertEqual(v["counts"]["week_todo"], 1)
        self.assertEqual(v["next"], "101:1")
        self.assertIn("closes 12:00 PM (in 2 days), no due date", cc_deadlines.render_text(v))
        self.assertIn("closes 12:00 PM", cc_deadlines.render_html(v))
        p, _ = self.one(lock=timedelta(hours=-1))                     # it closed an hour ago
        v = self.view(p)
        self.assertEqual(self.keys(v, "overdue"), ["101:1"])
        self.assertIn("closed Mon Oct 12, 11:00 AM (1 h ago), had no due date — closed without a hand-in",
                      cc_deadlines.render_text(v))
        p, _ = self.one(lock=timedelta(days=-40))                     # long gone: counted, not silently dropped
        v = self.view(p)
        self.assertEqual(self.keys(v, "overdue"), [])
        self.assertTrue(any("older unsubmitted" in n for n in v["notes"]))
        p, _ = self.one(due=timedelta(days=-1), lock=timedelta(days=2))   # late, but still accepted for 2 days
        self.assertIn("closes Wed Oct 14, 12:00 PM", self.item(self.view(p), "101:1")["notes"])

    def test_zero_and_redo_are_explained(self):
        p, _ = self.one(due=timedelta(days=-2), workflow_state="graded", score=0)
        v = self.view(p)
        self.assertEqual(self.keys(v, "overdue"), ["101:1"])
        self.assertIn("marked 0, nothing handed in", self.item(v, "101:1")["notes"])
        self.assertEqual(v["counts"]["week_done"], 0)
        p, _ = self.one(due=timedelta(days=3), workflow_state="submitted", redo_request=True,
                        submitted_at="2026-10-09T00:00:00Z")
        v = self.view(p)
        self.assertEqual((v["counts"]["week_todo"], v["counts"]["week_done"]), (1, 0))
        self.assertIn("your teacher asked you to redo this", self.item(v, "101:1")["notes"])

    def test_outside_site_work_is_not_called_overdue(self):
        p, _ = self.one(due=timedelta(days=-2), types=("external_tool",))
        v = self.view(p)
        self.assertEqual((v["counts"]["overdue"], v["counts"]["check_elsewhere"]), (0, 1))
        self.assertIn("check there", self.item(v, "101:1")["status_text"])
        p, _ = self.one(due=timedelta(days=3), types=("external_tool",))           # not due yet: plain wording
        self.assertEqual(self.item(self.view(p), "101:1")["status_text"],
                         "not submitted yet (handed in through an outside tool)")
        p, _ = self.one(due=timedelta(days=-1), types=("external_tool",), workflow_state="pending_review")
        self.assertEqual(self.item(self.view(p), "101:1")["status"], "submitted")  # the tool did report it
        self.assertIn("1 past-due on an outside site", cc_deadlines.summary_line(v))

    def test_uncollected_course_is_announced(self):
        p = make_payload()
        del p["data"]["202"], p["data"]["303"]
        v = self.view(self.resign(p))
        self.assertEqual(sum("was not collected" in w for w in v["warnings"]), 2)
        self.assertIn("WARNING: MATH 202 was not collected", cc_deadlines.render_text(v))
        p["data"] = {}
        text = cc_deadlines.render_text(self.view(self.resign(p)))
        self.assertIn("no course was collected", text)
        self.assertNotIn("no assignments were found", text)
        p = make_payload()
        p["data"]["999"] = {"assignments": [], "assignment_groups": []}
        self.assertTrue(any("(999)" in w for w in self.view(self.resign(p))["warnings"]))

    def test_failures_of_other_resources_do_not_cry_wolf(self):
        p = make_payload()
        p["errors"].append({"resource": "modules", "course_id": "101", "status": 500, "message": "HTTP 500"})
        p["truncated"] = ["modules:101"]
        self.assertEqual(len(self.view(p)["warnings"]), 1)            # still only the ORI 000 one
        p["truncated"] = ["assignments:101"]
        self.assertTrue(any("cut off" in w for w in self.view(p)["warnings"]))

    def test_recently_done_and_all(self):
        p = make_payload()
        v = self.view(p, show_all=True)
        self.assertIn("101:1009", self.keys(v, "done"))
        self.assertEqual(self.item(v, "101:1009")["status_text"], "graded 88/100")
        self.assertEqual(self.item(v, "101:1002")["status_text"], "graded 7/10 (late)")
        self.assertIn("101:1013", self.keys(v, "undated"))
        soon = self.view(p, days=1)
        self.assertEqual(self.keys(soon, "week"), ["101:1006", "202:2002"])
        late = cc_deadlines.build_view(p, self.cfg, at=NOW + timedelta(days=4))
        self.assertIn("101:1007", [i["key"] for s in late["sections"] if s["key"] == "done" for i in s["items"]])

    def test_old_overdue_hidden_until_asked(self):
        cfg = dict(self.cfg, include_courses=["606"])
        v = cc_deadlines.build_view(make_payload(), cfg, at=NOW)
        self.assertNotIn("606:6001", self.keys(v, "overdue"))
        self.assertTrue(any("older unsubmitted" in n for n in v["notes"]))
        v = cc_deadlines.build_view(make_payload(), cfg, at=NOW, show_all=True)
        self.assertIn("606:6001", self.keys(v, "overdue"))

    def test_warnings_and_notes(self):
        v = self.view()
        self.assertEqual(len(v["warnings"]), 1)
        self.assertIn("ORI 000", v["warnings"][0])
        self.assertTrue(any("CHEM 110" in n and "config include 606" in n for n in v["notes"]))
        old = cc_deadlines.build_view(make_payload(), self.cfg, at=NOW + timedelta(days=3))
        self.assertTrue(old["stale"])
        self.assertIn("this data is old", cc_deadlines.render_text(old))

    def test_text_output(self):
        text = cc_deadlines.render_text(self.view(), banner="Could not refresh.")
        for needle in ("OVERDUE (3)", "NEXT 7 DAYS (6)", "LATER (5)", "NO DUE DATE (1)", "WARNING: Could not refresh.",
                       "[!] CS 101 · HW 5", "[✓] WRIT 150 · Essay 2", "Data collected Mon Oct 12, 12:00 PM"):
            self.assertIn(needle, text)

    def test_html_is_escaped_and_complete(self):
        p = make_payload()
        p["data"]["101"]["assignments"][5]["name"] = '<script>alert("x")</script>'
        page = cc_deadlines.render_html(self.view(p), banner="<b>old</b>")
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&lt;b&gt;old&lt;/b&gt;", page)
        self.assertIn(f'href="{HOST}/courses/101/assignments/1008"', page)
        self.assertTrue(page.rstrip().endswith("</html>"))

    def test_empty_payload(self):
        p = make_payload()
        p["courses"], p["data"], p["errors"] = [], {}, []
        p["check"] = dict(zip(("assignments", "crc"), cc_ingest.checksum(p)))
        self.assertIn("Nothing to show", cc_deadlines.render_text(self.view(p)))
        self.assertIn("Nothing to show", cc_deadlines.render_html(self.view(p)))

    def test_json_view_is_serialisable(self):
        json.dumps(cc_deadlines.jsonable(self.view()))


class CliTests(Case):
    def coach(self, *args, stdin=None):
        return run_cli(self.env, *args, stdin=stdin)

    def test_offline_flow(self):
        code, out = self.coach("deadlines")
        self.assertEqual(code, 3, out)
        code, out = self.coach("setup", "--host", HOST + "/courses/1", "--timezone", "America/Los_Angeles")
        self.assertEqual(code, 0, out)
        code, out = self.coach("deadlines", "--offline")
        self.assertEqual(code, 2, out)
        payload_file = self.tmp / "payload.json"
        payload_file.write_text("```json\n" + json.dumps(make_payload()) + "\n```", encoding="utf-8")
        code, out = self.coach("ingest", str(payload_file))
        self.assertEqual(code, 0, out)
        self.assertIn("23 assignment(s)", out)
        code, out = self.coach("deadlines", "--offline", "--json")
        self.assertEqual(code, 0, out)
        view = json.loads(out)
        self.assertEqual(view["counts"]["overdue"], 3)
        self.assertTrue(Path(view["page"]).exists())
        code, out = self.coach("config", "ignore", "202")
        self.assertEqual(code, 0, out)
        code, out = self.coach("deadlines", "--offline")
        self.assertNotIn("MATH 202", out)
        code, out = self.coach("courses")
        self.assertIn("ignored by you", out)
        code, out = self.coach("status")
        self.assertIn("Last collection: Mon Oct 12, 12:00 PM", out)
        self.assertFalse((Path.home() / "CanvasCoach" / ".data" / "state.json").exists()
                         and not REAL_DATA_EXISTED, "a test wrote into the real data folder")

    def test_fallback_route_is_remembered(self):
        self.coach("setup", "--host", HOST)
        f = self.tmp / "p.json"
        f.write_text(json.dumps(make_payload()), encoding="utf-8")
        self.assertEqual(self.coach("ingest", str(f))[0], 0)
        self.assertEqual(self.coach("deadlines")[0], 0)                       # fresh: nothing to refresh
        os.environ["CANVAS_COACH_NOW"] = self.env["CANVAS_COACH_NOW"] = sample_data.iso(NOW + timedelta(hours=1))
        code, out = self.coach("deadlines")                                   # stale now
        self.assertEqual(code, 4, out)
        self.assertIn("fallback route", out)
        self.assertIn("OVERDUE", out)                                         # the old list is still shown, labelled
        self.assertIn("may be out of date", out)
        self.assertEqual(self.coach("deadlines", "--offline")[0], 0)
        self.assertEqual(self.coach("config", "set", "collector", "browser")[0], 0)
        code, out = self.coach("deadlines")                                   # browser tool missing in tests
        self.assertEqual(code, 1, out)
        self.assertIn("fallback route", out)

    def test_including_a_course_that_was_not_collected_forces_a_refresh(self):
        self.coach("setup", "--host", HOST)
        p = make_payload()
        del p["data"]["202"]
        f = self.tmp / "p.json"
        f.write_text(json.dumps(Case.resign(p)), encoding="utf-8")
        self.coach("ingest", str(f))
        code, out = self.coach("deadlines")           # fresh by age, but a shown course is missing
        self.assertEqual(code, 4, out)
        self.assertIn("MATH 202 was not collected", out)
        self.assertIn("not collected yet", self.coach("courses")[1])

    def test_exit_codes_keep_their_meaning(self):
        self.assertEqual(self.coach("deadline")[0], 1)                 # a typo is not "not logged in"
        self.assertEqual(self.coach("deadlines", "--days", "two")[0], 1)
        self.coach("setup", "--host", HOST)
        self.assertEqual(self.coach("config", "ignore")[0], 1)         # bad usage is not "not set up"
        self.assertEqual(self.coach("deadlines", "--offline", "--days", "0")[0], 1)
        self.assertEqual(self.coach("config", "set", "fresh_minutes", "soon")[0], 1)
        self.assertEqual(self.coach("config", "set", "horizon_days", "99999")[0], 1)
        Path(self.env["CANVAS_COACH_HOME"], "config.json").write_text("{ broken", encoding="utf-8")
        code, out = self.coach("status")
        self.assertEqual(code, 1, out)
        self.assertNotIn("Traceback", out)

    def test_damaged_copy_is_refused(self):
        self.coach("setup", "--host", HOST)
        p = make_payload()
        p["data"]["101"]["assignments"][5]["due_at"] = None
        code, out = self.coach("ingest", "-", stdin=json.dumps(p))
        self.assertEqual(code, 1, out)
        self.assertIn("Integrity check failed", out)
        self.assertEqual(cc_store.list_snapshots(dict(self.cfg)), [])

    def test_config_validation(self):
        self.coach("setup", "--host", HOST)
        code, out = self.coach("config", "set", "timezone", "Mars/Olympus")
        self.assertEqual(code, 1, out)
        code, out = self.coach("config", "set", "horizon_days", "10")
        self.assertEqual(code, 0, out)
        code, out = self.coach("config", "show")
        self.assertEqual(json.loads(out)["horizon_days"], 10)
        code, out = self.coach("config", "set", "nonsense", "1")
        self.assertEqual(code, 1, out)

    def test_fetch_script_embeds_settings(self):
        self.coach("setup", "--host", HOST)
        self.coach("config", "ignore", "606")
        code, out = self.coach("fetch-script", "--course", "101")
        self.assertEqual(code, 0, out)
        self.assertIn("window.__ccCollect = async function", out)
        tail = out.rstrip().splitlines()[-1]
        start, end = "(JSON.stringify(await window.__ccCollect(", ")).match(/[\\s\\S]{1,600}/g))"
        self.assertTrue(tail.startswith(start) and tail.endswith(end), tail[-60:])
        settings = json.loads(tail[len(start):-len(end)])
        self.assertEqual((settings["host"], settings["only_courses"], settings["skip_courses"], settings["slim_days"]),
                         (HOST, ["101"], ["606"], 30))


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class CollectorTests(Case):
    """Runs the real fetch.js under Node against the fake Canvas."""

    def setUp(self):
        super().setUp()
        os.environ.pop("CANVAS_COACH_NOW", None)   # the fake Canvas uses the real clock
        self.server = mock_canvas.serve(0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.cfg["host"] = self.origin

    def collect(self, cookie=mock_canvas.COOKIE, **settings):
        import cc_browser
        resources = settings.pop("resources", None) or cc_browser.load_resources(self.cfg)
        f = self.tmp / "settings.json"
        f.write_text(json.dumps(dict({"host": self.origin, "resources": resources, "max_pages": 50}, **settings)))
        p = subprocess.run(["node", str(HERE / "node_collect.js"), self.origin, str(f), cookie or ""],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_full_collection_matches_python(self):
        result = self.collect()
        cc_ingest.validate(result, self.cfg)                     # same checksum on both sides
        self.assertEqual(result["check"]["assignments"], 23)      # 14 came through 3 pages of 5
        self.assertEqual(sorted(result["data"]), ["101", "202", "303", "505", "606"])
        self.assertEqual({r["name"] for r in RESOURCES["per_course"]},
                         {"assignments", "assignment_groups", "modules", "submissions"},
                         "resources.json changed: update the expectations in this test")
        self.assertEqual(result, json.loads(json.dumps(result)))
        mods = result["data"]["101"]["modules"]
        self.assertEqual([m["name"][:6] for m in mods], ["Week 1", "Week 2", "Week 3", "Week 4"])
        self.assertEqual(len(mods[3]["items"]), 4)                    # left out by Canvas, fetched by "expand"
        page = next(i for i in mods[2]["items"] if i["type"] == "Page")
        self.assertEqual((page["page_url"], set(page) >= {"id", "title", "html_url", "url"}), ("week-3-notes", True))
        self.assertNotIn("module_id", page)
        hw6 = next(i for i in mods[2]["items"] if i["type"] == "Assignment")
        self.assertEqual(hw6["content_details"]["points_possible"], 10)
        subs = {x["assignment_id"]: x for x in result["data"]["101"]["submissions"]}
        self.assertEqual(len(subs), 14)
        self.assertEqual([c["author_name"] for c in subs[1009]["submission_comments"]], ["Prof. Ada", "Tess"])
        self.assertEqual(subs[1009]["rubric_assessment"]["_c1"]["points"], 38)
        self.assertNotIn("body", subs[1009])
        titles = {a["title"] for a in result["global"]["announcements"]}
        self.assertEqual(titles, {"Midterm room change", "Office hours", "Problem Set 2 typo", "Old chemistry notice"})
        self.assertNotIn("conversations", result["global"])           # the inbox is off unless asked for
        self.assertTrue(result["global"]["profile"]["calendar"]["ics"].endswith(".ics"))
        scores = {c["id"]: (c.get("enrollments") or [{}])[0].get("computed_current_score") for c in result["courses"]}
        self.assertEqual(scores[101], 86.4)
        payload, _ = cc_ingest.ingest([result], self.cfg)             # the private link moves to the settings
        self.assertNotIn("calendar", payload["global"]["profile"])
        self.assertTrue(cc_store.load_config()["calendar_feed"].endswith(".ics"))
        reflection = next(a for a in result["data"]["101"]["assignments"] if a["id"] == 1014)
        self.assertEqual(reflection["lock_info"], {"context_module": {"id": 77}})   # nested keep path
        self.assertIs(reflection["submission"]["redo_request"], False)
        self.assertEqual({e["resource"] for e in result["errors"]},
                         {"assignments", "assignment_groups", "modules", "submissions"})
        self.assertTrue(all(e["course_id"] == "505" and e["status"] == 401 for e in result["errors"]))
        a = result["data"]["101"]["assignments"][0]
        self.assertNotIn("description", a)
        self.assertNotIn("body", a["submission"])
        self.assertEqual(result["courses"][0]["term"]["name"], "Fall 2026")
        self.assertEqual(result["courses"][0]["enrollments"], [{
            "type": "student", "enrollment_state": "active", "computed_current_score": 86.4,
            "computed_current_grade": "B", "computed_final_score": 66.4, "computed_final_grade": "C"}])
        homework = next(g for g in result["data"]["101"]["assignment_groups"] if g["name"] == "Homework")
        self.assertEqual((homework["points_total"], homework["assignment_count"]), (80, 8))
        view = cc_deadlines.build_view(result, self.cfg)
        self.assertEqual(view["counts"]["overdue"], 3)
        self.assertTrue(any(r.startswith("GET /api/v1/courses/101/assignments?") and "page=3" in r
                            for r in self.server.requests))
        self.assertFalse(any(not r.startswith("GET ") for r in self.server.requests))

    def test_not_logged_in(self):
        result = self.collect(cookie="")
        self.assertEqual(result["error"], "unauthenticated")
        with self.assertRaises(cc_ingest.NotLoggedIn):
            cc_ingest.validate(result, self.cfg)

    def test_outage_and_foreign_next_link(self):
        self.server.RequestHandlerClass.down = True
        self.addCleanup(setattr, self.server.RequestHandlerClass, "down", False)
        result = self.collect()
        self.assertEqual((result["error"], result["status"]), ("canvas_unavailable", 503))
        self.server.RequestHandlerClass.down = False
        self.server.RequestHandlerClass.foreign_next = True
        self.addCleanup(setattr, self.server.RequestHandlerClass, "foreign_next", False)
        result = self.collect(only_courses=["101"])
        cc_ingest.validate(result, self.cfg)
        self.assertIn("assignments:101", result["truncated"])        # the cut-off list is declared, not hidden
        self.assertFalse(any("elsewhere.example" in r for r in self.server.requests))

    def test_inbox_only_when_switched_on_and_one_off_requests(self):
        self.cfg["inbox"] = True
        result = self.collect(only_courses=["202"])
        self.assertEqual([c["subject"] for c in result["global"]["conversations"]], ["Re: extension request", "Welcome"])
        self.assertEqual(result["global"]["conversations"][0]["participants"][0], {"id": 77, "name": "Prof. Ada"})
        one_off = {"courses": None, "per_course": [], "global": [
            {"name": "page", "path": "/api/v1/courses/101/pages/week-3-notes", "keep": None},
            {"name": "file", "path": "/api/v1/courses/101/files/7001", "keep": ["display_name", "size", "url"]},
            {"name": "gone", "path": "/api/v1/courses/101/pages/nope", "keep": None}]}
        result = self.collect(resources=one_off, only_courses=["-"])
        self.assertEqual(result["courses"], [])
        self.assertIn("chapter 3", result["global"]["page"]["body"])
        self.assertEqual(result["global"]["file"]["display_name"], "lecture3.pdf")
        self.assertEqual([(e["resource"], e["status"]) for e in result["errors"]], [("gone", 404)])
        cc_ingest.raise_for_error(result)

    def test_wrong_site(self):
        result = self.collect(host="https://canvas.other.edu")
        self.assertEqual(result["error"], "wrong_origin")

    def test_slim_skip_and_only(self):
        slim = self.collect(slim_days=7)
        cc_ingest.validate(slim, self.cfg)
        ids = {a["id"] for a in slim["data"]["101"]["assignments"]}
        self.assertNotIn(1001, ids)            # graded a month ago: dropped
        self.assertIn(1004, ids)               # missed: kept
        self.assertIn(1005, ids)               # still open: kept
        self.assertGreater(slim["slim"]["dropped"], 0)
        row = next(a for a in slim["data"]["101"]["assignments"] if a["id"] == 1006)      # empty fields are left out
        self.assertEqual(sorted(row), ["allowed_attempts", "assignment_group_id", "due_at", "grading_type", "html_url",
                                       "id", "name", "points_possible", "submission", "submission_types"])
        self.assertEqual(row["submission"], {"workflow_state": "unsubmitted"})
        self.assertNotIn("rules", slim["data"]["202"]["assignment_groups"][0])
        homework = next(g for g in slim["data"]["101"]["assignment_groups"] if g["name"] == "Homework")
        self.assertEqual(homework["points_total"], 80)   # weights still right after dropping rows
        some = self.collect(only_courses=["202"], skip_courses=["606"])
        self.assertEqual(sorted(some["data"]), ["202"])
        self.assertEqual(len(some["courses"]), 6)


if __name__ == "__main__":
    unittest.main(verbosity=1)
