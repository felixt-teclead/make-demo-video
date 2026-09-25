"""Regression tests for the QA definition change approved by the owner 2026-09-25 (v1-parity.md section 3).

Three new-gate false positives and one understatement, proven frame by frame on the delivered takes (runs/c1-four-
angles-20260925-045423-t3, runs/c2-find-and-read-20260925-051500-t1, runs/c3-correction-loop-20260925-052021-t1).
Every test uses REAL frames: native-resolution crops (gate/tests/data/parity/pointer-crops.npz) and two short clips
cut from the delivered clips (gate/tests/data/parity/*.mp4, frames unchanged apart from a crop and a re-encode):

- pointer-crops.npz (the frames of v1-parity-evidence/frames c1new-swim-*.png, c3-cursor-1498.png, c3-arrow-1082.png,
  c3-horiz-full.png at native resolution):
  c1_swim_1p5 / c1_swim_2p47: c1 04-swimlanes 1.5 s / 2.47 s, the demo cursor parked on the orange active "Swimlanes"
  button (tip 1608,169); c1_dialog_0: c1 05-step-dialog frame 0, the same spot (Q-43 boundary);
  c3_cursor: c3 03-horizontal 1.5 s, the demo cursor on the Horizontal button (1498,177);
  c3_arrows_a/b/c: c3 05-schritt-5 0.5 s, grey and orange connector arrowheads ((974,491), (1334,491), (798,491),
  (1508,836), (1870,835), (972,640)); c3_arrows_h: c3 03-horizontal 1.5 s ((546|722|1082|1442, 295));
  coral: Q-90 fixture q40-second-pointer 3.0 s, the coral second pointer with the orange edge glow (960,468).
  Each crop carries its origin (<name>_origin, x0 y0) in the full frame.
- c3-horizontal-pan-1364px.mp4: c3 04-pan frames 0-147, rows 120-679 (evidence c3pan-shift.txt: an eased 1364 px
  right pan, 1 -> 24 -> 2 px per painted frame, a repeated frame about every third frame).
- c2-sticky-header-ride-56px.mp4: c2 04-schritt-4 frames 0-99, rows 0-599 (evidence c2-header-ride.png: the lane
  headers ride up ~56 px with the diagram over frames 4-16, 0.13-0.53 s, then pin).

Both directions are tested: the false positive is gone, and the real defect still fails (the Q-90 second pointer, a
second pointer sprite on the same real pages, the 56 px ride-along, the 59 px step pan).
Run: .venv/bin/python -m unittest discover -s gate/tests
"""
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from test_gate import FIX, needs  # noqa: E402
from vcgate import video  # noqa: E402
from vcgate.checks import cursor, motion  # noqa: E402
from vcgate.model import ClipCtx  # noqa: E402

DATA = os.path.join(HERE, "data", "parity")
CROPS = os.path.join(DATA, "pointer-crops.npz")
C3PAN = os.path.join(DATA, "c3-horizontal-pan-1364px.mp4")
C2RIDE = os.path.join(DATA, "c2-sticky-header-ride-56px.mp4")
C3EASED = os.path.join(DATA, "c3-eased-pan-804px.mp4")
C3CUT = os.path.join(DATA, "c3-pan-ease-in-cut-12px.mp4")


def crops():
    d = np.load(CROPS)
    return {k: d[k] for k in d.files}


def tips(img, origin):
    return [(p["x"] + int(origin[0]), p["y"] + int(origin[1]), p["demo"]) for p in cursor.analyse_frame(img)]


def paste(img, patch, x, y):
    out = img.copy()
    out[y:y + patch.shape[0], x:x + patch.shape[1]] = patch
    return out


def motion_ctx(path):
    info = video.probe(path, "clip")
    ctx = ClipCtx("clip", 0, path, info, None, {}, "cut")
    ctx.load_low()
    with np.errstate(invalid="ignore", divide="ignore"):
        motion.run_clip(ctx)
    return ctx


def viol(ctx, check, severity="FAIL"):
    return [v for v in ctx.violations if v.check == check and v.severity == severity]


