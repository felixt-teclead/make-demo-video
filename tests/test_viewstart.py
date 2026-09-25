"""View start positions (vc/overlay/viewstart.py, D-33 item 3): rule validation, the install call order, the take
runner wiring (installed before any page loads, a bad rule stops the run), and the page script's own browserless
test (tests/viewstart_page.test.js, run with node when it is on PATH).

Run: python3 -m unittest tests.test_viewstart
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path[:0] = [ROOT, os.path.join(ROOT, "loop")]

from vc.overlay import viewstart as V  # noqa: E402
from vcloop import profiles as P  # noqa: E402
from vcloop import take_runner as T  # noqa: E402

RULE = {"id": "sticky", "url_regex": "/processes/[0-9a-f-]{36}", "selector": ".react-flow__viewport",
        "when": ".lane", "by": "wheel", "x": 0, "y": 0, "wheel_px_per_unit": 0.5, "settle_ms": 1500,
        "why": "specs/quirks/x.md#sticky"}


class Rules(unittest.TestCase):
    def test_valid_rule_is_kept_without_prose(self):
        r = V.validate(RULE)
        self.assertNotIn("why", r)
        self.assertEqual((r["by"], r["x"], r["y"]), ("wheel", 0, 0))
        self.assertEqual(V.validate({"id": "a", "url_regex": ".", "selector": "x", "y": 3})["by"], "scroll")

    def test_bad_rules_are_refused_with_a_reason(self):
        bad = [({**RULE, "by": "drag"}, "by"), ({**RULE, "x": None, "y": None}, "x"), ({**RULE, "url_regex": "("}, "compile"),
               ({**RULE, "selector": ""}, "selector"), ({**RULE, "x": "0"}, "number"), ({**RULE, "x": True}, "number"),
               ({**RULE, "wheel_px_per_unit": 0}, "> 0"), ({**RULE, "speed": 1}, "unknown")]
        for rule, word in bad:
            with self.assertRaises(V.RuleError) as cm:
                V.validate(rule)
            self.assertIn(word, str(cm.exception))

    def test_rules_from_profiles_and_duplicate_ids(self):
        profs = [{"name": "a", "view_start": [RULE]}, {"name": "b"}]
        self.assertEqual([r["id"] for r in V.rules_from_profiles(profs)], ["sticky"])
        with self.assertRaises(V.RuleError):
            V.rules_from_profiles(profs + [{"name": "c", "view_start": [RULE]}])
        self.assertEqual(V.rules_from_profiles(None), [])

    def test_example_profile_rule_matches_the_process_view_only(self):
        rules = V.rules_from_profiles([P.match("https://app.example.com/de/processes")])
        r = {x["id"]: x for x in rules}["sticky-lane-headers"]
        import re
        self.assertTrue(re.search(r["url_regex"], "https://app.example.com/de/processes/00000000-0000-4000-8000-000000000001"))
        self.assertTrue(re.search(r["url_regex"], "https://app.example.com/de/processes/00000000-0000-4000-8000-000000000001?orient=h"))
        self.assertFalse(re.search(r["url_regex"], "https://app.example.com/de/processes"))
        self.assertEqual((r["x"], r["y"], r["by"]), (0, 0, "wheel"))
        quirks = open(os.path.join(ROOT, "specs", "quirks", "app.example.com.md"), encoding="utf-8").read()
        self.assertIn("## sticky-lane-headers", quirks)            # D-31: the rule cites an existing record
        for field in ("**id:**", "**views:**", "**symptom:**", "**detection:**", "**mitigation"):
            self.assertIn(field, quirks.split("## sticky-lane-headers", 1)[1])


class Install(unittest.TestCase):
    def test_install_enables_page_then_adds_the_document_start_script_then_runs_it_now(self):
        calls = []

        def call(method, **kw):
            calls.append((method, kw))
            return {"identifier": "7"} if method == "Page.addScriptToEvaluateOnNewDocument" else {}
        self.assertEqual(V.install(call, [V.validate(RULE)]), "7")
        self.assertEqual([c[0] for c in calls], ["Page.enable", "Page.addScriptToEvaluateOnNewDocument", "Runtime.evaluate"])
        src = calls[1][1]["source"]
        self.assertEqual(src, calls[2][1]["expression"])
        self.assertTrue(src.startswith("/* vc view start") and src.endswith(");"))
        self.assertIn(json.dumps([V.validate(RULE)]), src)
        self.assertIsNone(V.install(call, []))
        self.assertEqual(len(calls), 3)

    def test_log_and_summary(self):
        e = {"id": "sticky", "frame": 5, "seen_frame": 5, "end_frame": 5, "from": {"x": 24, "y": 56},
             "got": {"x": 0, "y": 0}, "rounds": 1}
        self.assertEqual(V.read_log(lambda js: [e]), [e])
        self.assertEqual(V.read_log(lambda js: (_ for _ in ()).throw(RuntimeError("stale"))), [])
        s = V.summary([e, {**e, "frame": 9, "end_frame": 9}])
        self.assertEqual([x["before_first_paint"] for x in s], [True, False])


class Tab:
    def __init__(self, log):
        self.log = log

    def call(self, method, **kw):
        self.log.append(("cdp", method))
        return {"identifier": "1"}

    def js(self, expr):
        self.log.append(("js", expr[:30]))
        if expr == V.LOG_JS:
            return [{"id": "sticky", "frame": 3, "seen_frame": 3, "end_frame": 3, "from": {}, "got": {}, "rounds": 1}]
        return "https://app.example.com/x"

    def load_start_url(self, url):
        self.log.append(("load", url))


class Session:
    def __init__(self, log):
        self.tab = Tab(log)

    def check(self, checks, seconds):
        return {"ok": True}

    def run_step(self, step):
        return None


def _request(profiles):
    spec = {"start": {"url": "https://app.example.com/de/processes", "ready": [], "warm": ["https://w/1"]},
            "steps": [{"name": "a", "expect": [], "actions": []}]}
    kn = {"ready_timeout_s": 1, "settle_s": 1}
    return {"mode": "dry", "run_dir": os.path.join(ROOT, ".verify", "viewstart"), "run_id": "r", "job": "j",
            "spec": spec, "knobs": kn, "profiles": profiles}


class Runner(unittest.TestCase):
    def test_rules_install_before_the_first_load_and_the_log_lands_in_the_result(self):
        log = []
        res = T.run(_request([{"name": "t", "view_start": [RULE]}]), session_factory=lambda hk: Session(log))
        self.assertTrue(res["ok"], res)
        order = [x for x in log if x[0] in ("cdp", "load")]
        self.assertEqual(order[:3], [("cdp", "Page.enable"), ("cdp", "Page.addScriptToEvaluateOnNewDocument"),
                                     ("cdp", "Runtime.evaluate")])
        self.assertEqual(order[3], ("load", "https://w/1"))
        self.assertEqual(res["view_start"][0]["before_first_paint"], True)

    def test_no_rules_no_install_no_log(self):
        log = []
        res = T.run(_request([{"name": "t"}]), session_factory=lambda hk: Session(log))
        self.assertTrue(res["ok"])
        self.assertNotIn(("cdp", "Page.enable"), log)
        self.assertNotIn("view_start", res)

    def test_a_bad_rule_stops_the_run_before_any_load(self):
        log = []
        res = T.run(_request([{"name": "t", "view_start": [{**RULE, "by": "drag"}]}]),
                    session_factory=lambda hk: Session(log))
        self.assertFalse(res["ok"])
        self.assertEqual(res["error_class"], "profile")
        self.assertFalse([x for x in log if x[0] == "load"])


class PageScript(unittest.TestCase):
    def test_page_script_in_node(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not on PATH: the page script's browserless test is tests/viewstart_page.test.js")
        r = subprocess.run([node, os.path.join(ROOT, "tests", "viewstart_page.test.js")], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
