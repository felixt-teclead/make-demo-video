"""Cross-milestone tests without a browser: M2 runner x M3 overlay hooks (before_click, hover, orphan ripple),
the VC_HOOKS module, and the camera (Q-31 pan profile, Q-37 still beat, events)."""
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for p in (ROOT, os.path.join(ROOT, "jev"), os.path.join(ROOT, "jev", "tests"), os.path.join(ROOT, "tests")):
    sys.path.insert(0, p)

from fakes import FakeJev, FakeStale, FakeTab  # noqa: E402  (jev/tests/fakes.py)
from vcjev.accounting import DecisionLog  # noqa: E402
from vcjev.runner import Hooks, StepRunner  # noqa: E402
from test_runner import MODELS  # noqa: E402  (jev/tests/test_runner.py)
from vc.overlay import camera as C  # noqa: E402
from vc.overlay import vc_hooks  # noqa: E402
from vc.overlay.jev_hooks import OnCamera  # noqa: E402
from test_overlay import Clock, FakeCDP  # noqa: E402

CLICK = {"name": "details", "op": "click", "target": {"label": "Details anzeigen"},
         "check": {"type": "text_visible", "value": "Details"}}


class FilmedFakeTab(FakeTab):
    """jev's fake tab plus the CDP surface the overlay draws through."""

    def __init__(self, clock, order, **kw):
        super().__init__(**kw)
        self.cdp, self.order = FakeCDP(clock), order

    def call(self, method, **params):
        if method == "Runtime.evaluate" and ".press(" in params.get("expression", ""):
            self.order.append("press")
        if method == "Runtime.evaluate" and ".glide(" in params.get("expression", ""):
            self.order.append("glide")
        return self.cdp(method, **params)

    def viewport(self):
        return (1920, 1080)

    def js(self, expr, await_promise=False):
        return None

    def hover(self, point):
        self.order.append("hover")

    def act(self, action, page, text=None):
        r = super().act(action, page, text)
        self.order.append("click")
        return r


class M2xM3(unittest.TestCase):
    def setUp(self):
        self.clk, self.order, self.events = Clock(), [], []
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_step(self, tab):
        cam = OnCamera(tab, emit=self.events.append, stale=FakeStale)
        cam.ov._sleep, cam.ov._mono, cam.ov._wall = self.clk.sleep, self.clk.mono, self.clk.wall
        log = DecisionLog(os.path.join(self.tmp, "take.log.jsonl"), job="j", phase="take", take=1)
        r = StepRunner(tab, log, MODELS, hooks=Hooks(**cam.hooks()), choose=FakeJev(), stale=FakeStale,
                       sleep=self.clk.sleep, clock=self.clk.wall)
        return r.run_step(CLICK)

    def test_order_glide_hover_press_click_and_one_event(self):
        tab = FilmedFakeTab(self.clk, self.order, points=[{"x": 500.0, "y": 300.0, "w": 80, "h": 30}])
        self.assertTrue(self.run_step(tab).ok)
        self.assertEqual(self.order, ["glide", "hover", "press", "click"])
        self.assertEqual([e["type"] for e in self.events], ["click"])
        ev = self.events[0]
        for k in ("t", "glide_t", "click_t", "x", "y", "label"):
            self.assertIn(k, ev)
        self.assertAlmostEqual(ev["click_t"] - ev["t"], 0.120, places=2)     # Q-76: real click 120 ms after ring

    def test_input_lost_after_press_is_logged_as_orphan_then_retried(self):
        tab = FilmedFakeTab(self.clk, self.order, points=[{"x": 500.0, "y": 300.0, "w": 80, "h": 30}], stale_acts=1)
        self.assertTrue(self.run_step(tab).ok)
        self.assertEqual([e["type"] for e in self.events], ["orphan_ripple", "click"])


