"""The step execution contract (C-01 to C-03, C-08, C-09, C-14, C-31, F-28).

One step = one intended control. Each decision offers jev exactly that control
plus WAIT (DONE/BLOCKED are always added by the library). The step succeeds only
when its independent check holds after the step's input was executed (C-02);
jev's DONE never counts. Decisions per step are capped (C-03), transient
refusals are retried a bounded number of times.

The runner talks to the page only through the tab object (FilmedTab or a fake
for tests) and to jev only through `choose` (upstream `jev_ultrafast.model.choose`
by default), so its logic is unit-testable without a browser or a network.
"""

import math
import time
from dataclasses import dataclass, field

from .offer import DenyList, ResolveError, check_offer, describe, offered_set


class LoginRequired(RuntimeError):
    pass


class StepFailed(RuntimeError):
    def __init__(self, step, reason, result=None):
        super().__init__(f"step {step!r} failed: {reason}")
        self.step, self.reason, self.result = step, reason, result


@dataclass
class Settings:
    decisions_per_step: int = 6  # F-23 knob
    transient_retries: int = 2  # F-23 knob
    settle_s: float = 3.0  # how long the independent check is polled after an input
    ready_timeout_s: float = 10.0  # readiness wait: how long an absent target is re-observed before failing
    wait_poll_s: float = 0.4  # pause after a WAIT decision before observing again
    check_poll_s: float = 0.05
    point_tolerance_px: float = 2.0
    point_rechecks: int = 3


def _noop(*_a, **_k):
    return None


@dataclass
class Hooks:
    """On-camera hooks, called in this order for every click, select and fill (C-09, C-14):

    before_input(action, point)   glide the cursor to the point; called again if the point moved (no press yet)
    [the runner re-reads the point until it is stable, then hovers it: one mouseMoved at the click point]
    before_click(action, point)   right before the real input, after the final point check: 120 ms rest, press +
                                  ripple, 120 ms; may raise the stale-page error if the target vanished (then no
                                  ripple was drawn and no input is sent)
    [the executor sends the real input (or type_text for a fill)]
    after_input(action, point, info)   the input was sent: emit the click event, arm the pressed state
    input_aborted(action, point, reason)   the input was NOT sent after before_click (the target vanished in the
                                  last 120 ms): the overlay logs the orphan ripple
    """
    before_input: object = _noop  # (action, point) -> None; glide the cursor. Called again if the point moved.
    before_click: object = _noop  # (action, point) -> None; press + ripple right before the real input
    after_input: object = _noop  # (action, point, info) -> None; e.g. the click event
    input_aborted: object = _noop  # (action, point, reason) -> None
    type_text: object = None  # (tab, action, point, text, submit) -> dict; default: typing_hook.default_type_text
    on_decision: object = _noop  # (record) -> None


@dataclass
class StepResult:
    name: str
    ok: bool
    reason: str
    decisions: int = 0
    transient: int = 0
    executed: list = field(default_factory=list)
    check: dict = None
    jev_said_done: bool = False


def goal_for(step):
    if step.get("goal"):
        return step["goal"]
    t = step["target"]
    label = t.get("label") or t.get("label_contains") or t.get("label_regex")
    where = f" in {t['within']}" if t.get("within") else ""
    what = {"click": "Click", "type": "Type the given text into", "select": "Select"}[step["op"]]
    opt = f" option \"{t['option']}\"" if t.get("option") else ""
    return (f"{what} the \"{label}\" control{where}{opt}. It is the only offered control. "
            "Choose WAIT only if it is not ready yet.")


