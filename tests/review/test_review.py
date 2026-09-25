"""Viewer review (loop/vcloop/review.py): sampling, semantics, ledger candidates, and the replay on the real second-try
takes (owner decision 2026-09-25).

Suites:
- Sampling (no media): the event-driven sample plan on the real cut records of c1 t1, c2 t1, c3 t2 (data/).
- Semantics (no media): judge() and the ledger candidates.
- Recorded replay (no model): the stored answers of the live review (data/<case>-answer.json) under judge().
- Media replay (ffmpeg + the real runs): sheets are built; the frames sampled in c2's tooltip window show the tooltip.
- LIVE replay (VC_LIVE_REVIEW=1, a real `claude -p` call, about $0.8 and 45 s per case): the review itself must flag
  c2's tooltip (2.87-5.17 s) and cover hole, c3 t2's step-4 card under the lane-header column, and no blocker on c1.
  VC_LIVE_REVIEW_MODEL overrides the model (default: the CLI default = the orchestrating model);
  VC_LIVE_REVIEW_SAVE=1 stores the answers as the recorded fixtures.
The real runs: $VC_REPLAY_RUNS, else ../vc-v1-int/runs next to the repo.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "loop"))
from vcloop import review as RV   # noqa: E402

DATA = os.path.join(HERE, "data")
RUNS = os.environ.get("VC_REPLAY_RUNS") or os.path.join(os.path.dirname(REPO), "vc-v1-int", "runs")
CASES = {"c1": ("c1-four-angles-20260925-064809-t1", "specs/golden/c1-four-angles.toml"),
         "c2": ("c2-find-and-read-20260925-064810-t1", "specs/golden/c2-find-and-read.toml"),
         "c3": ("c3-correction-loop-20260925-064809-t2", "specs/golden/c3-correction-loop.toml")}
TOOLTIP = (2.87, 5.17)                       # c2: native "Prozesse" tooltip mid-card (v1-parity.md S2)
C2_COVERS = [(2.43, 2.87), (7.87, 8.37)]     # c2: cover windows with the hole (full-video seconds)
C3_PAN_STEP = (10.77, 18.83)                 # c3 t2: step "pan"; its end shows step 4 under the lane headers


def rec(case):
    with open(os.path.join(DATA, f"{case}-record.json")) as f:
        return json.load(f)


def overlaps(f, a, b, slack=0.1):
    t = float(f.get("t") if f.get("t") is not None else -1)
    te = float(f.get("t_end") if f.get("t_end") not in (None, "") else t)
    return t <= b + slack and te >= a - slack


def case_expectations(test, case, answer):
    """The owner's acceptance for one case, on a reviewer answer."""
    j = RV.judge(answer, "PASS")
    allf = j["blockers"] + j["minors"]
    if case == "c1":
        test.assertEqual(j["blockers"], [], RV.summary(j))
        test.assertTrue(j["clean"])
    elif case == "c2":
        tip = [f for f in j["blockers"] if f.get("category") == "stray_ui" and overlaps(f, *TOOLTIP)]
        test.assertTrue(tip, f"tooltip not flagged as a blocker: {RV.summary(j)}")
        test.assertIn("prozesse", " ".join(f.get("what", "") for f in tip).lower())
        hole = [f for f in allf if f.get("category") == "flicker_hole" and any(overlaps(f, a, b) for a, b in C2_COVERS)]
        test.assertTrue(hole, "cover hole not flagged")
        test.assertFalse(j["clean"])
    elif case == "c3":
        hid = [f for f in j["blockers"] if f.get("category") in ("hidden_content", "expected_missing")
               and (f.get("step") == "pan" or overlaps(f, *C3_PAN_STEP))]
        test.assertTrue(hid, f"content behind the lane header not flagged: {RV.summary(j)}")
        text = " ".join(f.get("what", "") for f in hid).lower()
        test.assertTrue("header" in text or "korrekt" in text, text)
        test.assertFalse(j["clean"])
    return j


