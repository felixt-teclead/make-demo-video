"""Review Major 2 (Q-51) end to end: the loop's event log (loop/vcloop/gatefeed.py) carries the spec's hold list per
clip, and the real gate judges the logged holds against it: a missing hold FAILs, and it does not switch off the
length check of the clip's other holds."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [os.path.join(ROOT, "loop")]

from vcloop import gatefeed  # noqa: E402

GATE = os.path.join(ROOT, "bin", "vc-gate")
FIX = os.environ.get("VC_FIXTURES") or os.path.abspath(os.path.join(ROOT, "..", "vc-v1-spec", "acceptance", "fixtures"))
SUCHE = os.path.join(FIX, "clean-reference-take", "suche.mp4")      # 3.07 s, click at 1.19 s
NO_FIX = ("Q-90 fixture clips not found (recordings of a private app, not in the public repo); set VC_FIXTURES "
          "to run this test")

SPEC = {"start": {"url": "https://x.example", "hold": 1.0}, "length": [1, 60],
        "steps": [{"name": "suche", "hold": 1.0,
                   "actions": [{"op": "click", "control": {"label": "Suche"}},
                               {"op": "hold", "seconds": 0.5}]}]}


class GatefeedHolds(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="gatefeed-holds-")
        os.makedirs(os.path.join(self.dir, "clips"))
        os.makedirs(os.path.join(self.dir, "cut"))
        os.symlink(SUCHE, os.path.join(self.dir, "clips", "01-suche.mp4"))
        with open(os.path.join(self.dir, "clips", "index.json"), "w") as f:
            json.dump({"clips": [{"index": 0, "step": 1, "name": "suche", "file": "clips/01-suche.mp4"}]}, f)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def build(self, holds):
        rec = {"clips": [{"name": "suche", "splices": [], "holds": holds,
                          "events": [{"type": "click", "clip_t": 1.19, "src_t": 1.19}]}]}
        with open(os.path.join(self.dir, "cut", "record.json"), "w") as f:
            json.dump(rec, f)
        return gatefeed.build(self.dir, SPEC, {"steps": [{"name": "suche", "ok": True, "verified": True}]})

    def gate(self, events):
        p = subprocess.run([GATE, self.dir, "--events", events, "--json", "--out", os.path.join(self.dir, "qa")],
                           capture_output=True, text=True)
        self.assertIn(p.returncode, (0, 1), p.stdout + p.stderr)
        return [v for v in json.loads(p.stdout)["violations"] if v["check"] == "Q-51"]

    def test_spec_hold_list_in_the_event_log(self):
        ev = json.load(open(self.build([])))
        self.assertEqual([(h["kind"], h["seconds"]) for h in ev["clips"]["suche"]["spec_holds"]],
                         [("landing", 1.0), ("reading", 0.5), ("step", 1.0)])

    @unittest.skipUnless(os.path.isdir(FIX), NO_FIX)
    def test_missing_landing_hold_fails_and_others_still_checked(self):
        # landing hold missing; the reading pause is right; the step hold is 2.0 s where the spec asks for 1.0 s
        q51 = self.gate(self.build([{"kind": "reading", "seconds": 0.5, "clip_t": 1.3, "kept_seconds": 0.5},
                                    {"kind": "step", "seconds": 2.0, "clip_t": 1.0, "kept_seconds": 2.0}]))
        text = " | ".join(v["reason"] for v in q51)
        self.assertIn("landing hold", text)
        self.assertIn("missing", text)
        self.assertIn("spec asks for 1.00 s", text)
        self.assertEqual(len(q51), 2, text)

    @unittest.skipUnless(os.path.isdir(FIX), NO_FIX)
    def test_complete_holds_pass(self):
        q51 = self.gate(self.build([{"kind": "landing", "seconds": 1.0, "clip_t": 0.0, "kept_seconds": 1.0},
                                    {"kind": "reading", "seconds": 0.5, "clip_t": 1.3, "kept_seconds": 0.5},
                                    {"kind": "step", "seconds": 1.0, "clip_t": 2.0, "kept_seconds": 1.0}]))
        self.assertEqual(q51, [])


if __name__ == "__main__":
    unittest.main()