@needs(CROPS)
class Q40CursorOnOrangeButton(unittest.TestCase):
    """Fix 1 (Q-40, Q-43 knock-on): the demo cursor parked on the orange active "Swimlanes" button was read as a
    pointer without the demo cursor's look (the orange button taken as a coloured glow), so Q-40 failed on c1 and Q-43
    found no cursor at swimlanes -> step-dialog. The look is now the sprite's own: white fill, DARK outline."""

    @classmethod
    def setUpClass(cls):
        cls.c = crops()

    def test_demo_cursor_on_orange_button_is_the_demo_cursor(self):
        for k in ("c1_swim_1p5", "c1_swim_2p47", "c1_dialog_0"):
            found = tips(self.c[k], self.c[k + "_origin"])
            self.assertEqual(found, [(1608, 169, True)], k)
            self.assertFalse(cursor.q40_bad(cursor.analyse_frame(self.c[k])), k)

    def test_q43_boundary_tip_found_and_continuous(self):
        a = tips(self.c["c1_swim_2p47"], self.c["c1_swim_2p47_origin"])
        b = tips(self.c["c1_dialog_0"], self.c["c1_dialog_0_origin"])
        self.assertTrue(a and a[0][2] and b and b[0][2])
        d = ((a[0][0] - b[0][0]) ** 2 + (a[0][1] - b[0][1]) ** 2) ** 0.5
        self.assertLessEqual(d, cursor.JUMP_PX)

    def test_coral_second_pointer_still_not_the_demo_cursor(self):
        ps = cursor.analyse_frame(self.c["coral"])
        self.assertEqual(len(ps), 1)
        self.assertFalse(ps[0]["demo"])            # its outline is coral, not dark
        self.assertGreater(ps[0]["look"]["outline_luma"], cursor.OUTLINE_LUMA_MAX)
        self.assertTrue(cursor.q40_bad(ps))

    def test_coral_pointer_on_the_orange_button_page_fails(self):
        img = paste(self.c["c1_swim_1p5"], self.c["coral"], 90, 45)     # beside the parked demo cursor
        ps = cursor.analyse_frame(img)
        self.assertEqual(len(ps), 2, ps)
        self.assertEqual(sorted(p["demo"] for p in ps), [False, True])
        self.assertTrue(cursor.q40_bad(ps))

    @needs(FIX)
    def test_q90_second_pointer_fixture_still_fails(self):
        import shutil
        from test_gate import make_take, run
        root = make_take([("q40", os.path.join(FIX, "q40-second-pointer.mp4"))])
        try:
            st, res, txt = run(root, "--no-events")
        finally:
            shutil.rmtree(root, ignore_errors=True)
        self.assertEqual(st, 1, txt)
        self.assertTrue([v for v in res["violations"] if v["check"] == "Q-40"], txt)


@needs(CROPS)
class Q40ConnectorArrowheads(unittest.TestCase):
    """Fix 2 (Q-40): the static grey (and orange) connector arrowheads between the app's step cards match the arrow
    head of the template, but they have no tail: they are not pointers. The only pointer is the demo cursor."""

    @classmethod
    def setUpClass(cls):
        cls.c = crops()

    def test_arrowheads_are_not_pointers(self):
        for k in ("c3_arrows_a", "c3_arrows_b", "c3_arrows_c", "c3_arrows_h"):
            self.assertEqual(cursor.analyse_frame(self.c[k]), [], k)

    def test_demo_cursor_on_the_horizontal_button(self):
        self.assertEqual(tips(self.c["c3_cursor"], self.c["c3_cursor_origin"]), [(1498, 177, True)])

    def test_second_pointer_among_the_arrowheads_still_fails(self):
        # the real demo cursor sprite (a copy) and the coral pointer pasted between the arrowheads of the same frame
        cur = self.c["c3_cursor"]
        ox, oy = self.c["c3_cursor_origin"]
        sprite = cur[177 - oy - 6:177 - oy + 26, 1498 - ox - 6:1498 - ox + 20]
        base = self.c["c3_arrows_h"]
        two = paste(paste(base, sprite, 100, 10), sprite, 300, 10)
        ps = cursor.analyse_frame(two)
        self.assertEqual(len(ps), 2, ps)
        self.assertTrue(all(p["demo"] for p in ps))
        self.assertTrue(cursor.q40_bad(ps))                     # two pointers
        coral = paste(paste(base, sprite, 100, 10), self.c["coral"], 300, 5)
        ps = cursor.analyse_frame(coral)
        self.assertEqual(sorted(p["demo"] for p in ps), [False, True], ps)
        self.assertTrue(cursor.q40_bad(ps))


