"""Regression tests for the integration review (vc-v1-int-review.md): C3, Majors 1, 2, 4, 5, 6 on the gate side.

Every test runs the real gate CLI (bin/vc-gate) on real fixture clips, or the real check functions on real decoded
clips. Run: .venv/bin/python -m unittest discover -s gate/tests
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from test_gate import FIX, GATE, PAN, SUCHE, make_take, needs  # noqa: E402

LATE = os.path.join(FIX, "synthetic-q30-late-content-3s.mp4")   # unexplained hard jump at ~3.7 s, click at 0.68 s


def gate(root, *args, env=None):
    """Run the gate CLI. Returns (exit status, report.json dict or None, report.txt)."""
    out = os.path.join(root, "qa")
    p = subprocess.run([GATE, root, "--json", "--out", out] + list(args), capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})))
    rj = os.path.join(out, "report.json")
    res = json.load(open(rj)) if os.path.exists(rj) else None
    rt = os.path.join(out, "report.txt")
    txt = open(rt).read() if os.path.exists(rt) else p.stdout + p.stderr
    return p.returncode, res, txt


def fails(res, check):
    return [v for v in (res or {}).get("violations", []) if v["check"] == check]


class Base(unittest.TestCase):
    def tearDown(self):
        for d in getattr(self, "_dirs", []):
            shutil.rmtree(d, ignore_errors=True)

    def take(self, *a, **k):
        d = make_take(*a, **k)
        self._dirs = getattr(self, "_dirs", []) + [d]
        return d


class C3ErrorsAbort(Base):
    """C3: a hold the cut trimmed away (t None) is a Q-51 FAIL with a report, never a crash; any unexpected error or
    tool failure ends with the ABORT status (3) and a report.json, never with the FAIL status (1)."""

    @needs(FIX)
    def test_trimmed_hold_is_q51_not_a_crash(self):
        ev = {"clips": {"late": {"clicks": [{"t": 0.68}],
                                 "holds": [{"kind": "step", "t": None, "seconds": 1.0, "kept_seconds": 0.0}]}}}
        st, res, txt = gate(self.take([("late", LATE)], ev))
        self.assertIsNotNone(res, "no report.json: the gate crashed\n" + txt)
        self.assertEqual(st, 1, txt)
        self.assertTrue(any("removed by the cut" in v["reason"] for v in fails(res, "Q-51")), txt)
        self.assertTrue(fails(res, "Q-30"), txt)          # the jump is still judged

    def test_missing_ffmpeg_aborts(self):
        ev = {"clips": {"suche": {"clicks": [{"t": 1.19}]}}}
        st, res, txt = gate(self.take([("suche", SUCHE)], ev), env={"VC_FFMPEG": "/nonexistent/ffmpeg"})
        self.assertEqual(st, 3, txt)
        self.assertIsNotNone(res, "no report.json on a tool error")
        self.assertIn("abort", res)
        self.assertNotRegex(txt, r"\d+ violation")

    def test_worker_error_in_pool_aborts(self):
        ev = {"clips": {"a": {"clicks": [{"t": 1.19}]}, "b": {"clicks": [{"t": 1.19}]}}}
        st, res, txt = gate(self.take([("a", SUCHE), ("b", SUCHE)], ev), "--workers", "2",
                            env={"VC_FFMPEG": "/nonexistent/ffmpeg"})
        self.assertEqual(st, 3, txt)
        self.assertIsNotNone(res)
        self.assertEqual(sorted(a["clip"] for a in res["abort"]), ["a", "b"])

    def test_nonzero_tool_exit_aborts(self):
        ev = {"clips": {"suche": {"clicks": [{"t": 1.19}]}}}
        st, res, txt = gate(self.take([("suche", SUCHE)], ev), env={"VC_FFMPEG": "/bin/false"})
        self.assertEqual(st, 3, txt)
        self.assertIsNotNone(res)


class Major1MissingEventLog(Base):
    """Major 1 (Q-02, Q-10): without clips/events.json the event checks (Q-10, Q-11, Q-13, Q-44, Q-51, Q-62) cannot be
    measured; the gate aborts instead of passing. --no-events is the explicit pixel-only diagnostic."""

    def test_no_event_log_aborts(self):
        st, res, txt = gate(self.take([("suche", SUCHE)]))
        self.assertEqual(st, 3, txt)
        self.assertIn("events.json", txt)
        self.assertNotRegex(txt, r"\d+ violation")

    def test_cut_record_is_no_substitute_for_the_event_log(self):
        rec = {"clips": [{"name": "suche", "splices": [], "events": [{"type": "click", "clip_t": 1.19}],
                          "holds": []}]}
        st, res, txt = gate(self.take([("suche", SUCHE)], record=rec))
        self.assertEqual(st, 3, txt)

    @needs(FIX)
    def test_explicit_no_events_still_judges_pixels(self):
        st, res, txt = gate(self.take([("suche", SUCHE)]), "--no-events")
        self.assertIn(st, (0, 1), txt)
        self.assertIn("no event log", txt)


class Major2MissingHold(Base):
    """Major 2 (Q-51): holds are judged against the spec's hold list; a missing hold FAILs, and it does not switch off
    the length check of the other holds."""

    @needs(FIX)
    def test_missing_hold_detected(self):
        ev = {"clips": {"suche": {"clicks": [{"t": 1.19}],
                                  "spec_holds": [{"kind": "landing", "seconds": 1.0}, {"kind": "step", "seconds": 1.0}],
                                  "holds": [{"kind": "step", "t": 2.0, "seconds": 1.0}]}}}
        st, res, txt = gate(self.take([("suche", SUCHE)], ev))
        q51 = fails(res, "Q-51")
        self.assertTrue(any("landing" in v["reason"] and "missing" in v["reason"] for v in q51), txt)
        self.assertEqual(len(q51), 1, txt)

    @needs(FIX)
    def test_other_holds_still_length_checked(self):
        ev = {"clips": {"suche": {"clicks": [{"t": 1.19}],
                                  "spec_holds": [{"kind": "landing", "seconds": 1.0}, {"kind": "step", "seconds": 1.0}],
                                  "holds": [{"kind": "step", "t": 0.5, "seconds": 2.0}]}}}
        st, res, txt = gate(self.take([("suche", SUCHE)], ev))
        q51 = fails(res, "Q-51")
        self.assertTrue(any("missing" in v["reason"] for v in q51), txt)
        self.assertTrue(any("spec asks for 1.00 s" in v["reason"] for v in q51), txt)


class Major4CursorMissingAtBoundary(Base):
    """Major 4 (Q-43): the cursor exists from the first frame and never pops in; a cursor missing at a clip boundary
    is a FAIL (Gate: FAIL), not a WARN."""

    @needs(FIX)
    def test_cursor_missing_at_clip_start_fails(self):
        st, res, txt = gate(self.take([("suche", SUCHE), ("black", os.path.join(FIX, "q20-black-1p5s.mp4"))]),
                            "--no-events")
        q43 = fails(res, "Q-43")
        self.assertTrue(any("not found" in v["reason"] and "start of black" in v["reason"] for v in q43), txt)


def _edge_clip(dst, where, look):
    """suche.mp4 with 2 extra frames at its start or end: its own first/last frame negated (a screen the viewer cannot
    place) or plain white (a blank). Small real clips built with ffmpeg."""
    from vcgate import video
    fx = {"negate": "negate", "white": "drawbox=t=fill:c=white"}[look]
    if where == "start":
        g = f"[0:v]split[a][b];[a]trim=end_frame=2,{fx},setpts=PTS-STARTPTS[f];[f][b]concat=n=2:v=1[o]"
    else:
        g = f"[0:v]split[a][b];[a]trim=start_frame=90,{fx},setpts=PTS-STARTPTS[f];[b][f]concat=n=2:v=1[o]"
    subprocess.run([video.ffmpeg(), "-v", "error", "-y", "-i", SUCHE, "-filter_complex", g, "-map", "[o]", "-c:v",
                    "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", dst], check=True)
    return dst


@needs(FIX)
class Major5EdgeFlashes(Base):
    """Major 5 (Q-21): a 1-3 frame flash or blank that touches a clip's first or last frame is judged too (no screen on
    one side means it cannot be a plain blend); Q-20 only fires above 3 frames, so nothing else flags it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vcgate-edge-")
        cls.clips = {(w, k): _edge_clip(os.path.join(cls.tmp, f"{w}-{k}.mp4"), w, k)
                     for w, k in (("start", "negate"), ("start", "white"), ("end", "negate"))}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def q21(self, key):
        ev = {"clips": {"edge": {"clicks": [{"t": 1.19 + (2 / 30.0 if key[0] == "start" else 0.0)}]}}}
        st, res, txt = gate(self.take([("edge", self.clips[key])], ev))
        return fails(res, "Q-21"), txt

    def test_orphan_frames_at_clip_start(self):
        q, txt = self.q21(("start", "negate"))
        self.assertTrue(q and q[0]["t0"] == 0.0, txt)

    def test_blank_frames_at_clip_start(self):
        q, txt = self.q21(("start", "white"))
        self.assertTrue(q and "blank" in q[0]["reason"], txt)

    def test_orphan_frames_at_clip_end(self):
        q, txt = self.q21(("end", "negate"))
        self.assertTrue(q, txt)

    def test_clean_clip_has_no_edge_flash(self):
        st, res, txt = gate(self.take([("suche", SUCHE)], {"clips": {"suche": {"clicks": [{"t": 1.19}]}}}))
        self.assertEqual(fails(res, "Q-21"), [], txt)


