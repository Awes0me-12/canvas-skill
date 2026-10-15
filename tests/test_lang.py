"""Tests for the output language: the catalogue is complete, and Chinese output has no English sentences in it."""
from helpers import *  # noqa: F401,F403

import html
import re
import string

import cc_brief
import cc_changes
import cc_feedback
import cc_grades
import cc_lang
import cc_lang_zh
import cc_plan
import cc_state
import cc_text
import cc_week
import lang_keys

# Words that stay in English on purpose: names of things, commands and units of the tools themselves.
ALLOWED = ("Canvas Coach", "Canvas", "agent-browser", "SKILL.md", "Apple", "Google", "macOS", "Mac", "Python", "JSON",
           "Documents", "Desktop", "Downloads", "PDT", "PST", "key", "TRUNCATED", "BLOCKED")
TWO_ENGLISH_WORDS = re.compile(r"[A-Za-z]{2,}(?:[ '’][A-Za-z]{2,})+")


def fields(text):
    return sorted({name for _, name, _, _ in string.Formatter().parse(text) if name})


def strings_in(value, found=None):
    """Every piece of text inside the sample data (names, titles, messages), longest first."""
    found = set() if found is None else found
    if isinstance(value, dict):
        for v in value.values():
            strings_in(v, found)
    elif isinstance(value, list):
        for v in value:
            strings_in(v, found)
    elif isinstance(value, str) and len(value) >= 2:
        found.add(value)
        found.add(cc_text.one_line(value))
        if "<" in value:                                   # what the views make of HTML
            found.update({cc_text.html_to_text(value), cc_text.excerpt(value, 400), cc_text.excerpt(value, 200),
                          cc_text.excerpt(value, 120)})
            found.update(line.strip() for line in cc_text.html_to_text(value).splitlines())
    return sorted((s for s in found if len(s) >= 2), key=len, reverse=True)


def english_left(text, data):
    """English runs of two or more words that are not names from the data, commands or allowed terms."""
    for s in strings_in(data):
        text = text.replace(s, "§")
    text = re.sub(r"coach\.py[^\n。；，）)]*", "§", text)             # commands stay as typed
    text = re.sub(r"https?://\S+|/\S+\.(?:html|md|ics|json)|~?/[\w./ -]+", "§", text)   # addresses and paths
    text = re.sub(r"--[a-z-]+", "§", text)
    for word in ALLOWED:
        text = text.replace(word, "§")
    return TWO_ENGLISH_WORDS.findall(text)


class CatalogueTests(Case):
    def test_every_sentence_is_translated_and_no_entry_is_stale(self):
        used = {k for keys in lang_keys.all_keys().values() for k in keys}
        self.assertGreater(len(used), 500)
        self.assertEqual(sorted(used - set(cc_lang_zh.TABLE)), [], "sentences in the code without a Chinese translation")
        self.assertEqual(sorted(set(cc_lang_zh.TABLE) - used), [], "translations of sentences the code no longer has")

    def test_placeholders_are_the_same_on_both_sides(self):
        wrong = [k for k, v in cc_lang_zh.TABLE.items() if fields(k) != fields(v)]
        self.assertEqual(wrong, [])
        self.assertTrue(all(isinstance(v, str) and v for v in cc_lang_zh.TABLE.values()))

    def test_english_is_the_default_and_an_unknown_language_falls_back(self):
        self.assertEqual((cc_lang.use(None), cc_lang.T("Saved.")), ("en", "Saved."))
        self.assertEqual((cc_lang.use("klingon"), cc_lang.T("{n} overdue", n=2)), ("en", "2 overdue"))
        self.addCleanup(cc_lang.use, "en")
        for name in ("zh", "ZH-CN", "chinese", "中文"):
            self.assertEqual((cc_lang.use(name), cc_lang.T("Saved.")), ("zh", "已保存。"), name)
        self.assertEqual(cc_lang.T("a sentence nobody translated"), "a sentence nobody translated")
        self.assertEqual(cc_lang.plural(1, "{n} change", "{n} changes"), "1处变化")


