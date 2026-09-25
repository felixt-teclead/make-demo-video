"""Adapter from the overlay to the jev wrapper's hooks (M2: `vcjev.runner.Hooks`).

    from vc.overlay.jev_hooks import OnCamera
    cam = OnCamera(runner_tab, emit=post_event, stale=StalePage)   # emit(dict) -> e.g. POST /event (M1)
    cam.install()                                       # after the start URL loaded, before recording starts
    hooks = Hooks(**cam.hooks(), on_decision=...)

M2 calls, for every click, select and fill:
  before_input(action, point)   glide (Q-74), follow the target if it moved (C-09); again if M2 saw it move
  [M2: final point re-read, then one hover mouseMoved at the stable point]
  before_click(action, point)   rest 120 ms since arriving -> final point check (raises `stale` if the target is
                                gone: nothing drawn, no input) -> press + ripple (Q-75, Q-76) -> 120 ms -> return
  [M2's executor sends the real input at once]
  after_input(action, point, info)   emit the click event (M4/M6a fields), arm the pressed state (Q-48), then,
                                once the ripple has ended, park the real input pointer (below)
  input_aborted(action, point, reason)   the input failed after the press: log the orphan ripple
`type_text` performs the field's click and then types at 37 ms per character (Q-79), or pastes a copied value at
once (Q-55).

Every input is still sent by the jev executor's code path (C-14); the overlay itself only draws.

Pointer parking (Q-71): the real input pointer (CDP mouse) is what the page and Chrome see hovering. Left on the
clicked control it makes Chrome show the control's native title tooltip about 0.5 s later, which then sits on screen
for the whole hold (c2 of 2026-09-25: "Prozesse" mid-card for 2.3 s). So after every click (once the ripple has
ended) and right after a field's click (before typing), and once at `install()` (off camera, after the setup), the
real pointer moves to the park point, where it hovers nothing. The demo cursor sprite stays where it is (it is a
page overlay). The runner's hover move re-syncs the real pointer to the sprite's next click point before each press.
Knob: `park=` or env `VC_POINTER_PARK`: "outside" (default: just outside the viewport's top-left corner, (-1, -1)),
"x,y" (a fixed point on plain page background), or "off".
"""

import json
import os

from .controller import Overlay
from .timing import RIPPLE_MS, normalize_typed_text

PARK_DEFAULT = "outside"
OUTSIDE = (-1.0, -1.0)


def park_point(spec=None):
    """The real pointer's park point for a knob value ("outside" | "off" | "x,y"); None = do not park."""
    spec = (spec if spec is not None else os.environ.get("VC_POINTER_PARK") or PARK_DEFAULT).strip().lower()
    if spec in ("off", "0", "no", "false", "none"):
        return None
    if spec in ("outside", "1", "yes", "true", "on"):
        return OUTSIDE
    try:
        x, y = (float(v) for v in spec.split(","))
    except ValueError:
        raise ValueError(f"VC_POINTER_PARK: expected outside, off or x,y, got {spec!r}") from None
    return (x, y)

_VALUE = """(node => { const e = window.__jevFast?.nodes.get(node);
  return e ? ('value' in e ? String(e.value) : e.innerText) : null; })(%s)"""


