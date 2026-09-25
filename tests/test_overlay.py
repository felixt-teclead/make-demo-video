"""Unit tests (stdlib only) for the on-camera timing, pacing, the controller's call order and the M2 hook adapter.

Run: python3 -m unittest discover -s tests -p 'test_*.py'
The filmed measurements (glide, ripple, press, typing on video) are tests/visual/run.sh.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vc.overlay import pacing  # noqa: E402
from vc.overlay import timing as T  # noqa: E402
from vc.overlay.controller import Overlay  # noqa: E402
from vc.overlay.jev_hooks import OnCamera  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def mono(self):
        return self.now

    def wall(self):
        return 1.79e9 + self.now

    def sleep(self, s):
        self.now += max(0.0, s)


class FakeCDP:
    def __init__(self, clock):
        self.clock, self.calls, self.scripts, self.value, self.n = clock, [], {}, None, 0

    def __call__(self, method, **params):
        self.calls.append((round(self.clock.now, 4), method, params))
        if method == "Page.addScriptToEvaluateOnNewDocument":
            self.n += 1
            ident = str(self.n)
            self.scripts[ident] = params["source"]
            return {"identifier": ident}
        if method == "Page.removeScriptToEvaluateOnNewDocument":
            self.scripts.pop(params["identifier"])
            return {}
        if method == "Runtime.evaluate":
            if ".press(" in params["expression"]:
                return {"result": {"value": self.clock.wall() * 1000}}
            if "__jevFast" in params["expression"]:
                return {"result": {"value": self.value}}
            return {"result": {"value": None}}
        return {}

    def evals(self):
        return [c for c in self.calls if c[1] == "Runtime.evaluate"]


class TimingTest(unittest.TestCase):
    def test_fitts_examples_from_q74(self):
        for d, ms in ((300, 259), (600, 412), (1000, 529), (1500, 624), (120, 180), (688, 443)):
            self.assertAlmostEqual(T.glide_ms(d), ms, delta=0.6, msg=d)
        self.assertEqual(T.glide_ms(100000), 1100)
        self.assertEqual(T.glide_ms(0), 0)

    def test_target_width_clamp(self):
        self.assertEqual(T.target_width(), 40)
        self.assertEqual(T.target_width(10, 200), 16)
        self.assertEqual(T.target_width(300, 90), 64)
        self.assertEqual(T.target_width(30, 50), 30)

    def test_sine_ease_zero_velocity_at_ends(self):
        self.assertEqual(T.sine_ease(0), 0)
        self.assertEqual(T.sine_ease(1), 1)
        self.assertLess(T.sine_ease(0.01), 0.001)
        self.assertAlmostEqual(T.sine_ease(0.5), 0.5)

    def test_typing_plan(self):
        plan = T.typing_schedule("a  b\tc", submit=True)
        self.assertEqual([c for _, c in plan], ["a", " ", "b", " ", "c", "\n"])
        self.assertEqual([t for t, _ in plan][:5], [0, 37, 74, 111, 148])
        self.assertEqual(plan[-1][0], 148 + 200)
        self.assertEqual(T.typing_schedule("ab", submit=False)[-1][1], "b")
        self.assertEqual(T.typing_duration_ms(25), 888)


class PacingTest(unittest.TestCase):
    def test_first_match_wins(self):
        self.assertEqual(pacing.readability_pause(card_chars=10, text_words=100), (1.2, "card"))
        self.assertEqual(pacing.readability_pause(card_chars=48)[0], 3.0)
        self.assertEqual(pacing.readability_pause(card_chars=500)[0], 6.0)
        self.assertEqual(pacing.readability_pause(card_chars=10, before_write=True)[0], 1.6)
        self.assertEqual(pacing.readability_pause(text_words=1), (1.5, "text"))
        self.assertAlmostEqual(pacing.readability_pause(text_words=10)[0], 10 / 3.3)
        self.assertEqual(pacing.readability_pause(text_words=100)[0], 6.0)
        self.assertEqual(pacing.readability_pause(same_control=True, new_view=True), (0.2, "same_control"))
        self.assertEqual(pacing.readability_pause(new_view=True), (1.0, "new_view"))
        self.assertEqual(pacing.readability_pause(), (0.3, "other"))

    def test_decision_time_counts_toward_pause(self):
        self.assertEqual(pacing.remaining_pause(1.0, 0.7), 0.30000000000000004)
        self.assertEqual(pacing.remaining_pause(1.0, 2.0), 0.0)

    def test_holds(self):
        self.assertEqual(pacing.HOLDS, {"landing": 1.0, "step": 1.0, "dialog": 3.0, "final": 12.0})
        self.assertTrue(pacing.is_timeline_hold(1.21))
        self.assertFalse(pacing.is_timeline_hold(1.2))


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.clk = Clock()
        self.cdp = FakeCDP(self.clk)
        self.log = []
        self.ov = Overlay(self.cdp, sleep=self.clk.sleep, monotonic=self.clk.mono, wall=self.clk.wall,
                          log=self.log.append, start=(1000, 800))

    def test_install_registers_new_document_script(self):
        self.ov.install()
        self.assertEqual(len(self.cdp.scripts), 1)
        src = next(iter(self.cdp.scripts.values()))
        self.assertIn('"x": 1000.0', src)
        self.assertIn("vc.overlay", src)

    def test_script_replaced_add_before_remove(self):
        self.ov.install()
        self.ov.place(10, 20)
        methods = [m for _, m, _ in self.cdp.calls if "Script" in m]
        self.assertEqual(methods, ["Page.addScriptToEvaluateOnNewDocument", "Page.addScriptToEvaluateOnNewDocument",
                                   "Page.removeScriptToEvaluateOnNewDocument"])
        self.assertEqual(len(self.cdp.scripts), 1)
        self.assertIn('"x": 10.0', next(iter(self.cdp.scripts.values())))

    def test_click_order_and_timing(self):
        self.ov.install()
        clicked = []
        ev = self.ov.click(1300, 800, lambda: clicked.append(self.clk.now), size=(40, 40))
        glide_ms = T.glide_ms(300)
        exprs = [(t, p["expression"]) for t, _m, p in self.cdp.evals()]
        t_glide = next(t for t, e in exprs if ".glide(" in e)
        t_press = next(t for t, e in exprs if ".press(" in e)
        # glide -> (its duration) -> 120 ms rest -> press -> 120 ms -> real click
        self.assertAlmostEqual(t_press - t_glide, glide_ms / 1000 + 0.120, places=3)
        self.assertAlmostEqual(clicked[0] - t_press, 0.120, places=3)
        self.assertEqual(ev["type"], "click")
        self.assertAlmostEqual(ev["t"], self.clk.wall() - 0.120 - 0.0, delta=0.01)
        self.assertLess(ev["glide_t"], ev["t"])
        self.assertIn("click_t", ev)
        # the new-document script carries the ripple, so a navigating click continues it
        self.assertIn('"ripple": {"x": 1300.0', next(iter(self.cdp.scripts.values())))

    def test_recheck_follows_moved_target(self):
        pts = iter([(1310, 800), (1310, 800)])
        ev = self.ov.click(1300, 800, lambda: None, recheck=lambda: next(pts))
        self.assertEqual(ev["x"], 1310)
        self.assertEqual(len([e for e in self.log if e.get("type") == "glide"]), 2)
        self.assertEqual(len([c for c in self.cdp.evals() if ".press(" in c[2]["expression"]]), 1)

    def test_typing_flat_37ms_and_enter(self):
        inserted = []
        enters = []
        self.ov.type_text("Hallo   Welt", lambda ch: inserted.append((self.clk.now, ch)), submit=True,
                          press_enter=lambda: enters.append(self.clk.now))
        self.assertEqual("".join(c for _, c in inserted), "Hallo Welt")
        gaps = [round(b[0] - a[0], 4) for a, b in zip(inserted, inserted[1:])]
        self.assertTrue(all(abs(g - 0.037) < 1e-6 for g in gaps), gaps)
        self.assertAlmostEqual(enters[0] - inserted[-1][0], 0.2, places=4)
        ev = [e for e in self.log if e.get("type") == "typing"][0]
        self.assertIn("end", ev)

    def test_no_enter_unless_submit(self):
        enters = []
        self.ov.type_text("x", lambda ch: None, submit=False, press_enter=lambda: enters.append(1))
        self.assertEqual(enters, [])

    def test_mismatch_sets_whole_value(self):
        got = []
        ev = self.ov.type_text("abc", lambda ch: None, read_value=lambda: "ab", set_value=got.append)
        self.assertEqual(got, ["abc"])
        self.assertTrue(ev["value_set_whole"])

    def test_paste_is_one_insert_after_150ms(self):
        ins = []
        t0 = self.clk.now
        ev = self.ov.paste("PR-1", lambda v: ins.append((self.clk.now, v)))
        self.assertEqual(ins, [(t0 + 0.15, "PR-1")])
        self.assertEqual(ev["type"], "paste")


class FakeTab:
    def __init__(self, clock):
        self.cdp = FakeCDP(clock)
        self.pts = []

    def call(self, method, **params):
        return self.cdp(method, **params)

    def viewport(self):
        return (1920, 1080)

    def point(self, node):
        return self.pts.pop(0) if self.pts else {"x": 500, "y": 400, "w": 80, "h": 30}

    def js(self, expr, await_promise=False):
        return self.cdp.value


class JevHooksTest(unittest.TestCase):
    def setUp(self):
        self.clk = Clock()
        self.tab = FakeTab(self.clk)
        self.events = []
        self.cam = OnCamera(self.tab, emit=self.events.append)
        self.cam.ov._sleep, self.cam.ov._mono, self.cam.ov._wall = self.clk.sleep, self.clk.mono, self.clk.wall

    def presses(self):
        return len([c for c in self.tab.cdp.evals() if ".press(" in c[2]["expression"]])

    def test_click_via_hooks_emits_m4_fields(self):
        a = {"id": 1, "node": 7, "kind": "click", "own_label": "BPMN"}
        p = {"x": 500, "y": 400, "w": 80, "h": 30}
        self.cam.before_input(a, p)
        self.assertEqual(self.presses(), 0)             # the glide only; the press waits for M2's final check
        self.cam.before_click(a, p)
        self.cam.after_input(a, p, None)
        self.assertEqual(self.presses(), 1)
        ev = self.events[0]
        for k in ("type", "t", "glide_t", "x", "y", "click_t", "label"):
            self.assertIn(k, ev)
        self.assertEqual(ev["type"], "click")

    def test_second_before_input_for_same_action_never_ripples_again(self):
        a = {"id": 2, "node": 7, "kind": "click"}
        self.tab.pts = [{"x": 500, "y": 400, "w": 80, "h": 30}, {"x": 520, "y": 400, "w": 80, "h": 30}]
        self.cam.before_input(a, {"x": 500, "y": 400, "w": 80, "h": 30})
        self.cam.before_input(a, {"x": 520, "y": 400, "w": 80, "h": 30})     # M2 saw the target move
        self.cam.before_click(a, {"x": 520, "y": 400, "w": 80, "h": 30})
        self.cam.before_click(a, {"x": 520, "y": 400, "w": 80, "h": 30})     # a repeated call never ripples again
        self.assertEqual(self.presses(), 1)
        self.assertEqual(self.cam.ov.x, 520)

    def test_rest_120ms_counts_from_arrival_and_press_is_last(self):
        a = {"id": 5, "node": 7, "kind": "click"}
        p = {"x": 500, "y": 400, "w": 80, "h": 30}
        self.cam.before_input(a, p)
        t_arrive = self.clk.now
        self.clk.sleep(0.05)                           # M2's point re-read and hover take 50 ms
        self.cam.before_click(a, p)
        t_press = next(t for t, m, q in self.tab.cdp.calls if m == "Runtime.evaluate" and ".press(" in q["expression"])
        self.assertAlmostEqual(t_press - t_arrive, 0.120, places=3)
        self.assertAlmostEqual(self.clk.now - t_press, 0.120, places=3)

    def test_target_gone_at_the_press_draws_nothing(self):
        class Gone(Exception):
            pass
        cam = OnCamera(self.tab, emit=self.events.append, stale=Gone)
        cam.ov._sleep, cam.ov._mono, cam.ov._wall = self.clk.sleep, self.clk.mono, self.clk.wall
        a = {"id": 6, "node": 7, "kind": "click"}
        p = {"x": 500, "y": 400, "w": 80, "h": 30}
        cam.before_input(a, p)
        self.tab.pts = [None]
        with self.assertRaises(Gone):
            cam.before_click(a, p)
        self.assertEqual(self.presses(), 0)
        self.assertEqual(self.events, [])

    def test_input_failing_after_the_press_logs_an_orphan_ripple(self):
        a = {"id": 7, "node": 7, "kind": "click", "own_label": "Weiter"}
        p = {"x": 500, "y": 400, "w": 80, "h": 30}
        self.cam.before_input(a, p)
        self.cam.before_click(a, p)
        self.cam.input_aborted(a, p, "page changed")
        self.assertEqual([e["type"] for e in self.events], ["orphan_ripple"])
        self.assertEqual(self.events[0]["label"], "Weiter")
        self.assertIsNone(self.cam.ov.pending)

    def test_fill_types_after_its_click(self):
        a = {"id": 3, "node": 9, "kind": "fill"}
        p = {"x": 300, "y": 200, "w": 400, "h": 36}
        self.tab.pts = [p, p]
        self.cam.before_input(a, p)
        self.cam.before_click(a, p)
        self.tab.cdp.value = "Hallo Welt"
        info = self.cam.type_text(self.tab, a, p, "Hallo  Welt", False)
        self.cam.after_input(a, p, info)
        kinds = [e["type"] for e in self.events]
        self.assertEqual(kinds, ["click", "typing"])
        ins = [c[2]["text"] for c in self.tab.cdp.calls if c[1] == "Input.insertText"]
        self.assertEqual("".join(ins), "Hallo Welt")
        self.assertFalse(info["fallback_whole_value"])

    def test_copied_value_is_pasted(self):
        self.cam.mark_copied("PR-2026-1")
        a = {"id": 4, "node": 9, "kind": "fill"}
        p = {"x": 300, "y": 200, "w": 400, "h": 36}
        self.tab.pts = [p, p]
        self.cam.before_input(a, p)
        self.cam.before_click(a, p)
        info = self.cam.type_text(self.tab, a, p, "PR-2026-1", False)
        ins = [c[2]["text"] for c in self.tab.cdp.calls if c[1] == "Input.insertText"]
        self.assertEqual(ins, ["PR-2026-1"])
        self.assertTrue(info["paste"])
        self.assertEqual(self.events[-1]["type"], "paste")

    # ---- Q-71: the real pointer is parked off the clicked control (no native title tooltip) ------------------
    def moves(self):
        return [(t, q["x"], q["y"]) for t, m, q in self.tab.cdp.calls
                if m == "Input.dispatchMouseEvent" and q.get("type") == "mouseMoved"]

    def test_click_parks_the_real_pointer_after_the_ripple(self):
        a = {"id": 11, "node": 7, "kind": "click", "own_label": "Prozesse"}
        p = {"x": 143, "y": 202, "w": 240, "h": 40}
        self.cam.before_input(a, p)
        self.cam.before_click(a, p)
        sprite = (self.cam.ov.x, self.cam.ov.y)
        self.cam.after_input(a, p, None)
        ring_t = self.events[0]["t"]
        mv = self.moves()
        self.assertEqual(len(mv), 1)
        t, x, y = mv[0]
        self.assertEqual((x, y), (-1.0, -1.0))                       # outside the viewport: hovers nothing
        self.assertAlmostEqual(self.clk.wall() - ring_t, T.RIPPLE_MS / 1000.0, places=3)   # after the ripple
        self.assertEqual((self.cam.ov.x, self.cam.ov.y), sprite)      # the demo cursor stays on the control

    def test_fill_parks_right_after_the_field_click_before_typing(self):
        a = {"id": 12, "node": 9, "kind": "fill"}
        p = {"x": 300, "y": 200, "w": 400, "h": 36}
        self.tab.pts = [p, p]
        self.cam.before_input(a, p)
        self.cam.before_click(a, p)
        self.tab.cdp.value = "Bestell"
        self.cam.type_text(self.tab, a, p, "Bestell", False)
        kinds = [(m, q.get("type")) for _, m, q in self.tab.cdp.calls if m.startswith("Input.")]
        release = kinds.index(("Input.dispatchMouseEvent", "mouseReleased"))
        self.assertEqual(kinds[release + 1], ("Input.dispatchMouseEvent", "mouseMoved"))
        first_char = kinds.index(("Input.insertText", None))
        self.assertLess(release + 1, first_char)

    def test_install_parks_and_park_off_never_moves(self):
        self.cam.install()
        self.assertEqual([(x, y) for _, x, y in self.moves()], [(-1.0, -1.0)])
        cam = OnCamera(self.tab, emit=self.events.append, park="off")
        cam.ov._sleep, cam.ov._mono, cam.ov._wall = self.clk.sleep, self.clk.mono, self.clk.wall
        n = len(self.moves())
        a = {"id": 13, "node": 7, "kind": "click"}
        p = {"x": 500, "y": 400, "w": 80, "h": 30}
        cam.install()
        cam.before_input(a, p)
        cam.before_click(a, p)
        cam.after_input(a, p, None)
        self.assertEqual(len(self.moves()), n)

    def test_park_knob(self):
        from vc.overlay.jev_hooks import park_point
        self.assertEqual(park_point("outside"), (-1.0, -1.0))
        self.assertIsNone(park_point("off"))
        self.assertEqual(park_point("1900, 1070"), (1900.0, 1070.0))
        with self.assertRaises(ValueError):
            park_point("corner")


if __name__ == "__main__":
    unittest.main()