class ChineseTests(Case):
    def setUp(self):
        super().setUp()
        cc_lang.use("zh")
        self.addCleanup(cc_lang.use, "en")
        self.cfg["language"] = "zh"

    def test_dates_and_distances(self):
        tz = cc_time.zone(self.cfg)
        at = cc_time.local(NOW, tz)
        due = cc_time.local(datetime(2026, 10, 16, 6, 59, tzinfo=timezone.utc), tz)       # Thu Oct 15, 11:59 PM
        self.assertEqual((cc_time.stamp(due), cc_time.day(due, with_year=True)), ("10月15日 周四 23:59", "2026年10月15日 周四"))
        self.assertEqual(cc_time.clock(due.replace(hour=0, minute=5)), "0:05")
        self.assertEqual(cc_time.clock(due.replace(hour=9, minute=5)), "9:05")
        rel = lambda **kw: cc_time.relative(cc_time.local(NOW + timedelta(**kw), tz), at)
        self.assertEqual([rel(minutes=30), rel(hours=5), rel(days=1), rel(days=3), rel(hours=-2), rel(days=-1), rel(days=-4)],
                         ["30分钟后", "今天，5小时后", "明天", "3天后", "2小时前", "昨天", "4天前"])
        self.assertEqual(cc_time.midnight_note(due.replace(hour=0, minute=30)), "刚过午夜，实际相当于周三晚上")

    def test_no_view_leaks_english_sentences(self):
        payload = make_payload(inbox=True)
        self.cfg.update(inbox=True, rest_days=["fri", "sat", "sun"],
                        class_times={"101": [{"days": ["wed"], "time": "10:00", "kind": "lecture"}]})
        courses = cc_model.course_table(payload, self.cfg, NOW)
        cc_state.add_manual(self.cfg, courses, "writ", "Outline", NOW + timedelta(days=5), kind="exam", weight=20,
                            place="HSSB 1174", source="syllabus", confirmed=False)
        cc_state.edit_manual(self.cfg, "m1", also=(NOW + timedelta(days=6), "email"))
        cc_state.set_override(self.cfg, "101:1014", None, due=NOW + timedelta(days=4), note="ask first")
        cc_state.add_question(self.cfg, "Is the outline graded?", courses, "writ")
        old = make_payload()
        old["collected_at"] = sample_data.iso(NOW - timedelta(hours=6))
        old["data"]["202"]["assignments"][1]["due_at"] = sample_data.iso(NOW + timedelta(days=3))
        old["data"]["101"]["assignments"] = [a for a in old["data"]["101"]["assignments"] if a["id"] != 1010]
        del old["data"]["101"]["assignments"][5]["hidden_text"]
        changes = cc_changes.diff(Case.resign(old), payload, self.cfg, NOW)
        self.assertGreaterEqual({c["kind"] for c in changes}, {"due", "new", "hidden"})
        deadlines = cc_deadlines.build_view(payload, self.cfg, at=NOW, show_all=True)
        week = cc_week.build_view(payload, self.cfg, at=NOW)
        brief = cc_brief.build(payload, self.cfg, at=NOW)
        brief["must"] = cc_plan.todays_must(payload, self.cfg, at=NOW)
        plan = cc_plan.build(payload, self.cfg, at=NOW)
        texts = {
            "deadlines": cc_deadlines.render_text(deadlines, "无法刷新", keys=True, changes=cc_changes.render_text(changes, "x")),
            "deadlines page": cc_deadlines.render_html(deadlines, "", cc_changes.render_html(changes, "x")),
            "week": cc_week.render_text(week), "week page": cc_week.render_html(week),
            "brief": cc_brief.render_text(brief), "notification": " ".join(cc_brief.notification(brief, changes)),
            "grades": cc_grades.render_text(cc_grades.build_view(payload, self.cfg, at=NOW, target=85)),
            "grades page": cc_grades.render_html(cc_grades.build_view(payload, self.cfg, at=NOW, target=85)),
            "feedback": cc_feedback.render_text(cc_feedback.build_view(payload, self.cfg, at=NOW, everything=True)),
            "plan": cc_plan.render_text(plan, keys=True), "plan page": cc_plan.render_html(plan),
        }
        extra = {"notes": ["Outline", "HSSB 1174", "syllabus", "email", "ask first", "Is the outline graded?"]}
        for name, text in texts.items():
            if name.endswith("page"):
                self.assertIn('<html lang="zh-Hans">', text, name)
                text = html.unescape(re.sub(r"<style>.*?</style>|<[^>]+>", " ", text, flags=re.S))
            self.assertEqual(english_left(text, [payload, old, extra]), [], name)
        self.assertIn("今日必做", texts["brief"])
        self.assertIn("日期未确认", texts["deadlines"])
        self.assertIn("这是你自己设的日期；Canvas 上没有日期", texts["deadlines"])

    def test_the_setting_switches_every_command(self):
        run_cli(self.env, "setup", "--host", HOST)
        cc_store.save_snapshot(self.cfg, make_payload())
        self.assertIn("Saved.", run_cli(self.env, "config", "set", "inbox", "on")[1])
        code, out = run_cli(self.env, "config", "set", "language", "chinese")
        self.assertEqual((code, out.strip()), (0, "已保存。"))
        self.assertEqual(cc_store.load_config()["language"], "zh")
        cc_lang.use("zh")
        code, out = run_cli(self.env, "deadlines", "--offline")
        self.assertEqual(code, 0, out)
        for needle in ("Canvas 截止日期，截至 10月12日 周一 12:00", "已逾期（3）", "CS 101 · HW 6 — 17:00（今天，5小时后） — 未提交",
                       "数据读取于 10月12日 周一 12:00"):
            self.assertIn(needle, out)
        self.assertIn("语言：         zh", run_cli(self.env, "status")[1])
        code, out = run_cli(self.env, "done", "nothing", "like", "this")
        self.assertEqual(code, 1)
        self.assertIn("没有找到与“nothing like this”匹配的项目", out)
        self.assertIn("出错：", run_cli(self.env, "config", "set", "language", "french")[1])
        code, out = run_cli(dict(self.env, CANVAS_COACH_LANG="en"), "deadlines", "--offline")     # the override, for trial runs
        self.assertIn("OVERDUE (3)", out)
        self.assertEqual(run_cli(self.env, "config", "set", "language", "en")[1].strip(), "Saved.")


if __name__ == "__main__":
    unittest.main(verbosity=1)
