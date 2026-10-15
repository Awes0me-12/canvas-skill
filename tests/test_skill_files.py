"""Tests that the skill's own files stay valid for both Claude Code and Codex, and that install.sh links safely."""
from helpers import *  # noqa: F401,F403

import re

ROOT = HERE.parent


class SkillFileTests(Case):
    def test_skill_md_header_suits_both_agents(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        self.assertIsNotNone(m, "SKILL.md must start with a --- header ---")
        fields = dict(line.split(": ", 1) for line in m.group(1).splitlines())
        self.assertEqual(sorted(fields), ["description", "name"], "only the fields both agents understand")
        self.assertRegex(fields["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(fields["name"]), 64)
        self.assertLessEqual(len(fields["description"]), 1024)
        self.assertNotIn("<", fields["description"])
        self.assertIn("$canvas-coach", text)            # how Codex users call it is documented
        self.assertIn("outside the sandbox", text)

    def test_codex_display_file(self):
        text = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        values = dict(re.findall(r'^\s+(\w+): "(.*)"$', text, re.M))
        self.assertEqual(values["display_name"], "Canvas Coach")
        self.assertTrue(25 <= len(values["short_description"]) <= 64, values["short_description"])
        self.assertIn("$canvas-coach", values["default_prompt"])
        self.assertRegex(text, r"(?m)^policy:\n  allow_implicit_invocation: true$")

    def test_install_links_both_apps_and_never_replaces_a_real_folder(self):
        home = self.tmp / "home dir"
        env = dict(os.environ, HOME=str(home))
        env.pop("CODEX_HOME", None)
        run = lambda: subprocess.run(["bash", str(ROOT / "install.sh")], env=env, capture_output=True, text=True)
        p = run()
        self.assertEqual(p.returncode, 0, p.stderr)
        for app in (".claude", ".codex"):
            link = home / app / "skills" / "canvas-coach"
            self.assertTrue(link.is_symlink() and link.resolve() == ROOT.resolve(), (app, p.stdout))
        self.assertEqual(run().returncode, 0, "running it twice is fine")
        mine = home / ".codex" / "skills" / "canvas-coach"
        mine.unlink()
        mine.mkdir()
        (mine / "notes.txt").write_text("keep me")
        p = run()
        self.assertIn("already exists and is not a link; left it alone", p.stdout)
        self.assertEqual((mine / "notes.txt").read_text(), "keep me")
        env["CODEX_HOME"] = str(self.tmp / "codex home")
        run()
        self.assertTrue((self.tmp / "codex home" / "skills" / "canvas-coach").is_symlink())


if __name__ == "__main__":
    unittest.main(verbosity=1)