def _pan_clip(dst, wobble):
    """A 4 s sine pan of 790 px down a still page (schritt.mp4's first frame stacked twice), every frame painted
    (a 1 px/frame drift keeps the ease tails from repeating a frame). `wobble` px are added on every odd frame in
    the middle of the pan: the steps then alternate +-wobble, which Q-31's jitter rule does not catch (< 5 px)."""
    from vcgate import video
    src = os.path.join(FIX, "clean-reference-take", "schritt.mp4")
    y = (f"if(lt(n,15),0,if(gt(n,105),790,350*(1-cos(PI*(n-15)/90))+(n-15)"
         f"+{wobble}*mod(n,2)*gt(sin(PI*(n-15)/90),0.5)))")
    g = (f"[0:v]trim=end_frame=1,setpts=PTS-STARTPTS,format=yuv444p,split[a][b];[a][b]vstack,"
         f"loop=loop=119:size=1:start=0,setpts=N/30/TB,crop=1910:986:0:'{y}'[o]")
    subprocess.run([video.ffmpeg(), "-v", "error", "-y", "-i", src, "-filter_complex", g, "-map", "[o]", "-frames:v",
                    "120", "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv444p",
                    dst], check=True)
    return dst


@needs(FIX)
class Major6NeighbourSteps(Base):
    """Major 6 (Q-34): neighbouring steps of a pan differ by at most 2 px (computed before, never enforced). The
    duplicate rule's WARN default (owner's OD-15 rule, revised 2026-09-25) is unchanged."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vcgate-pan-")
        cls.even = _pan_clip(os.path.join(cls.tmp, "even.mp4"), 0)
        cls.uneven = _pan_clip(os.path.join(cls.tmp, "uneven.mp4"), 1.5)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_uneven_pan_fails_q34(self):
        st, res, txt = gate(self.take([("pan", self.uneven)]), "--no-events")
        q34 = fails(res, "Q-34")
        self.assertTrue(q34 and "neighbouring steps" in q34[0]["reason"], txt)
        self.assertEqual(len(q34), 1, txt)                  # one episode, one violation (Q-06)

    def test_even_pan_passes(self):
        st, res, txt = gate(self.take([("pan", self.even)]), "--no-events")
        self.assertEqual(fails(res, "Q-34") + fails(res, "Q-31"), [], txt)

    def test_duplicate_default_unchanged(self):
        from vcgate.checks import motion
        self.assertFalse(motion.Q34_STRICT)
        st, res, txt = gate(self.take([("pan", PAN)]), "--no-events")
        self.assertEqual(fails(res, "Q-34"), [], txt)      # the Q-90 clean pan stays a PASS (dropped paints: WARN)


if __name__ == "__main__":
    unittest.main()