class CameraProfile(unittest.TestCase):
    def test_durations(self):
        self.assertAlmostEqual(C.pan_seconds(1000), 2.5)                      # 400 px/s mean
        self.assertAlmostEqual(C.pan_seconds(1000, 3.0), 3.0)                 # the spec's seconds
        self.assertAlmostEqual(C.pan_seconds(2000, 1.0), math.pi * 2000 / 2400)  # 40 px/frame cap lengthens it
        self.assertEqual(C.pan_seconds(20), C.MIN_PAN_S)

    def test_q31_profile_rigid_eased_one_direction(self):
        for d, secs in ((1000, None), (2036, 8.4), (300, None), (-700, None), (2000, 1.0)):
            offs = C.sine_offsets(d, C.pan_seconds(d, secs))
            steps = [b - a for a, b in zip(offs, offs[1:])]
            moving = [s for s in steps if s]
            self.assertTrue(all(abs(s) <= 40 for s in steps), (d, max(map(abs, steps))))
            self.assertTrue(all(s * d >= 0 for s in steps), "one direction")
            self.assertGreaterEqual(len(moving), 3)
            big = max(abs(s) for s in moving)
            self.assertLessEqual(abs(moving[0]), 0.6 * big)
            self.assertLessEqual(abs(moving[-1]), 0.6 * big)
            self.assertEqual(offs[-1], d)
            mean = abs(d) / C.pan_seconds(d, secs)
            self.assertLessEqual(mean, 2 / math.pi * 1200 + 1e-6)   # 40 px/frame peak = ~764 px/s mean

    def test_move_emits_event_holds_still_and_checks(self):
        sleeps, emitted = [], []

        class Tab:
            def js(self, expr, await_promise=False):
                assert await_promise and "requestAnimationFrame" in expr
                return {"ok": True, "distance": 900, "axis": "y", "t0": 1.79e12, "t1": 1.79e12 + 2250, "frames": 68,
                        "ms": 2250, "scroller": "page"}

        r = C.move(Tab(), {"op": "pan", "to": {"type": "text_visible", "value": "Schritt 4"}, "direction": "down"},
                   emit=emitted.append, check=lambda to: True, sleep=sleeps.append)
        self.assertTrue(r["ok"])
        self.assertEqual(emitted[0]["type"], "pan")
        self.assertAlmostEqual(emitted[0]["end"] - emitted[0]["t"], 2.25, places=3)
        self.assertEqual(sleeps, [C.STILL_AFTER_S])                            # Q-37 still beat
        r = C.move(Tab(), {"op": "reveal", "to": {"type": "text_visible", "value": "X"}, "hold": 1.0},
                   check=lambda to: False, sleep=sleeps.append)
        self.assertFalse(r["ok"])
        self.assertEqual(len(sleeps), 1)                                       # an action hold replaces the beat

    def test_move_reports_page_failures(self):
        class Tab:
            def js(self, expr, await_promise=False):
                return {"ok": False, "reason": "no scroll container on the x axis (the view may pan by drag)"}
        r = C.move(Tab(), {"op": "pan", "to": {"type": "text_visible", "value": "X"}}, sleep=lambda s: None)
        self.assertFalse(r["ok"])
        self.assertIn("drag", r["reason"])
        r = C.move(Tab(), {"op": "pan", "to": {"type": "dialog_open"}}, sleep=lambda s: None)
        self.assertIn("`to` must be", r["reason"])

    def test_page_source_parses(self):
        node = shutil.which("node") or os.path.expanduser("~/.local/bin/node")
        if not os.path.exists(node):
            self.skipTest("node not available")
        src = C._page_source({"text": "a", "kind": "pan", "direction": "down", "seconds": 3.0, "margin": 48,
                              "pause_animations": False})
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(src + ";\n")
        try:
            p = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
        finally:
            os.unlink(f.name)


class VcHooksModule(unittest.TestCase):
    def test_filming_hooks_bind_to_the_tab_and_post_events(self):
        posted = []

        class Rec:
            def event(self, ev):
                posted.append(ev)

        h = vc_hooks.make_hooks(Rec(), True)
        for k in vc_hooks.HOOK_NAMES + ("bind", "install", "camera", "mark_copied"):
            self.assertIn(k, h)
        with self.assertRaises(RuntimeError):
            h["before_input"]({"id": 1}, {"x": 1, "y": 1})
        clk = Clock()
        tab = FilmedFakeTab(clk, [])
        h["mark_copied"]("PR-1")
        h["bind"](tab)
        h["install"]()
        self.assertTrue(any(m == "Page.addScriptToEvaluateOnNewDocument" for _, m, _ in tab.cdp.calls))

    def test_dry_run_has_camera_but_no_overlay(self):
        h = vc_hooks.make_hooks(None, False)
        self.assertEqual(set(h), {"bind", "install", "camera", "mark_copied"})
        h["bind"](object())
        self.assertIsNone(h["install"]())


if __name__ == "__main__":
    unittest.main()
