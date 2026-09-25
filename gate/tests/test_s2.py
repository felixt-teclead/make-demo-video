"""S2 gate checks: Q-23 skeleton frames and Q-03 raw-versus-cut diagnosis.

Run: .venv/bin/python -m unittest test_s2   (cwd gate/tests; needs the spec fixtures, ffmpeg, and for the Q-03 end-to-end
test the cutter bin/vc-cut). VC_FIXTURES overrides the fixture path.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from test_gate import FIX, GATE, REPO, make_take, needs, run  # noqa: E402
from vcgate import layers  # noqa: E402
from vcgate.video import ffmpeg  # noqa: E402

REF = os.path.join(FIX, "clean-reference-take")


def q23(res):
    return [v for v in res["violations"] + res["warnings"] if v["check"] == "Q-23"]


class Q23Fixtures(unittest.TestCase):
    """Q-90 item 3: 11 skeleton frames FAIL; 1 skeleton frame WARN and the take still passes."""

    @needs(FIX)
    def test_eleven_frames_fail(self):
        root = make_take([("q23-skeleton-11f", os.path.join(FIX, "q23-skeleton-11f.mp4"))],
                         {"clips": {"q23-skeleton-11f": {"clicks": [{"t": 1.31, "label": "Einstellungen"}]}}})
        try:
            st, res, txt = run(root)
            v = q23(res)
            self.assertEqual(len(v), 1, txt)
            self.assertEqual(v[0]["severity"], "FAIL")
            self.assertEqual(v[0]["detail"]["frames"], 11)
            self.assertAlmostEqual(v[0]["t0"], 1.30, places=2)
            self.assertIn("11 skeleton frames", txt)
        finally:
            shutil.rmtree(root)

    @needs(FIX)
    def test_one_frame_warns(self):
        root = make_take([("q23-skeleton-1f-warn", os.path.join(FIX, "q23-skeleton-1f-warn.mp4"))],
                         {"clips": {"q23-skeleton-1f-warn": {"clicks": [{"t": 0.8, "label": "Prozesse"}]}}})
        try:
            st, res, txt = run(root)
            self.assertEqual(st, 0, txt)
            v = q23(res)
            self.assertEqual(len(v), 1, txt)
            self.assertEqual(v[0]["severity"], "WARN")
            self.assertEqual(v[0]["detail"]["frames"], 1)
            self.assertTrue(txt.rstrip().endswith("PASS"))
        finally:
            shutil.rmtree(root)

    @needs(FIX)
    def test_no_event_log_is_not_judged(self):
        root = make_take([("q23-skeleton-11f", os.path.join(FIX, "q23-skeleton-11f.mp4"))])
        try:
            st, res, txt = run(root, "--no-events")
            self.assertEqual(q23(res), [])
            self.assertIn("Q-23: not judged", txt)
        finally:
            shutil.rmtree(root)


    def test_missing_event_log_aborts(self):
        """Q-23 counts per logged click: with clips/events.json missing (no --no-events) the gate ABORTs (Q-02),
        it never reports Q-23 or a count."""
        root = make_take([("q23-skeleton-11f", os.path.join(FIX, "q23-skeleton-11f.mp4"))])
        try:
            st, res, txt = run(root)
            self.assertEqual(st, 3, txt)
            self.assertIsNone(res)
            self.assertIn("events.json", txt)
            self.assertNotIn("Q-23", txt)
            self.assertNotRegex(txt, r"\d+ violation")
        finally:
            shutil.rmtree(root)


@needs(FIX)
class Q23Synthetic(unittest.TestCase):
    """Invariant, not a defect list (Q-01): any detail-less stand-in fails, a plain cross-fade does not."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vcgate-q23-")
        a, b = os.path.join(cls.tmp, "a.png"), os.path.join(cls.tmp, "b.png")
        ff = ffmpeg()
        for src, n, out in ((os.path.join(REF, "prozesse.mp4"), 10, a), (os.path.join(REF, "suche.mp4"), 80, b)):
            subprocess.run([ff, "-v", "error", "-y", "-i", src, "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", out],
                           check=True)
        enc = ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]
        loop = ["-loop", "1", "-framerate", "30"]
        # old view 1 s, then 5 frames of a blurred new view (a stand-in without detail), then the new view
        cls.skel = os.path.join(cls.tmp, "skel5.mp4")
        subprocess.run([ff, "-v", "error", "-y"] + loop + ["-t", "1", "-i", a] + loop + ["-t", "0.16667", "-i", b]
                       + loop + ["-t", "2", "-i", b] + ["-filter_complex",
                       "[1:v]gblur=sigma=14[bb];[0:v][bb][2:v]concat=n=3:v=1"] + enc + [cls.skel], check=True)
        cls.fades = []
        for d in (0.2, 0.5):
            out = os.path.join(cls.tmp, f"fade{d}.mp4")
            subprocess.run([ff, "-v", "error", "-y"] + loop + ["-t", "1.5", "-i", a] + loop + ["-t", "3", "-i", b]
                           + ["-filter_complex", f"[0:v][1:v]xfade=transition=fade:duration={d}:offset=1.0"]
                           + enc + [out], check=True)
            cls.fades.append(out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, path):
        root = make_take([("c", path)], {"clips": {"c": {"clicks": [{"t": 0.97, "x": 1500, "y": 900}]}}})
        try:
            return run(root)
        finally:
            shutil.rmtree(root)

    def test_blurred_standin_fails(self):
        st, res, txt = self._run(self.skel)
        v = q23(res)
        self.assertEqual(len(v), 1, txt)
        self.assertEqual((v[0]["severity"], v[0]["detail"]["frames"]), ("FAIL", 5))

    def test_cross_fade_is_no_skeleton(self):
        for f in self.fades:
            st, res, txt = self._run(f)
            self.assertEqual(q23(res), [], f + "\n" + txt)