class OnCamera:
    def __init__(self, tab, *, emit=None, log=None, viewport=None, start=None, copied=None, stale=RuntimeError,
                 park=None):
        """tab: M2's FilmedTab (needs .call(method, **params), .point(node), .js(expr)).
        emit: receives one dict per on-camera event (click, typing, paste) with epoch `t` and M4's fields.
        copied: values the demo copied earlier in the take; typing one of them becomes a paste (Q-55).
        park: where the real pointer rests after a click ("outside" | "off" | "x,y"; default env VC_POINTER_PARK,
        else "outside"), so no title tooltip or hover card appears (Q-71)."""
        self.tab = tab
        self.park_at = park_point(park)
        self._log = log or (lambda e: None)
        self._emit = emit or (lambda e: None)
        self.copied = set(copied or ())
        if viewport is None:
            try:
                viewport = tuple(tab.viewport())
            except Exception:
                viewport = (1920, 1080)
        self.ov = Overlay(tab.call, viewport=viewport, start=start, log=log)
        self._armed = None       # action id whose press/ripple already ran
        self._arrived = None     # action id the cursor has glided to (press still pending)
        self.stale = stale       # the exception M2 treats as "page changed, nothing executed"

    # ------------------------------------------------------------------------------------------------ setup
    def install(self):
        st = self.ov.install()
        self.park()                    # off camera: the setup's click may have left the pointer on a control
        return st

    def park(self, not_before=None):
        """Move the real input pointer to the park point (Q-71); `not_before` (epoch s): wait until then first.
        One mouseMoved, no path, invisible (the demo cursor is the overlay). A failure (page navigating) is logged."""
        if self.park_at is None:
            return None
        if not_before is not None:
            self.ov._sleep_until(self.ov._mono() + (not_before - self.ov._wall()))
        x, y = self.park_at
        try:
            self.tab.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, button="none", buttons=0,
                          pointerType="mouse")
        except Exception as e:  # noqa: BLE001 - a navigation in progress; the next hover re-syncs anyway
            self._log({"type": "pointer_park_failed", "t": self.ov._wall(), "error": str(e)[:200]})
            return None
        return (x, y)

    def hooks(self):
        return {"before_input": self.before_input, "before_click": self.before_click, "after_input": self.after_input,
                "input_aborted": self.input_aborted, "type_text": self.type_text}

    def mark_copied(self, value):
        self.copied.add(value)

    # ------------------------------------------------------------------------------------------------ hooks
    def _recheck(self, action):
        def where():
            p = self.tab.point(action["node"]) if "node" in action else None
            return (p["x"], p["y"]) if p else None
        return where

    def before_input(self, action, point):
        size = (point.get("w"), point.get("h"))
        if self._arrived == action.get("id") or self._armed == action.get("id"):
            # M2 saw the target move: follow it; the press comes only in before_click (never a second ripple)
            self.ov.glide(point["x"], point["y"], size)
            self.ov._arrived = self.ov._mono()
            return
        self.ov.arrive(point["x"], point["y"], size=size, recheck=self._recheck(action))
        self._arrived = action.get("id")

    def before_click(self, action, point):
        if self._armed == action.get("id"):
            return                              # already pressed for this action (never a second ripple, Q-44)
        size = (point.get("w"), point.get("h"))
        where = self._recheck(action)

        def final_check():
            pt = where() if "node" in action else (point["x"], point["y"])
            if pt is None:
                raise self.stale("target vanished before the press")
            return pt

        self.ov.press(size=size, final_check=final_check)
        self._armed = action.get("id")
        self._arrived = None

    def input_aborted(self, action, point, reason):
        if self._armed != action.get("id"):
            return
        ev = self.ov.abort_press(reason)
        self._armed = None
        self._emit({**ev, "label": action.get("own_label") or action.get("label")})

    def after_input(self, action, point, info):
        if action.get("kind") == "fill":
            return                      # type_text already emitted the click and the typing
        ev = self.ov.clicked()
        self._armed = None
        self._emit({**_public(ev), "label": action.get("own_label") or action.get("label"),
                    "kind": action.get("kind")})
        self.park(not_before=ev.get("t", self.ov._wall()) + RIPPLE_MS / 1000.0)   # after the ripple (Q-71)

    def type_text(self, tab, action, point, text, submit):
        x, y = self.ov.x, self.ov.y            # the point the press/ripple showed (the field's centre)
        for event in ("mousePressed", "mouseReleased"):
            tab.call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
        click = self.ov.clicked()
        self._armed = None
        self._emit({**_public(click), "label": action.get("own_label") or action.get("label"), "kind": "fill"})
        self.park()                      # the field has focus; typing needs no pointer (Q-71, no typing delay)
        _select_all(tab)
        node = action.get("node")

        def read():
            try:
                return tab.js(_VALUE % json.dumps(node))
            except Exception:
                return None

        def set_whole(v):
            _select_all(tab)
            tab.call("Input.insertText", text=v)

        def enter():
            for kind in ("keyDown", "keyUp"):
                tab.call("Input.dispatchKeyEvent", type=kind, key="Enter", code="Enter", windowsVirtualKeyCode=13,
                         nativeVirtualKeyCode=13, **({"text": "\r"} if kind == "keyDown" else {}))

        if text in self.copied:
            ev = self.ov.paste(text, lambda v: tab.call("Input.insertText", text=v))
            if submit:
                enter()
            fallback = False
        else:
            ev = self.ov.type_text(text, lambda ch: tab.call("Input.insertText", text=ch), submit=submit,
                                   press_enter=enter, read_value=read, set_value=set_whole)
            fallback = ev.get("value_set_whole", False)
        self._emit(_public(ev))
        return {"chars": len(normalize_typed_text(text)), "t_first": ev["t"], "t_last": ev["end"],
                "fallback_whole_value": fallback, "paste": ev["type"] == "paste"}


def _select_all(tab):
    tab.call("Input.dispatchKeyEvent", type="keyDown", key="a", code="KeyA", modifiers=2, commands=["selectAll"])
    tab.call("Input.dispatchKeyEvent", type="keyUp", key="a", code="KeyA", modifiers=2)


def _public(ev):
    return {k: v for k, v in ev.items() if k not in ("glides", "result")}
