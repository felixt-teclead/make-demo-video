"""Contract tests of the gate (Q-02, Q-07, Q-10, Q-11, Q-13, Q-14, Q-37, Q-51, Q-62, Q-95, determinism).

Run: .venv/bin/python -m unittest discover -s gate/tests  (needs the spec fixtures; VC_FIXTURES overrides the path)
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
GATE = os.path.join(REPO, "bin", "vc-gate")
FIX = os.environ.get("VC_FIXTURES") or os.path.abspath(os.path.join(REPO, "..", "vc-v1-spec", "acceptance", "fixtures"))
SUCHE = os.path.join(FIX, "clean-reference-take", "suche.mp4")          # 3.07 s, click at 1.19
PAN = os.path.join(FIX, "q31-clean-pan-2036px-PASS.mp4")                # 9.0 s, pan ~0.18-8.52


def needs(*paths):
    """Skip unless the test data exists: the Q-90 fixture clips and the recorded parity/tooltip clips are screen
    recordings of a private app and are not in the public repo (set VC_FIXTURES / add gate/tests/data to run)."""
    miss = [os.path.basename(p.rstrip("/")) or p for p in paths if not os.path.exists(p)]
    return unittest.skipIf(bool(miss), f"private test data not found ({', '.join(miss)}): recordings of a private "
                                       "app, not in the public repo; set VC_FIXTURES or add gate/tests/data to run")


def make_take(clips, events=None, record=None, extra=None):
    root = tempfile.mkdtemp(prefix="vcgate-test-")
    os.makedirs(os.path.join(root, "clips"))
    idx = []
    for i, (name, src) in enumerate(clips):
        dst = os.path.join(root, "clips", f"{i + 1:02d}-{name}.mp4")
        os.symlink(src, dst)
        idx.append({"index": i, "step": i + 1, "name": name, "file": os.path.relpath(dst, root)})
    with open(os.path.join(root, "clips", "index.json"), "w") as fh:
        json.dump({"clips": idx, "full": "full.mp4"}, fh)
    if events is not None:
        with open(os.path.join(root, "clips", "events.json"), "w") as fh:
            json.dump(events, fh)
    if record is not None:
        os.makedirs(os.path.join(root, "cut"))
        with open(os.path.join(root, "cut", "record.json"), "w") as fh:
            json.dump(record, fh)
    for rel, src in (extra or {}).items():
        os.symlink(src, os.path.join(root, rel))
    return root


def run(root, *args):
    p = subprocess.run([GATE, root, "--json", "--out", os.path.join(root, "qa")] + list(args),
                       capture_output=True, text=True)
    res = json.loads(p.stdout) if p.returncode in (0, 1) else None
    txt = open(os.path.join(root, "qa", "report.txt")).read() if os.path.exists(os.path.join(root, "qa", "report.txt")) else p.stdout
    return p.returncode, res, txt


def checks(res):
    return sorted({v["check"] for v in res["violations"]})


SUCHE_EV = {"clicks": [{"t": 1.19}]}


class GateContract(unittest.TestCase):
    def tearDown(self):
        for d in getattr(self, "_dirs", []):
            shutil.rmtree(d, ignore_errors=True)

    def take(self, *a, **k):
        d = make_take(*a, **k)
        self._dirs = getattr(self, "_dirs", []) + [d]
        return d

    @needs(FIX)
    def test_q10_extra_mp4_ignored(self):
        ev = {"steps": ["suche"], "clips": {"suche": SUCHE_EV}}
        base = self.take([("suche", SUCHE)], ev)
        st1, r1, _ = run(base)
        extra = self.take([("suche", SUCHE)], ev, extra={"full.mp4": SUCHE, "clips/stray-copy.mp4": PAN})
        st2, r2, txt = run(extra)
        self.assertEqual(st1, st2)
        self.assertEqual(r1["violations"], r2["violations"])
        self.assertEqual(sorted(r2["ignored"]), ["clips/stray-copy.mp4", "full.mp4"])
        self.assertIn("ignored (not in the clip index): full.mp4", txt)

    @needs(FIX)
    def test_q10_missing_step_named(self):
        st, res, txt = run(self.take([("suche", SUCHE)], {"steps": ["prozesse", "suche"], "clips": {"suche": SUCHE_EV}}))
        self.assertEqual(st, 1)
        self.assertIn("Q-10", checks(res))
        self.assertIn("missing steps: prozesse", txt)

    @needs(FIX)
    def test_q11_not_verified_and_dry_run(self):
        ev = {"dry_run": True, "clips": {"suche": dict(SUCHE_EV, result={"verified": False, "error": "text not found"})}}
        st, res, txt = run(self.take([("suche", SUCHE)], ev))
        q11 = [v for v in res["violations"] if v["check"] == "Q-11"]
        self.assertEqual(len(q11), 2)
        self.assertIn("text not found", txt)

    @needs(FIX)
    def test_q13_length(self):
        ev = {"length": {"min": 30, "max": 60}, "clips": {"suche": SUCHE_EV}}
        st, res, _ = run(self.take([("suche", SUCHE)], ev))
        self.assertIn("Q-13", checks(res))
        ev = {"length": {"min": 2, "max": 2.5}, "clips": {"suche": SUCHE_EV}}   # 3.07 s <= 2.5 + 1 s slack
        st, res, _ = run(self.take([("suche", SUCHE)], ev))
        self.assertNotIn("Q-13", checks(res))

    @needs(FIX)
    def test_q14_action_outside_clip(self):
        ev = {"clips": {"suche": {"clicks": [{"t": 1.19}, {"t": 7.5, "label": "Weiter"}]}}}
        st, res, txt = run(self.take([("suche", SUCHE)], ev))
        self.assertIn("Q-14", checks(res))
        self.assertIn("7.50 s lies outside the clip", txt)

    @needs(FIX)
    def test_q51_holds(self):
        ev = {"clips": {"suche": dict(SUCHE_EV, holds=[{"kind": "reading", "t": 1.5, "seconds": 1.2,
                                                         "spec_seconds": 1.6}])}}
        st, res, _ = run(self.take([("suche", SUCHE)], ev))
        self.assertIn("Q-51", checks(res))                      # 1.2 s vs 1.6 s: off by more than 0.25 s
        ev = {"clips": {"suche": dict(SUCHE_EV, holds=[{"kind": "step", "t": 2.0, "seconds": 1.0,
                                                         "spec_seconds": 1.0}])}}
        st, res, _ = run(self.take([("suche", SUCHE)], ev))
        self.assertNotIn("Q-51", checks(res))                   # ends at 3.0 s inside the 3.07 s clip
        rec = {"clips": [{"name": "suche", "splices": [2.5]}]}
        st, res, txt = run(self.take([("suche", SUCHE)], ev, rec))
        self.assertIn("Q-51", checks(res))                      # a splice inside the hold trims it
        self.assertIn("trimmed", txt)
        ev = {"clips": {"suche": dict(SUCHE_EV, holds=[{"kind": "step", "t": 2.0, "seconds": 2.0}])}}
        st, res, txt = run(self.take([("suche", SUCHE)], ev))
        self.assertIn("cut short", txt)

    @needs(FIX)
    def test_q62_final_hold(self):
        ev = {"final_hold": True, "clips": {"suche": SUCHE_EV}}
        st, res, txt = run(self.take([("suche", SUCHE)], ev))
        self.assertIn("Q-62", checks(res))                      # no final hold on the last clip
        ev = {"clips": {"suche": dict(SUCHE_EV, holds=[{"kind": "final", "t": 1.5, "seconds": 12.0}])}}
        st, res, txt = run(self.take([("suche", SUCHE)], ev))
        self.assertIn("Q-62", checks(res))                      # a 3 s clip cannot hold 12 s

    @needs(FIX)
    def test_q37_still_beat_after_pan(self):
        ev = {"clips": {"pan": {"clicks": [{"t": 8.75}]}}}
        st, res, txt = run(self.take([("pan", PAN)], ev))
        self.assertIn("Q-37", checks(res))

    def test_q02_abort_contract(self):
        st, res, txt = run(self.take([("zero", os.path.join(FIX, "synthetic-q02-zero-frames.mp4")),
                                      ("suche", SUCHE)]), "--no-events")   # no event log alone aborts too
        self.assertEqual(st, 3)
        self.assertIn("zero", txt)
        self.assertNotRegex(txt, re.compile(r"\d+ violation", re.I))

    @needs(FIX)
    def test_q07_report_lines(self):
        st, res, txt = run(self.take([("black", os.path.join(FIX, "q20-black-1p5s.mp4"))]), "--no-events")
        lines = txt.strip().splitlines()
        self.assertRegex(lines[-1], r"^FAIL: \d+ violations?$")
        fail = [l for l in lines if l.strip().startswith("FAIL Q-20")][0]
        self.assertIn("black", fail)
        self.assertRegex(fail, r"\d+\.\d\d-\d+\.\d\d s: ")
        self.assertTrue(lines[-2].startswith("totals:"))

    @needs(FIX)
    def test_deterministic(self):
        root = self.take([("suche", SUCHE)], {"clips": {"suche": SUCHE_EV}})
        _, r1, _ = run(root)
        _, r2, _ = run(root)
        for r in (r1, r2):
            r.pop("elapsed")
            for c in r["clips"]:
                c.pop("timing")
        self.assertEqual(r1, r2)

    def test_q95_no_model_no_network(self):
        src = os.path.join(REPO, "gate", "vcgate")
        bad = re.compile(r"^\s*(import|from)\s+(requests|urllib|http|socket|anthropic|openai|httpx)\b", re.M)
        for dirpath, _, files in os.walk(src):
            for f in files:
                if f.endswith(".py"):
                    self.assertIsNone(bad.search(open(os.path.join(dirpath, f)).read()), f)

    @needs(FIX)
    def test_raw_mode(self):
        run_dir = tempfile.mkdtemp(prefix="vcgate-raw-")
        self._dirs = getattr(self, "_dirs", []) + [run_dir]
        os.symlink(os.path.join(FIX, "q20-black-1p5s.mp4"), os.path.join(run_dir, "raw.mp4"))
        with open(os.path.join(run_dir, "events.jsonl"), "w") as fh:
            fh.write(json.dumps({"type": "mark", "name": "step-1", "step": 1, "video_t": 0.0}) + "\n")
        p = subprocess.run([GATE, run_dir, "--raw", "--json"], capture_output=True, text=True)
        res = json.loads(p.stdout)
        self.assertEqual(res["mode"], "raw")
        self.assertIn("Q-20", checks(res))
        self.assertFalse(os.path.exists(os.path.join(run_dir, "qa")))


if __name__ == "__main__":
    unittest.main()