class Sampling(unittest.TestCase):
    def test_click_offsets_covers_pans_holds_first_last(self):
        s = RV.sample_times(rec("c2"))
        ts = [x["t"] for x in s]
        near = lambda t: any(abs(t - u) <= 0.02 for u in ts)   # noqa: E731
        for t in (2.3, 2.5, 2.8, 3.3):                                         # click "Prozesse" +0/.2/.5/1
            self.assertTrue(near(t), t)
        for t in (2.433, 2.65, 2.867):                                         # cover start / mid / end
            self.assertTrue(near(t), t)
        a, b = 10.8 + 0.0667, 10.8 + 8.5667                                    # the pan in step 4
        for fr in (0, .25, .5, .75, 1):
            self.assertTrue(near(a + fr * (b - a)), fr)
        self.assertTrue(near(b + 0.5))
        self.assertTrue(near(31.1667 + 1.8667) and near(31.1667 + 13.8667))   # final hold start / end
        self.assertEqual(ts[0], 0.0)
        self.assertAlmostEqual(ts[-1], 45.1 - 1 / 30, places=2)
        self.assertLessEqual(max(b - a for a, b in zip(ts, ts[1:])), 1.0 + 1e-6)   # never a gap above 1 s

    def test_one_sample_per_frame_and_reasons_merged(self):
        s = RV.sample_times(rec("c2"))
        frames = [x["frame"] for x in s]
        self.assertEqual(frames, sorted(set(frames)))
        self.assertTrue(any(";" in x["why"] for x in s))

    def test_tooltip_window_is_sampled_densely(self):
        s = RV.sample_times(rec("c2"))
        inside = [x for x in s if TOOLTIP[0] <= x["t"] <= TOOLTIP[1]]
        self.assertGreaterEqual(len(inside), 4)

    def test_pan_end_of_c3_is_sampled(self):
        s = RV.sample_times(rec("c3"))
        whys = {x["why"] for x in s if C3_PAN_STEP[0] <= x["t"] <= C3_PAN_STEP[1]}
        self.assertTrue(any("pan 100%" in w for w in whys) and any("step end" in w for w in whys), whys)

    def test_every_click_has_a_zoom_point(self):
        for case in CASES:
            r = rec(case)
            clicks = sum(1 for c in r["clips"] for e in c["events"] if e.get("type") == "click")
            zooms = {(x["step"], round(x["zoom"][0]), round(x["zoom"][1])) for x in RV.sample_times(r) if x["zoom"]}
            self.assertGreaterEqual(len(zooms), clicks - 1, case)   # two clicks on the same point share a sheet

    def test_label_strip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "l.ppm")
            RV._label_ppm(p, "#3 T=2.30 click +0 Prozesse Ü…", 960)
            with open(p, "rb") as f:
                data = f.read()
            self.assertTrue(data.startswith(b"P6\n960 34\n255\n"))
            self.assertEqual(len(data), len(b"P6\n960 34\n255\n") + 960 * 34 * 3)