class Q31EasedHorizontalPan(unittest.TestCase):
    """Fix 3 (Q-31): c3's eased 1364 px horizontal pan was read as a 1110 px pan with a 17-frame stall: one bright
    line set 'the largest' row energy, so most of the sparse diagram counted as a sticky band and the slow ease-in
    frames had too little surface (read as repeated frames or unmeasurable). The sticky-band rule now compares band
    energy (16 px). The mild judder (evenly dropped paints) stays a Q-34 WARN (OD-15)."""

    @needs(C3PAN)
    def test_c3_pan_is_one_smooth_pan_with_a_q34_warn(self):
        ctx = motion_ctx(C3PAN)
        self.assertEqual(viol(ctx, "Q-31"), [], [v.reason for v in ctx.violations])
        self.assertEqual(viol(ctx, "Q-34"), [], [v.reason for v in ctx.violations])
        w = viol(ctx, "Q-34", "WARN")
        self.assertEqual(len(w), 1, [v.reason for v in ctx.violations])
        self.assertIn("right pan of 13", w[0].reason)
        total = float(w[0].reason.split("right pan of ")[1].split(" px")[0])
        self.assertAlmostEqual(total, 1364, delta=12)            # c3pan-shift.txt: 1364 px

    @needs(FIX)
    def test_59px_step_pan_still_fails(self):
        ctx = motion_ctx(os.path.join(FIX, "q34-pan-paints-every-2nd-frame-59px.mp4"))
        self.assertTrue(viol(ctx, "Q-31"), [v.reason for v in ctx.violations])
        self.assertTrue(viol(ctx, "Q-34"), [v.reason for v in ctx.violations])

    def test_band_energy_ignores_one_bright_line(self):
        e = np.zeros(200)
        e[40:160] = 1000.0          # a sparse diagram moving rigidly
        e[100] = 10000.0            # one bright line (a dashed connector): alone it would be "the largest"
        self.assertLess(np.sum(e >= motion.STICKY_FRAC * e.max()), 2)   # the old per-row rule: 1 row left
        be = motion._band_energy(e)
        rows = np.where(be >= motion.STICKY_FRAC * be.max())[0]
        self.assertGreater(len(rows), 100)
        e2 = e.copy()
        e2[40:80] = 0.0             # a pinned header band stays sticky
        be2 = motion._band_energy(e2)
        self.assertFalse(np.any(be2[40:72] >= motion.STICKY_FRAC * be2.max()))


class Q31EaseTailsOfASparsePan(unittest.TestCase):
    """Fix (Q-31, 2026-09-25, v1-parity section 3): c3 t3 04-pan, an eased 804 px right pan over the dark, sparse
    diagram (camera event: distance 804, 91 curve steps; shift.py: 1, 2, 2, 2, 3, ... 14 ... 1 px, a repeated frame
    about every 2nd frame), was judged as a 576 px pan whose first step is 12 px: the 480x240 MAD of the ease-in
    frames is 0.09-0.50 (<= 0.5, "still"; the 14 px peak is only 0.67), so the episode began 50 frames into the
    motion. A seeded episode now extends over the adjacent faint changes (MAD > FAINT_MAD). Data: c3-eased-pan-804px.mp4
    = t3 clips/04-pan.mp4 frames 0-179, full frame (a crop would raise the MAD and hide the bug);
    c3-pan-ease-in-cut-12px.mp4 = frames 58-179 of the same clip after 12 copies of frame 58 (the ease-in cut off:
    the pan starts from rest with a 12 px step)."""

    @needs(C3EASED)
    def test_c3_eased_804px_pan_passes_q31(self):
        ctx = motion_ctx(C3EASED)
        self.assertEqual(viol(ctx, "Q-31"), [], [v.reason for v in ctx.violations])
        self.assertEqual(viol(ctx, "Q-34"), [], [v.reason for v in ctx.violations])
        w = viol(ctx, "Q-34", "WARN")                      # evenly dropped paints (OD-15)
        self.assertEqual(len(w), 1, [v.reason for v in ctx.violations])
        total = float(w[0].reason.split("right pan of ")[1].split(" px")[0])
        self.assertAlmostEqual(total, 804, delta=8)

    @needs(C3EASED)
    def test_old_episode_rule_reproduces_the_false_positive(self):
        old = motion.FAINT_MAD
        motion.FAINT_MAD = float("inf")                   # no extension: the episode rule before the fix
        try:
            ctx = motion_ctx(C3EASED)
        finally:
            motion.FAINT_MAD = old
        v = viol(ctx, "Q-31")
        self.assertEqual(len(v), 1, [x.reason for x in ctx.violations])
        self.assertIn("not eased: first step 12 px", v[0].reason)

    @needs(C3CUT)
    def test_pan_with_the_ease_in_cut_off_still_fails(self):
        ctx = motion_ctx(C3CUT)
        v = viol(ctx, "Q-31")
        self.assertEqual(len(v), 1, [x.reason for x in ctx.violations])
        self.assertIn("not eased: first step 12 px", v[0].reason)

    def test_extension_bridges_only_short_stalls(self):
        faint = np.zeros(40, bool)
        faint[[3, 5, 9, 27, 29, 34]] = True               # 3 still frames (6-8) are bridged; longer rests (10-19, 30-33) end it
        self.assertEqual(motion._extend_faint(faint, 20, 25, 40), (20, 29))
        self.assertEqual(motion._extend_faint(faint, 12, 25, 40), (3, 29))