class StepRunner:
    def __init__(self, tab, log, models, *, deny_extra=(), settings=None, hooks=None, choose=None, stale=None,
                 sleep=time.sleep, clock=time.time):
        self.tab, self.log, self.models = tab, log, models
        self.deny_extra = tuple(deny_extra)
        self.s = settings or Settings()
        self.h = hooks or Hooks()
        if self.h.type_text is None:
            from .typing_hook import default_type_text
            self.h.type_text = default_type_text
        self.http_info = lambda: None  # per-decision HTTP breakdown (attempts, backoff), if the transport gives one
        if choose is None:
            from .upstream import ROUTE
            from .upstream import choose as upstream_choose
            choose = upstream_choose
            self.http_info = lambda: (ROUTE.last or {}).get("http")
        self.choose = choose
        if stale is None:
            from .upstream import StalePage as stale
        self.Stale = stale
        self.sleep, self.clock = sleep, clock

    # ---- helpers -------------------------------------------------------
    def _poll_check(self, check, seconds):
        deadline = self.clock() + seconds
        while True:
            r = self.tab.check(check)
            if r.get("ok") or self.clock() >= deadline:
                return r
            self.sleep(self.s.check_poll_s)

    def _stable_point(self, action):
        StalePage = self.Stale
        p = self.tab.point(action["node"])
        if p is None:
            raise StalePage("target is gone, disabled or covered")
        self.h.before_input(action, p)
        for _ in range(self.s.point_rechecks):
            q = self.tab.point(action["node"])
            if q is None:
                raise StalePage("target vanished during the glide")
            if math.dist((p["x"], p["y"]), (q["x"], q["y"])) <= self.s.point_tolerance_px:
                return q
            p = q
            self.h.before_input(action, p)  # element moved: glide again to the new point
        raise StalePage("target keeps moving")

    def _pre_press(self, action, point):
        """Hover the stable click point (the control's own hover state shows during the rest, as in cursor.png),
        then let the overlay press. Both happen after the last point check, right before the real input."""
        hover = getattr(self.tab, "hover", None)
        if hover is not None:
            hover(point)
        self.h.before_click(action, point)

    # ---- the contract ----------------------------------------------------
    def run_step(self, step):
        StalePage = self.Stale
        name, op = step["name"], step["op"]
        if op not in ("click", "type", "select"):
            raise ValueError(f"step {name!r}: jev runs click/type/select steps, not {op!r}")
        if not step.get("check"):
            raise ValueError(f"step {name!r}: no independent success check (C-02)")
        if op == "type" and not isinstance(step.get("text"), str):
            raise ValueError(f"step {name!r}: a type step needs its exact text (F-08)")
        deny = DenyList(self.deny_extra, allow=step.get("approved_write_labels", ()))
        res = StepResult(name, False, "")
        history, nochange = [], 0
        goal = goal_for(step)

        def fail(reason):
            res.ok, res.reason = False, reason
            self.log.event("step_end", step=name, ok=False, reason=reason, decisions=res.decisions)
            raise StepFailed(name, reason, res)

        def succeed(check, reason="check holds"):
            res.ok, res.reason, res.check = True, reason, check
            self.log.event("step_end", step=name, ok=True, reason=reason, decisions=res.decisions, check=check)
            return res

        self.log.event("step_start", step=name, op=op, target=step["target"], check=step["check"])
        ready_since = None
        while True:
            page = self.tab.observe(screenshot=False)
            if self.tab.login_form():
                self.log.event("login_required", step=name, url=page.get("url"))
                raise LoginRequired(f"step {name!r}: login required ({page.get('url')}); no decision made (C-31)")
            if res.executed:
                chk = self.tab.check(step["check"])
                if chk.get("ok"):
                    return succeed(chk)
            if res.decisions >= self.s.decisions_per_step:
                fail(f"reached the cap of {self.s.decisions_per_step} jev decisions without the check holding")
            actions = self.tab.enrich(page["actions"])
            try:
                offer = offered_set(actions, name, step["target"], op, deny)
            except ResolveError as e:
                if not res.executed and e.kind == "none":
                    # Readiness wait: the control is not rendered/enabled yet. Logged so the cutter
                    # can speed it up (Q-56); never a jev decision, never an input.
                    now = self.clock()
                    ready_since = ready_since or now
                    if now - ready_since < self.s.ready_timeout_s:
                        self.sleep(self.s.wait_poll_s)
                        continue
                    self.log.event("readiness_wait", step=name, start=round(ready_since, 3), end=round(now, 3),
                                   ok=False)
                if not res.executed:
                    self.log.event("resolve_failed", step=name, reason=str(e), candidates=e.candidates)
                    fail(f"before acting: {e}")
                chk = self._poll_check(step["check"], self.s.settle_s)
                if chk.get("ok"):
                    return succeed(chk)
                fail(f"target no longer resolves and the check does not hold: {e}")
            assert check_offer(offer), offer
            if ready_since is not None:
                self.log.event("readiness_wait", step=name, start=round(ready_since, 3), end=round(self.clock(), 3),
                               ok=True)
                ready_since = None
            self.log.check_budget()
            t0 = self.clock()
            try:
                decision = self.choose({**page, "actions": offer}, goal, history)
            except (RuntimeError, ValueError, KeyError) as e:  # provider error / invalid answer: nothing executed
                res.transient += 1
                self.log.event("transient", step=name, kind="model", reason=str(e)[:200], n=res.transient)
                if res.transient > self.s.transient_retries:
                    fail(f"jev call failed {res.transient} times: {e}")
                continue
            res.decisions += 1
            rec_extra = {"t_start": round(t0, 3), "t_end": round(self.clock(), 3), "url": page.get("url"),
                         "offer_ok": True}
            http = self.http_info()
            if http:
                rec_extra["http"] = http
            choice = decision["choice"]
            action = next((a for a in offer if a["id"] == choice), None)

            if choice in ("DONE", "BLOCKED"):
                rec = self.log.decision(step=name, n=res.decisions, offered=[describe(a) for a in offer],
                                        decision=decision, model_cfg=self.models["jev"], extra=rec_extra)
                self.h.on_decision(rec)
                if choice == "BLOCKED":
                    fail("jev reported BLOCKED")
                res.jev_said_done = True
                chk = self._poll_check(step["check"], self.s.settle_s) if res.executed else self.tab.check(step["check"])
                if res.executed and chk.get("ok"):
                    return succeed(chk, "check holds (jev also reported DONE)")
                fail("jev reported DONE but the independent check is false" if res.executed else
                     "jev reported DONE before the step's input was executed")

            if action["kind"] == "wait":
                rec = self.log.decision(step=name, n=res.decisions, offered=[describe(a) for a in offer],
                                        decision=decision, model_cfg=self.models["jev"], extra=rec_extra)
                self.h.on_decision(rec)
                try:
                    self.tab.act(action, page)
                except StalePage:
                    pass
                history.append({"action": action["label"], "kind": "wait", "text": None, "page_changed": None})
                self.sleep(self.s.wait_poll_s)
                continue

            # click / type / select on the one intended control
            pressed = None
            try:
                if action["kind"] == "fill":
                    if not self.tab.fresh(page):
                        raise StalePage("page changed before typing")
                    point = self._stable_point(action)
                    self._pre_press(action, point)
                    pressed = point
                    t_input = self.clock()
                    info = self.h.type_text(self.tab, action, point, step["text"], bool(step.get("submit")))
                else:
                    point = self._stable_point(action)
                    self._pre_press(action, point)
                    pressed = point
                    t_input = self.clock()
                    info = self.tab.act(action, page)
            except StalePage as e:
                if pressed is not None:          # press + ripple shown, input not sent: tell the overlay
                    self.h.input_aborted(action, pressed, str(e))
                rec_extra.update(executed=False, stale=str(e))
                rec = self.log.decision(step=name, n=res.decisions, offered=[describe(a) for a in offer],
                                        decision=decision, model_cfg=self.models["jev"], extra=rec_extra)
                self.h.on_decision(rec)
                res.transient += 1
                self.log.event("transient", step=name, kind="stale", reason=str(e), n=res.transient)
                if res.transient > self.s.transient_retries:
                    fail(f"page kept changing before input ({res.transient} stale refusals)")
                continue
            rec_extra.update(executed=True, click_point={"x": round(point["x"], 1), "y": round(point["y"], 1)},
                             t_input=round(t_input, 3), label=action.get("own_label"))
            rec = self.log.decision(step=name, n=res.decisions, offered=[describe(a) for a in offer],
                                    decision=decision, model_cfg=self.models["jev"], extra=rec_extra)
            self.h.on_decision(rec)
            self.h.after_input(action, point, info)
            res.executed.append({"id": action["id"], "kind": action["kind"], "label": action.get("own_label"),
                                 "point": point, "t_input": t_input})
            chk = self._poll_check(step["check"], self.s.settle_s)
            if chk.get("ok"):
                return succeed(chk)
            after = self.tab.observe(screenshot=False)
            changed = after.get("fingerprint") != page.get("fingerprint")
            history.append({"action": action["label"], "kind": action["kind"],
                            "text": step.get("text") if action["kind"] == "fill" else None, "page_changed": changed})
            nochange = 0 if changed else nochange + 1
            if nochange >= 3:
                fail("three inputs in a row did not change the page")

    def run(self, steps):
        return [self.run_step(s) for s in steps]