class Semantics(unittest.TestCase):
    CLEAN = {"steps": [{"step": "a", "expected_visible": True}], "findings": []}
    MINOR = {"steps": [], "findings": [{"t": 1.0, "region": "top left", "severity": "minor", "what": "x"}]}
    BLOCK = {"steps": [], "findings": [{"t": 3.0, "t_end": 5.0, "step": "a", "region": "card", "severity": "blocker",
                                        "category": "stray_ui", "what": "tooltip"}]}

    def test_never_passes_a_gate_fail(self):
        for v in ("FAIL", "ABORT", "ERROR 2"):
            j = RV.judge(self.CLEAN, v)
            self.assertEqual((j["verdict"], j["clean"]), ("FAIL", False))

    def test_blocker_fails_minor_passes(self):
        self.assertEqual(RV.judge(self.BLOCK, "PASS")["verdict"], "FAIL")
        j = RV.judge(self.MINOR, "PASS")
        self.assertEqual((j["verdict"], len(j["minors"])), ("PASS", 1))
        self.assertEqual(RV.judge(self.CLEAN, "PASS")["verdict"], "PASS")

    def test_missing_or_invalid_answer_fails(self):
        self.assertEqual(RV.judge(None, "PASS")["verdict"], "FAIL")
        self.assertEqual(RV.judge({"error": "no JSON"}, "PASS")["verdict"], "FAIL")
        bad = {"findings": [{"what": "no time, no region", "severity": "minor"}]}
        self.assertEqual(RV.judge(bad, "PASS")["verdict"], "FAIL")
        bad = {"findings": [{"t": 1, "region": "x", "severity": "meh", "what": "?"}]}
        self.assertEqual(RV.judge(bad, "PASS")["verdict"], "FAIL")

    def test_expected_state_not_visible_is_a_blocker(self):
        j = RV.judge({"steps": [{"step": "pan", "expected_visible": False, "note": "Rücksprung off view"}],
                      "findings": []}, "PASS")
        self.assertEqual(j["verdict"], "FAIL")
        self.assertIn("pan: Rücksprung off view", RV.summary(j))

    def test_old_frame_check_answer_still_read(self):
        j = RV.judge({"clean": False, "issues": [{"step": "pan", "what": "cut at the left"}]}, "PASS")
        self.assertEqual(j["verdict"], "FAIL")

    def test_summary_starts_with_the_step(self):
        self.assertTrue(RV.summary(RV.judge(self.BLOCK, "PASS")).startswith("a: tooltip (3.00-5.00s, card)"))

    def test_ledger_candidate_per_blocker(self):
        j = RV.judge({"findings": self.BLOCK["findings"] + [
            {"t": 16.3, "step": "pan", "region": "left", "severity": "blocker", "category": "hidden_content",
             "what": "card under the lane header"}] + self.MINOR["findings"]}, "PASS")
        md = RV.ledger_candidates(j, case="c3", run_id="c3-t2", date="2026-09-25", review_dir="/r/review")
        self.assertEqual(md.count("### FX-??"), 2)
        for field in ("- symptom:", "- root cause:", "- change:", "- evidence:", "- scope guess:", "- owner decision:"):
            self.assertEqual(md.count(field), 2, field)
        self.assertIn("scope guess: COMMON", md)       # stray UI: engine-wide
        self.assertIn("scope guess: SPECIFIC", md)     # hidden content: case camera

    def test_house_style_calibration_in_prompt(self):
        for pv in (RV.PRIVACY_OFF, RV.PRIVACY_ON.format(shown="x", account="y")):
            p = RV.checklist(pv)
            self.assertNotIn("{house_style}", p)
            self.assertNotIn("{privacy}", p)
            for m in RV.HOUSE_STYLE_MARKERS:
                self.assertIn(m, p)
            self.assertRegex(p, r"(?s)Fast typing.*intended.*final hold.*about 12 s.*intended")

    def test_house_style_findings_do_not_count(self):
        typing = {"t": 3.0, "step": "search", "region": "search box", "category": "pace", "severity": "blocker",
                  "what": "the typed query appears instantly, too fast to follow the typing"}
        hold = {"t": 30.0, "t_end": 42.0, "step": "open", "region": "whole frame", "category": "pace",
                "severity": "minor", "what": "the final hold of about 12 s is long and static"}
        tooltip = {"t": 31.0, "step": "open", "region": "top left", "category": "stray_ui", "severity": "blocker",
                   "what": "a tooltip stays during the final hold"}
        j = RV.judge({"steps": [], "findings": [typing, hold]}, "PASS")
        self.assertTrue(j["clean"])
        self.assertEqual((len(j["minors"]), len(j["house_style"])), (0, 2))
        j = RV.judge({"steps": [], "findings": [hold, tooltip]}, "PASS")
        self.assertEqual([f["category"] for f in j["blockers"]], ["stray_ui"])

    def test_parse_answer_tolerates_fences(self):
        self.assertEqual(RV.parse_answer('```json\n{"findings": []}\n```'), {"findings": []})
        self.assertIn("error", RV.parse_answer("no json here"))


class RecordedReplay(unittest.TestCase):
    """The live answers stored as fixtures, judged again (catches semantics drift without a model call)."""

    def _answer(self, case):
        p = os.path.join(DATA, f"{case}-answer.json")
        if not os.path.exists(p):
            self.skipTest(f"no recorded answer {p}")
        with open(p) as f:
            return json.load(f)

    def test_c1_hit_has_no_blocker(self):
        case_expectations(self, "c1", self._answer("c1"))

    def test_c2_tooltip_and_cover_hole(self):
        case_expectations(self, "c2", self._answer("c2"))

    def test_c3_t2_content_behind_lane_header(self):
        case_expectations(self, "c3", self._answer("c3"))


