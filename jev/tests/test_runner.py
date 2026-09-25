"""C-01, C-02, C-03, C-09, C-31 and the typing hook, with a fake tab and a fake jev."""

import tempfile
import unittest
from pathlib import Path

from fakes import FakeJev, FakeStale, FakeTab, act

from vcjev.accounting import DecisionLog
from vcjev.offer import check_offer
from vcjev.runner import Hooks, LoginRequired, Settings, StepFailed, StepRunner

MODELS = {"jev": {"model": "fake-jev", "price": (0.042, 0.0)}}
CLICK = {"name": "details", "op": "click", "target": {"label": "Details anzeigen"},
         "check": {"type": "dialog_open"}}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = DecisionLog(Path(self.dir.name) / "d.jsonl", job="j", phase="take", take=1)
        self.clock = Clock()

    def tearDown(self):
        self.dir.cleanup()

    def runner(self, tab, jev, hooks=None, **settings):
        return StepRunner(tab, self.log, MODELS, settings=Settings(**settings), hooks=hooks, choose=jev,
                          stale=FakeStale, sleep=self.clock.sleep, clock=self.clock)

    def decisions(self):
        return [r for r in self.log.records() if r["type"] == "decision"]

    def test_click_then_independent_check_passes_in_one_decision(self):
        tab, jev = FakeTab(checks=(True,)), FakeJev()
        r = self.runner(tab, jev).run_step(CLICK)
        self.assertTrue(r.ok)
        self.assertEqual(r.decisions, 1)
        self.assertEqual(tab.acted, ["e7c"])
        d = self.decisions()[0]
        self.assertEqual([o["id"] for o in d["offered"]], ["e7c", "wait"])
        self.assertTrue(d["executed"])

    def test_every_logged_offer_has_exactly_one_actionable(self):
        tab, jev = FakeTab(checks=(False,)), FakeJev(("WAIT",))
        with self.assertRaises(StepFailed):
            self.runner(tab, jev).run_step(CLICK)
        for call in jev.calls:
            self.assertTrue(check_offer(call["actions"]))
        for d in self.decisions():
            actionable = [o for o in d["offered"] if o["kind"] in ("click", "fill", "select")]
            self.assertEqual(len(actionable), 1)

    def test_done_with_false_check_fails_the_step(self):
        # C-02 Verify: jev says DONE while the step's check is false -> failed step.
        tab, jev = FakeTab(checks=(False,)), FakeJev(("target", "DONE"))
        with self.assertRaises(StepFailed) as cm:
            self.runner(tab, jev).run_step(CLICK)
        self.assertIn("DONE", cm.exception.reason)
        self.assertFalse(cm.exception.result.ok)

    def test_done_before_acting_is_not_success_even_if_check_true(self):
        tab, jev = FakeTab(checks=(True,)), FakeJev(("DONE",))
        with self.assertRaises(StepFailed) as cm:
            self.runner(tab, jev).run_step(CLICK)
        self.assertIn("before the step's input", cm.exception.reason)
        self.assertEqual(tab.acted, [])

    def test_blocked_fails(self):
        with self.assertRaises(StepFailed):
            self.runner(FakeTab(), FakeJev(("BLOCKED",))).run_step(CLICK)

    def test_cap_stops_an_unsatisfiable_step(self):
        # C-03 Verify: a step that can never be satisfied stops after the cap and does not hang.
        tab, jev = FakeTab(checks=(False,)), FakeJev(("WAIT",))
        with self.assertRaises(StepFailed) as cm:
            self.runner(tab, jev).run_step(CLICK)
        self.assertEqual(len(jev.calls), 6)
        self.assertIn("cap of 6", cm.exception.reason)
        self.assertEqual(cm.exception.step, "details")

    def test_cap_is_configurable_and_counts_clicks_too(self):
        # clicks that change the page but never satisfy the check
        tab, jev = FakeTab(checks=(False,)), FakeJev(("target", "WAIT"))
        with self.assertRaises(StepFailed):
            self.runner(tab, jev, decisions_per_step=2).run_step(CLICK)
        self.assertEqual(len(jev.calls), 2)

    def test_three_inputs_without_page_change_fail(self):
        class Same(FakeTab):
            def observe(self, screenshot=False):
                p = super().observe(screenshot)
                p["fingerprint"] = "same"
                return p

        tab, jev = Same(checks=(False,)), FakeJev(("target",))
        with self.assertRaises(StepFailed) as cm:
            self.runner(tab, jev).run_step(CLICK)
        self.assertIn("did not change the page", cm.exception.reason)

    def test_stale_refusals_retried_twice_then_fail(self):
        tab, jev = FakeTab(stale_acts=99), FakeJev()
        with self.assertRaises(StepFailed) as cm:
            self.runner(tab, jev).run_step(CLICK)
        self.assertEqual(len(jev.calls), 3)  # first try + 2 retries
        self.assertIn("stale", cm.exception.reason)

    def test_one_stale_refusal_then_success(self):
        tab, jev = FakeTab(stale_acts=1), FakeJev()
        r = self.runner(tab, jev).run_step(CLICK)
        self.assertTrue(r.ok)
        self.assertEqual(r.transient, 1)
        self.assertEqual(r.decisions, 2)

    def test_model_error_is_transient(self):
        calls = []

        def flaky(page, goal, history):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("Model provider returned HTTP 502; no action executed.")
            return FakeJev()(page, goal, history)

        r = self.runner(FakeTab(), flaky).run_step(CLICK)
        self.assertTrue(r.ok)
        self.assertEqual(r.transient, 1)

    def test_login_page_stops_without_a_decision(self):
        tab, jev = FakeTab(login=True), FakeJev()
        with self.assertRaises(LoginRequired):
            self.runner(tab, jev).run_step(CLICK)
        self.assertEqual(jev.calls, [])

    def test_ambiguous_target_fails_before_any_decision(self):
        tab, jev = FakeTab(), FakeJev()
        step = {**CLICK, "target": {"label": "Öffnen"}}
        with self.assertRaises(StepFailed) as cm:
            self.runner(tab, jev).run_step(step)
        self.assertIn("before acting", cm.exception.reason)
        self.assertEqual(jev.calls, [])

    def test_absent_target_readiness_wait_then_fail(self):
        tab, jev = FakeTab(), FakeJev()
        step = {**CLICK, "target": {"label": "Später"}}
        with self.assertRaises(StepFailed) as cm:
            self.runner(tab, jev, ready_timeout_s=2).run_step(step)
        self.assertIn("before acting", cm.exception.reason)
        self.assertEqual(jev.calls, [])
        waits = [r for r in self.log.records() if r["type"] == "readiness_wait"]
        self.assertEqual(len(waits), 1)
        self.assertFalse(waits[0]["ok"])

    def test_late_target_is_awaited_then_clicked(self):
        from fakes import page_actions
        late = page_actions() + [act(20, "Später")]
        tab = FakeTab(pages=[page_actions(), page_actions(), late])
        step = {**CLICK, "target": {"label": "Später"}}
        r = self.runner(tab, FakeJev()).run_step(step)
        self.assertTrue(r.ok)
        waits = [x for x in self.log.records() if x["type"] == "readiness_wait"]
        self.assertTrue(waits[0]["ok"])

    def test_click_point_known_before_the_click_and_rechecked(self):
        # C-09: the hook sees the point before act(); if the element moved, it glides again.
        seen = []

        class Tab(FakeTab):
            def act(self, action, page, text=None):
                seen.append(("act", action["id"]))
                return super().act(action, page, text)

        tab = Tab(points=[{"x": 10.0, "y": 10.0}, {"x": 40.0, "y": 10.0}, {"x": 40.0, "y": 10.0}])
        hooks = Hooks(before_input=lambda a, p: seen.append(("glide", p["x"])),
                      after_input=lambda a, p, info: seen.append(("ripple", p["x"])))
        self.runner(tab, FakeJev(), hooks).run_step(CLICK)
        self.assertEqual(seen, [("glide", 10.0), ("glide", 40.0), ("act", "e7c"), ("ripple", 40.0)])
        self.assertEqual(self.decisions()[0]["click_point"], {"x": 40.0, "y": 10.0})

    def test_before_click_after_final_check_and_hover(self):
        # M3's request: a hook right before the real click, after the last point check; hover precedes the press.
        seen = []

        class Tab(FakeTab):
            def act(self, action, page, text=None):
                seen.append(("act", action["id"]))
                return super().act(action, page, text)

            def hover(self, point):
                seen.append(("hover", point["x"]))

        tab = Tab(points=[{"x": 10.0, "y": 10.0}, {"x": 40.0, "y": 10.0}, {"x": 40.0, "y": 10.0}])
        hooks = Hooks(before_input=lambda a, p: seen.append(("glide", p["x"])),
                      before_click=lambda a, p: seen.append(("press", p["x"])),
                      after_input=lambda a, p, info: seen.append(("clicked", p["x"])))
        self.runner(tab, FakeJev(), hooks).run_step(CLICK)
        self.assertEqual(seen, [("glide", 10.0), ("glide", 40.0), ("hover", 40.0), ("press", 40.0), ("act", "e7c"),
                                ("clicked", 40.0)])

    def test_target_gone_at_press_sends_no_input_and_no_orphan(self):
        # before_click raises the stale error (the target vanished during the rest): no input, retried as transient
        seen = []
        calls = {"n": 0}

        def press(a, p):
            calls["n"] += 1
            if calls["n"] == 1:
                raise FakeStale("target vanished before the press")
            seen.append("press")

        tab = FakeTab()
        hooks = Hooks(before_click=press, input_aborted=lambda a, p, r: seen.append("orphan"))
        r = self.runner(tab, FakeJev(), hooks).run_step(CLICK)
        self.assertTrue(r.ok)
        self.assertEqual(seen, ["press"])
        self.assertEqual(tab.acted, ["e7c"])

    def test_input_failing_after_press_reports_the_orphan_ripple(self):
        seen = []
        tab = FakeTab(stale_acts=1)
        hooks = Hooks(before_click=lambda a, p: seen.append("press"),
                      input_aborted=lambda a, p, r: seen.append(("orphan", r)))
        r = self.runner(tab, FakeJev(), hooks).run_step(CLICK)
        self.assertTrue(r.ok)
        self.assertEqual(seen, ["press", ("orphan", "page changed"), "press"])

    def test_type_step_uses_the_typing_hook_not_the_library(self):
        typed = []

        def typist(tab, action, point, text, submit):
            typed.append((action["kind"], text, submit, point["x"]))
            return {"chars": len(text)}

        tab = FakeTab()
        step = {"name": "suche", "op": "type", "target": {"label": "Suche"}, "text": "Urlaub 2026",
                "submit": True, "check": {"type": "text_visible", "value": "Treffer"}}
        r = self.runner(tab, FakeJev(), Hooks(type_text=typist)).run_step(step)
        self.assertTrue(r.ok)
        self.assertEqual(typed, [("fill", "Urlaub 2026", True, 100.0)])
        self.assertEqual(tab.acted, [])  # upstream act (text helper + insertText) never used

    def test_step_without_check_is_rejected(self):
        with self.assertRaises(ValueError):
            self.runner(FakeTab(), FakeJev()).run_step({**CLICK, "check": None})

    def test_denied_target_never_reaches_jev(self):
        tab, jev = FakeTab(), FakeJev()
        with self.assertRaises(StepFailed) as cm:
            self.runner(tab, jev).run_step({**CLICK, "target": {"label": "Delete"}})
        self.assertIn("never-offer", cm.exception.reason)
        self.assertEqual(jev.calls, [])

    def test_decision_records_have_latency_tokens_cost(self):
        self.runner(FakeTab(), FakeJev()).run_step(CLICK)
        d = self.decisions()[0]
        self.assertEqual((d["latency_ms"], d["tokens_in"], d["tokens_out"]), (120, 1000, 50))
        self.assertEqual((d["cost_usd"], d["cost_source"]), (0.00005, "provider"))
        self.assertEqual((d["phase"], d["take"], d["job"]), ("take", 1, "j"))


if __name__ == "__main__":
    unittest.main()
