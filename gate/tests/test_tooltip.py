"""Q-71 stray tooltip-like box (gate/vcgate/checks/tooltip.py).

QA definition change approved by the owner 2026-09-25 (self-improvement rule: detect what the parity viewer found).
REAL frames only (gate/tests/data/tooltip/, native pixels, cropped and re-encoded at crf 12):

- c2-prozesse-tooltip-480x270.mp4: the delivered c2 video (runs/jobs/c2-find-and-read-20260925-064810/deliver/
  c2-find-and-read.mp4) 2.3-5.6 s, crop 480x270 at (760,420): Chrome's native "Prozesse" title tooltip, 69x27 px at
  (970,555) in the full frame, on screen 2.87-5.17 s after the sidebar click. Must FAIL.
- c2-dialog-dims-button-320x120.mp4: c2 04-schritt-4 10.6-12.0 s, crop 320x120 at (1600,20): the orange-framed
  "Für die Organisation freigeben" button dims under the step dialog's backdrop. A framed text box that only changes
  tone is no stray box: must not fire (it did before the edge-structure rule).
- the committed parity clips (a 1364 px pan, a sticky-header ride-along) must not fire either.
Run: .venv/bin/python -m unittest discover -s gate/tests
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from test_gate import needs  # noqa: E402
from vcgate import video  # noqa: E402
from vcgate.checks import tooltip  # noqa: E402
from vcgate.model import ClipCtx, ClipEvents  # noqa: E402

DATA = os.path.join(HERE, "data", "tooltip")
PARITY = os.path.join(HERE, "data", "parity")
TIP = os.path.join(DATA, "c2-prozesse-tooltip-480x270.mp4")
DIM = os.path.join(DATA, "c2-dialog-dims-button-320x120.mp4")


def samples(path):
    info = video.probe(path, "t")
    return [f for _, f in tooltip.iter_samples(path, "t", info["width"], info["height"])]


def ctx_for(path, events=None):
    info = video.probe(path, os.path.basename(path))
    ctx = ClipCtx("suche", 0, path, info, events, {})
    ctx.load_low()
    ctx.scale = 1.0          # native-pixel crops: thresholds stay those of the 1920-wide frame
    return ctx


class TestTooltip(unittest.TestCase):
    @needs(TIP, DIM)
    def test_real_prozesse_tooltip_fails(self):
        ctx = ctx_for(TIP)
        tooltip.run_clip(ctx)
        fails = [v for v in ctx.violations if v.check == "Q-71" and v.severity == "FAIL"]
        self.assertEqual(len(fails), 1, [v.to_dict() for v in ctx.violations])
        v = fails[0]
        box = v.extra["box"]
        # full-frame position 970,555 = crop origin 760,420 + 210,135; 69x27 px
        self.assertEqual((box["x0"], box["y0"]), (210, 135))
        self.assertEqual((box["x1"] - box["x0"] + 1, box["y1"] - box["y0"] + 1), (69, 27))
        # on screen 2.87-5.17 s in the delivered video = 0.57-2.87 s in the crop (sampled every 0.1 s)
        self.assertAlmostEqual(v.t0, 0.6, delta=0.15)
        self.assertAlmostEqual(v.t1, 2.9, delta=0.15)
        self.assertIn("disappears as a unit", v.reason)

    @needs(TIP, DIM)
    def test_click_on_the_box_is_the_controls_own_state(self):
        ev = ClipEvents({"clicks": [{"t": 0.1, "x": 240, "y": 148, "label": "x"}]})   # inside the box
        ctx = ctx_for(TIP, ev)
        tooltip.run_clip(ctx)
        self.assertEqual([v for v in ctx.violations if v.check == "Q-71"], [])

    @needs(TIP, DIM)
    def test_dimmed_framed_button_is_no_box(self):
        self.assertEqual(tooltip.find_boxes(samples(DIM)), [])
        # the edge-structure rule is what rejects it
        old = tooltip.SAME_STRUCTURE
        tooltip.SAME_STRUCTURE = 2.0
        try:
            self.assertTrue(tooltip.find_boxes(samples(DIM)))
        finally:
            tooltip.SAME_STRUCTURE = old

    def test_real_pans_do_not_fire(self):
        for name in ("c3-horizontal-pan-1364px.mp4", "c2-sticky-header-ride-56px.mp4"):
            p = os.path.join(PARITY, name)
            if not os.path.exists(p):
                continue
            self.assertEqual(tooltip.find_boxes(samples(p)), [], name)


if __name__ == "__main__":
    unittest.main()