class Q03Diagnosis(unittest.TestCase):
    """The layer of each defect from made-up gate results (fast, no video)."""

    record = {"c": {"kept": [{"clip_start": 0.0, "clip_end": 1.0, "src_start": 10.0, "src_end": 11.0, "factor": 1.0},
                             {"clip_start": 1.0, "clip_end": 2.0, "src_start": 12.0, "src_end": 13.0, "factor": 1.0}]}}

    @staticmethod
    def v(check, t0, t1=None, clip="c"):
        return {"check": check, "severity": "FAIL", "clip": clip, "t0": t0, "t1": t1, "reason": check}

    def test_raw_abort_places_nothing(self):
        cut = {"violations": [{"check": "Q-20", "severity": "FAIL", "clip": "c", "t0": 1.0, "t1": 1.2, "reason": "x"}],
               "warnings": []}
        d = layers.diagnose(cut, {"abort": [{"clip": "raw", "reason": "decodes to 0 frames"}]}, self.record,
                            {"c": 30.0})
        self.assertEqual(d["items"], [])
        self.assertEqual(d["abort"][0]["clip"], "raw")

    def test_layers(self):
        cut = {"violations": [self.v("Q-31", 0.5, 0.8), self.v("Q-42", 1.0), self.v("Q-13", None, clip=None)],
               "warnings": []}
        raw = {"violations": [self.v("Q-31", 10.45, 10.9, "raw"), self.v("Q-20", 11.2, 11.8, "raw"),
                              self.v("Q-21", 12.9, 13.1, "raw"), self.v("Q-14", 0.0, 45.0, "raw")], "warnings": []}
        d = layers.diagnose(cut, raw, self.record, {"c": 30.0})
        got = {(i["check"], i["layer"]) for i in d["items"]}
        self.assertEqual(got, {("Q-31", "recording"), ("Q-42", "cutter"), ("Q-13", "structure"),
                               ("Q-20", "raw only"), ("Q-21", "raw only")})
        by = {i["check"]: i for i in d["items"]}
        self.assertIn("removed by the cutter", by["Q-20"]["where"])
        self.assertIn("not seen in the clips", by["Q-21"]["where"])
        self.assertEqual((by["Q-42"]["raw_t0"], by["Q-31"]["raw_t0"]), (12.0, 10.5))

    def test_frame_map_wins(self):
        m = layers.ClipMap({"frame_map": [300, 301, 360, 361], "kept": self.record["c"]["kept"]}, 30.0)
        self.assertEqual(m.raw_span(0.0, 2 / 30.0), (10.0, 12.0))
        self.assertEqual(m.kept_frames(10.1, 11.9), 0)


