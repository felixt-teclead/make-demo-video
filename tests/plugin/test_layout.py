"""Static checks of the plugin layer: F-17, F-18, F-20, C-38, C-40 (stdlib unittest)."""
import glob
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def read(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return f.read()


def frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    return m.group(1) if m else ""


SKILLS = sorted(os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "skills", "*", "SKILL.md")))
STEPS = sorted(os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "docs", "steps", "*.md")))
AGENTS = sorted(os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(ROOT, "agents", "*.md")))
PLUGIN_DOCS = SKILLS + STEPS + AGENTS + ["docs/house-rules.md"]
PLUGIN_FILES = PLUGIN_DOCS + [".claude-plugin/plugin.json", "hooks/hooks.json", "hooks/guard.py"]

# one distinctive phrase per house rule (F-17: each rule lives in exactly one file)
HOUSE_RULES = {
    "read-only by default": r"Read-only by default",
    "no credentials": r"No credentials in any model context",
    "jev for all browser work": r"jev for all browser work",
    "one browser": r"One browser, one lock",
    "never touch the QA definition": r"Never touch the QA definition",
    "approval": r"Approval belongs to the approver",
    "style defaults": r"No human mimicry",
}


class LayoutTest(unittest.TestCase):
    def test_manifest(self):
        m = json.loads(read(".claude-plugin/plugin.json"))
        self.assertEqual(m["name"], "make-demo-video")
        self.assertTrue(m.get("version") and m.get("description"))
        self.assertTrue(SKILLS and AGENTS)

    def test_skill_set(self):
        """F-17: the end-to-end entry skill and the separate spec skill; loop phases get no skill."""
        self.assertEqual(SKILLS, ["skills/make-demo-video/SKILL.md", "skills/write-demo-spec/SKILL.md"])
        spec = read("skills/write-demo-spec/SKILL.md")
        self.assertIn("docs/steps/align.md", spec, "the spec skill routes to the one align step doc")
        self.assertIn("docs/steps/align.md", read("skills/make-demo-video/SKILL.md"))
        self.assertNotIn("bin/vc-loop run", spec)

    def test_hooks_config(self):
        h = json.loads(read("hooks/hooks.json"))["hooks"]["PreToolUse"][0]
        self.assertEqual(h["matcher"], "*", "the guard must see every tool (C2: any tool may reach the secrets)")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/guard.py", h["hooks"][0]["command"])

    def test_skill_descriptions_trigger_and_non_trigger(self):
        for p in SKILLS + AGENTS:
            fm = frontmatter(read(p))
            self.assertRegex(fm, r"(?m)^name: \S+", p)
            desc = fm.split("description:", 1)[1]
            self.assertRegex(desc, r"Use (when|only when)", p)
            self.assertRegex(desc, r"(Do NOT use for|Not for)", p)
        desc = frontmatter(read("skills/make-demo-video/SKILL.md"))
        for phrase in ("demo video of", "only the spec", "chat-app scenes", "QA checks, thresholds",
                       "single screenshot"):
            self.assertIn(phrase, desc)
        self.assertIn("write-demo-spec", desc)
        desc = frontmatter(read("skills/write-demo-spec/SKILL.md"))
        for phrase in ("only the spec", "make-demo-video", "chat-app scenes", "QA checks, thresholds",
                       "single screenshot", "diff"):
            self.assertIn(phrase, desc)

    def test_each_house_rule_in_exactly_one_file(self):
        for rule, pat in HOUSE_RULES.items():
            hits = [p for p in PLUGIN_DOCS if re.search(pat, read(p), re.I)]
            self.assertEqual(hits, ["docs/house-rules.md"], f"{rule}: {hits}")
        for p in PLUGIN_DOCS:
            if p != "docs/house-rules.md":
                self.assertNotRegex(read(p), r"final hold 12 s|1\.5 to 6 s", p)

    def test_step_docs_reachable_from_the_skill(self):
        skill = "".join(read(p) for p in SKILLS) + read("docs/steps/run.md") + read("agents/fixer.md")
        for p in STEPS:
            self.assertIn(p, skill)

    def test_loop_phases_described_once_not_per_skill(self):
        """No skill or step doc runs record/cut/QA/join by hand (F-17): the loop owns them."""
        for p in SKILLS + STEPS + AGENTS:
            t = read(p)
            self.assertNotRegex(t, r"bin/vc-(cut|gate)\s+\S|/record/(start|stop)|ffmpeg|python3 -m vc\.cut", p)
            numbered = re.findall(r"(?mi)^\s*\d+\.\s*\**(record|film|cut|run (the )?qa|qa|join)\b", t)
            self.assertLessEqual(len(numbered), 0, f"{p}: {numbered}")

    def test_no_c38_items(self):
        pats = [r"\.gif\b", r"gifsicle|imageio|palettegen", r"playwright|puppeteer|selenium|pyppeteer",
                r"jitter\s*[=:]|typo_?rate|humani[sz]e", r"claude\.ai/(new|chat)|data-testid=\"chat",
                r"chat_selector"]
        for p in PLUGIN_FILES:
            for pat in pats:
                self.assertNotRegex(read(p), re.compile(pat, re.I), p)
        for ci in (".github/workflows", ".gitlab-ci.yml", ".circleci", "Jenkinsfile"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, ci)), ci)

    def test_no_app_specifics(self):
        """C-40: no host names or app labels in skills, step docs, agent or hooks."""
        for p in PLUGIN_FILES:
            self.assertNotRegex(read(p), r"app\.example\.com|Prozesse|Swimlane|Urlaubsantrag", p)

    def test_fixer_minimal_tools(self):
        fm = frontmatter(read("agents/fixer.md"))
        tools = {t.strip() for t in re.search(r"(?m)^tools: (.*)$", fm).group(1).split(",")}
        self.assertEqual(tools, {"Read", "Grep", "Glob", "Edit", "Write", "Bash"})
        self.assertRegex(fm, r"(?m)^model: inherit$")


if __name__ == "__main__":
    unittest.main()