def _runs_ok(case):
    return os.path.exists(os.path.join(RUNS, CASES[case][0], "full.mp4")) and RV.ffmpeg()


class MediaReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not _runs_ok("c2"):
            raise unittest.SkipTest(f"real runs not found under {RUNS} (VC_REPLAY_RUNS) or no ffmpeg")
        cls.tmp = tempfile.mkdtemp(prefix="vc-review-")
        cls.req = RV.build_request(os.path.join(RUNS, CASES["c2"][0]), [], model="orchestrator",
                                   out_dir=os.path.join(cls.tmp, "c2"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_sheets_written(self):
        kinds = [s["kind"] for s in self.req["sheets"]]
        self.assertIn("timeline", kinds)
        self.assertGreaterEqual(kinds.count("zoom"), 7)
        for s in self.req["sheets"]:
            self.assertTrue(os.path.getsize(s["path"]) > 10000, s["path"])
        self.assertLess(self.req["sample_s"], 60)

    def test_sampled_frames_show_the_tooltip(self):
        """A white-bordered box at (955..1055, 545..595) in the frames sampled inside 2.87-5.17 s, none before."""
        fr_dir = os.path.join(self.tmp, "c2", "frames")
        s = RV.sample_times(json.load(open(os.path.join(RUNS, CASES["c2"][0], "cut", "record.json"))))
        hits, before = 0, 0
        for x in s:
            if x["t"] > 6.0:
                break
            raw = subprocess.run([RV.ffmpeg(), "-v", "error", "-i", os.path.join(fr_dir, f"f{x['frame']:05d}.jpg"),
                                  "-vf", "crop=100:50:955:545,format=gray", "-f", "rawvideo", "-"],
                                 capture_output=True, check=True).stdout
            bright = sum(1 for b in raw if b > 235) > 100
            if TOOLTIP[0] <= x["t"] <= TOOLTIP[1]:
                hits += bright
            elif x["t"] < TOOLTIP[0] - 0.05:
                before += bright
        self.assertGreaterEqual(hits, 4)
        self.assertEqual(before, 0)


@unittest.skipUnless(os.environ.get("VC_LIVE_REVIEW") == "1", "live model test: set VC_LIVE_REVIEW=1 (~$0.8, 45 s/case)")
class LiveReplay(unittest.TestCase):
    """LIVE: a real review of the real second-try takes."""
    results = {}

    def _run(self, case):
        run, spec = CASES[case]
        if not _runs_ok(case):
            self.skipTest(f"run {run} not found under {RUNS}")
        from vcloop import spec as S
        sp = S.load(os.path.join(REPO, spec))
        res, _ = S.resolve(sp, {"unique": "-"})
        d = tempfile.mkdtemp(prefix=f"vc-review-{case}-")
        self.addCleanup(shutil.rmtree, d, True)
        req = RV.build_request(os.path.join(RUNS, run), RV.steps_from_spec(res), model="orchestrator", out_dir=d)
        ans = RV.run_headless(req, os.environ.get("VC_LIVE_REVIEW_MODEL"))
        print(f"\n[live review {case}] model {ans.get('model')} tokens {ans.get('tokens')} {ans.get('seconds')} s "
              f"${ans.get('agent_cost_usd')} (+ sampling {req['sample_s']} s): {RV.summary(RV.judge(ans, 'PASS'))}")
        if os.environ.get("VC_LIVE_REVIEW_SAVE") == "1":
            with open(os.path.join(DATA, f"{case}-answer.json"), "w") as f:
                json.dump(ans, f, indent=1, ensure_ascii=False)
        return ans

    def test_c1_hit_no_blocker(self):
        case_expectations(self, "c1", self._run("c1"))

    def test_c2_flags_tooltip_and_hole(self):
        case_expectations(self, "c2", self._run("c2"))

    def test_c3_t2_flags_content_behind_header(self):
        case_expectations(self, "c3", self._run("c3"))


if __name__ == "__main__":
    unittest.main()