class Q03EndToEnd(unittest.TestCase):
    """Q-03 Verify: a real white-out in the raw that the cutter removed shows in raw mode only."""

    @needs(FIX)
    def test_whiteout_removed_by_cutter(self):
        tmp = tempfile.mkdtemp(prefix="vcgate-q03-")
        try:
            run_dir = os.path.join(tmp, "run")
            os.makedirs(run_dir)
            shutil.copy(os.path.join(FIX, "q20-whiteout-at-navigation.mp4"), os.path.join(run_dir, "raw.mp4"))
            t0 = 1727200000.0
            ev = [{"type": "start", "t": t0, "video_t": 0.0, "frame": 0},
                  {"type": "mark", "name": "consent", "step": 1, "t": t0, "video_t": 0.0, "frame": 0},
                  {"type": "click", "t": t0 + 0.27, "video_t": 0.27, "frame": 8, "navigates": True,
                   "label": "Zugriff erlauben"}]
            with open(os.path.join(run_dir, "events.jsonl"), "w") as fh:
                fh.write("".join(json.dumps(e) + "\n" for e in ev))
            man = {"run_id": "q03", "raw": "raw.mp4", "t0": t0, "fps": 30, "width": 1910, "height": 986,
                   "duration": 4.0, "frames": 120, "marks": [ev[1]], "holds": [], "events": ev,
                   "segments": [{"index": 0, "step": 1, "name": "consent", "start_t": 0.0, "end_t": 4.0,
                                 "start_frame": 0, "end_frame": 120, "holds": []}]}
            with open(os.path.join(run_dir, "manifest.json"), "w") as fh:
                json.dump(man, fh)
            env = dict(os.environ, PATH=os.path.expanduser("~/.local/bin") + os.pathsep + os.environ.get("PATH", ""))
            subprocess.run([os.path.join(REPO, "bin", "vc-cut"), run_dir], check=True, capture_output=True, env=env)
            # the clip-time event log, built the way the loop builds it (a cut take without it ABORTs, Q-02)
            sys.path.insert(0, os.path.join(REPO, "loop"))
            from vcloop import gatefeed
            gatefeed.build(run_dir, {"steps": [{"name": "consent", "hold": 1.0}], "length": [1, 10]},
                           {"steps": [{"name": "consent", "ok": True, "verified": True}]})

            st, res, txt = run(run_dir, "--layers")
            self.assertIn(st, (0, 1), txt)
            self.assertFalse([v for v in res["violations"] if v["check"] == "Q-20"], "white-out reached a clip")
            q20 = [i for i in res["layers"]["items"] if i["check"] == "Q-20"]
            self.assertTrue(q20, txt)
            self.assertTrue(all(i["layer"] == "raw only" and "removed by the cutter" in i["where"] for i in q20), txt)
            self.assertIn("WHITE", q20[0]["reason"])
            self.assertIn("RAW ONLY  FAIL Q-20", txt)
            self.assertIn("not counted toward the gate budget", txt)
            # the raw run never changes the verdict
            st2, res2, _ = run(run_dir)
            self.assertEqual((st2, len(res2["violations"])), (st, len(res["violations"])))
            p = subprocess.run([GATE, run_dir, "--raw", "--layers"], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            # --layers keeps the ABORT semantics: a cut take without its event log aborts, no raw run, no count
            ev_path = os.path.join(run_dir, "clips", "events.json")
            os.rename(ev_path, ev_path + ".off")
            st3, res3, txt3 = run(run_dir, "--layers")
            self.assertEqual(st3, 3, txt3)
            self.assertNotIn("layers (Q-03", txt3)
            self.assertNotRegex(txt3, r"\d+ violation")
            os.rename(ev_path + ".off", ev_path)
            # an unmeasurable raw capture is noted, places no defect in a layer, and never changes the verdict
            with open(os.path.join(run_dir, "raw.mp4"), "wb") as fh:
                fh.write(os.urandom(4096))
            st4, res4, txt4 = run(run_dir, "--layers")
            self.assertEqual(st4, st, txt4)
            self.assertNotIn("Traceback", txt4)
            self.assertEqual(res4["layers"]["items"], [], txt4)
            self.assertIn("raw capture could not be measured", txt4)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