class Q31StickyHeaderRideAlong(unittest.TestCase):
    """Fix 4 (Q-31): the lane headers of c2 ride up ~56 px with the diagram over frames 4-16 (0.13-0.53 s) and then
    pin. The gate failed it as '1 frame, -8 px'; it now traces the band and reports the ride-along (FAIL)."""

    def ride(self, path):
        ctx = motion_ctx(path)
        f = viol(ctx, "Q-31")
        self.assertEqual(len(f), 1, [v.reason for v in ctx.violations])
        r = f[0].extra.get("ride_along")
        self.assertIsNotNone(r, f[0].reason)
        self.assertIn("rides along with the pan", f[0].reason)
        return r

    @needs(C2RIDE)
    def test_c2_ride_along_56px_fails(self):
        r = self.ride(C2RIDE)
        self.assertAlmostEqual(r["px"], 56, delta=4)
        self.assertAlmostEqual(r["t0"], 0.13, delta=0.05)
        self.assertAlmostEqual(r["t1"], 0.53, delta=0.05)
        self.assertGreaterEqual(r["frames"], 11)

    @needs(FIX)
    def test_q90_sticky_header_fixture_reports_the_ride(self):
        r = self.ride(os.path.join(FIX, "q31-sticky-header-ride-along.mp4"))
        self.assertAlmostEqual(r["px"], 56, delta=4)            # README: rides along for 56 px, then pins

    @needs(C3PAN, FIX)
    def test_no_ride_along_in_clean_pans(self):
        for p in (C3PAN, os.path.join(FIX, "q31-clean-pan-2036px-PASS.mp4")):
            ctx = motion_ctx(p)
            self.assertEqual(viol(ctx, "Q-31"), [], p)


@needs(os.path.join(DATA, "c3-raw-vfr-3s.mp4"))
class SeekDecodeIsFrameExact(unittest.TestCase):
    """Seeked decodes (decode_scaled: Q-23; decode_full: Q-31/Q-34, Q-30, Q-20/Q-21 windows) return exactly frames
    start..start+count-1. c3-raw-vfr-3s.mp4 is the first 3.2 s of the c3 raw take (runs/c3-correction-loop-20260925-
    052021-t1/raw.mp4), stream-copied so its millisecond timestamps (33/34/33 ms) are unchanged. Without
    -fps_mode passthrough the constant-rate rawvideo output repeated the first frame after some seeks and shifted
    the rest by one (2 of 91 starts here; 15 of 345 on the full c2/c3 raw takes)."""

    RAW = os.path.join(DATA, "c3-raw-vfr-3s.mp4")

    def reference(self, w, h, pix):
        import subprocess
        vf = f"scale={w}:{h}:flags=area,format=gray" if pix == "scaled" else "format=gray"
        out = subprocess.run([video.ffmpeg(), "-v", "error", "-i", self.RAW, "-map", "0:v:0", "-fps_mode",
                              "passthrough", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(out, np.uint8).reshape(-1, h, w)

    def test_decode_scaled_every_start(self):
        info = video.probe(self.RAW, "raw")
        ref = self.reference(160, 90, "scaled")
        wrong = []
        for s in range(1, len(ref) - 6):
            got = video.decode_scaled(self.RAW, "raw", 160, 90, s, 6, info["fps"])
            if len(got) != 6 or any(np.abs(got[k].astype(int) - ref[s + k].astype(int)).mean() > 0.3
                                    for k in range(6)):
                wrong.append(s)
        self.assertEqual(wrong, [])

    def test_decode_full_at_the_starts_that_shifted(self):
        info = video.probe(self.RAW, "raw")
        W, H = info["width"], info["height"]
        ref = self.reference(W, H, "full")
        for s in range(60, 80):
            got = video.decode_full(self.RAW, "raw", W, H, s, 4, info["fps"], "gray")
            self.assertEqual(len(got), 4, s)
            for k in range(4):
                self.assertLess(float(np.abs(got[k].astype(int) - ref[s + k].astype(int)).mean()), 0.3, (s, k))


if __name__ == "__main__":
    unittest.main()
