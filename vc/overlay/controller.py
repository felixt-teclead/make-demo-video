"""On-camera actions: the demo cursor's glide, press, ripple and click, typing and paste (spec Q-40..Q-79).

Transport-agnostic: `call(method, **params) -> dict` is any synchronous CDP call bound to the one filmed tab's
session (for example jev-ultrafast's `Browser.call`). The overlay never sends input events itself: the real click,
each typed character, Enter and a paste are callbacks that the jev executor performs (C-14). The overlay only draws.

Typical use from the jev wrapper (C-09: the executor knows the click point before it clicks):

    ov = Overlay(browser.call)
    ov.install()                               # once per take, and again after re-attaching to the tab
    log = ov.click(x, y, do_click=lambda: send_press_release(x, y), size=(w, h), recheck=where_is_target_now)
    ov.type_text("Urlaub 2026", insert=lambda ch: call("Input.insertText", text=ch), submit=False)
"""

import json
import time
from pathlib import Path

from . import timing as T

_SRC = Path(__file__).with_name("overlay.js").read_text(encoding="utf-8").strip()


class OverlayError(RuntimeError):
    pass


class Overlay:
    def __init__(self, call, *, viewport=(1920, 1080), start=None, log=None,
                 sleep=time.sleep, monotonic=time.monotonic, wall=time.time):
        self.call = call
        self.viewport = viewport
        if start is None:
            start = (round(viewport[0] * T.DEFAULT_START_FRACTION[0]), round(viewport[1] * T.DEFAULT_START_FRACTION[1]))
        self.x, self.y = float(start[0]), float(start[1])
        self._ripple = None      # {x, y, t0} epoch ms, carried into the next document (Q-43, Q-76)
        self._press = None
        self._script_id = None
        self.pending = None      # the click event between press() and the real click
        self._arrived = None     # monotonic time the cursor arrived (start of the 120 ms rest)
        self._glides = None
        self._log = log or (lambda event: None)
        self._sleep, self._mono, self._wall = sleep, monotonic, wall

    # ---------------------------------------------------------------------------------------------- plumbing
    def _state(self):
        return {"x": self.x, "y": self.y, "ripple": self._ripple, "press": self._press}

    def _source(self):
        return f"({_SRC})({json.dumps(self._state())})"

    def _register(self):
        """Keep the new-document script in step with the cursor, so a navigation paints it in place (Q-22, Q-43).
        The new script is added before the old one is removed, so no navigation can fall between them."""
        new = self.call("Page.addScriptToEvaluateOnNewDocument", source=self._source())["identifier"]
        old, self._script_id = self._script_id, new
        if old is not None:
            try:
                self.call("Page.removeScriptToEvaluateOnNewDocument", identifier=old)
            except Exception:
                pass

    def _api(self, expr):
        """Run `expr` against the overlay API in the current document (installing it there first if missing)."""
        src = f"(() => {{ const o = {self._source()}; return o ? o.{expr} : null; }})()"
        try:
            r = self.call("Runtime.evaluate", expression=src, returnByValue=True, awaitPromise=True)
        except Exception as e:  # the document is navigating; the new-document script covers it
            self._log({"type": "overlay_eval_failed", "t": self._wall(), "expr": expr[:40], "error": str(e)[:200]})
            return None
        if r.get("exceptionDetails"):
            self._log({"type": "overlay_eval_failed", "t": self._wall(), "expr": expr[:40],
                       "error": str(r["exceptionDetails"].get("text"))[:200]})
            return None
        return r.get("result", {}).get("value")

    def _sleep_until(self, deadline):
        while True:
            left = deadline - self._mono()
            if left <= 0:
                return
            self._sleep(min(left, 0.05) if left > 0.002 else left)

    # ---------------------------------------------------------------------------------------------- public
    def install(self):
        """Register for every future document and draw in the current one. Idempotent."""
        self._register()
        st = self._api("state()")
        self._log({"type": "overlay_installed", "t": self._wall(), "x": self.x, "y": self.y, "state": st})
        return st

    def state(self):
        return self._api("state()")

    def place(self, x, y):
        """Set the cursor without motion. Only off camera (before recording starts, Q-43)."""
        self.x, self.y = float(x), float(y)
        self._register()
        return self._api(f"place({self.x},{self.y})")

    def glide(self, x, y, size=None):
        """Glide in a straight line with sine easing, Fitts' law duration (Q-74). Blocks until the glide ends."""
        w, h = (size or (None, None))
        d = ((x - self.x) ** 2 + (y - self.y) ** 2) ** 0.5
        ms = T.glide_ms(d, w, h)
        self.x, self.y = float(x), float(y)
        self._register()
        t_start = self._mono()
        wall_start = self._wall()
        self._api(f"glide({self.x},{self.y},{ms})")   # returns when the glide has ended on screen
        self._sleep_until(t_start + ms / 1000.0)       # never shorter than planned (e.g. if the page was navigating)
        ev = {"type": "glide", "t": wall_start, "end": self._wall(), "x": self.x, "y": self.y,
              "distance": round(d, 1), "ms": round(ms, 1)}
        self._log(ev)
        return ev

    def arrive(self, x, y, *, size=None, recheck=None):
        """Glide to the target and follow it if it moved during the glide (C-09). Starts the 120 ms rest clock.
        Returns the glide events."""
        glides = [self.glide(x, y, size)]
        if recheck is not None:
            for _ in range(2):
                pt = recheck()
                if not pt or ((pt[0] - self.x) ** 2 + (pt[1] - self.y) ** 2) ** 0.5 <= 2:
                    break
                glides.append(self.glide(pt[0], pt[1], size))
        self._arrived = self._mono()
        self._glides = glides
        return glides

    def press(self, *, size=None, final_check=None):
        """120 ms after arriving: press + ripple (the logged click time), then return 120 ms after the ring's first
        frame, so the caller's real click follows at once (Q-74..Q-76).

        final_check(): optional; called at the end of the rest, right before anything is drawn. It returns the
        target's current point (x, y), or raises to abort (for example the target vanished): then nothing was drawn.
        If the target moved by more than 2 px, the cursor follows it and rests again (at most twice)."""
        glides = list(getattr(self, "_glides", None) or [])
        arrived = getattr(self, "_arrived", None)
        if arrived is None:
            arrived = self._mono()
        for _ in range(3):
            self._sleep_until(arrived + T.REST_BEFORE_PRESS_MS / 1000.0)
            if final_check is None:
                break
            pt = final_check()                                   # may raise: no ripple is drawn then
            if not pt or ((pt[0] - self.x) ** 2 + (pt[1] - self.y) ** 2) ** 0.5 <= 2:
                break
            glides.append(self.glide(pt[0], pt[1], size))
            arrived = self._mono()
        t0 = int(self._wall() * 1000)
        self._ripple = {"x": self.x, "y": self.y, "t0": t0}
        self._press = t0
        self._register()                     # a navigation caused by this click continues the ripple in place
        started = self._api(f"press({t0})")            # returns in the frame in which ring and press start
        t_ring = (started / 1000.0) if isinstance(started, (int, float)) and started > 0 else t0 / 1000.0
        # the real click follows the ring's first frame by 120 ms (wall clock of that frame, not of our call)
        self._sleep_until(self._mono() + (t_ring + T.CLICK_AFTER_PRESS_MS / 1000.0 - self._wall()))
        glide_t = glides[0]["t"] if glides else t_ring
        self.pending = {"type": "click", "t": t_ring, "glide_t": glide_t, "x": self.x, "y": self.y,
                        "click_t": self._wall(), "glides": glides}
        self._arrived = self._glides = None
        return dict(self.pending)

    def click(self, x, y, do_click, *, size=None, recheck=None, on_arrive=None, pressed_after_ms=500):
        """glide -> 120 ms rest -> press + ripple (the logged click time) -> real click 120 ms later (Q-74..Q-76).
        `arrive()` + `press()` in one call, for direct use without the jev hooks.

        do_click():   performs the real click (the jev executor's input). Called exactly once.
        size:         (w, h) of the target, for Fitts' W.
        recheck():    optional, returns the target's current point (x, y) or None; if the target moved by more than
                      2 px during the glide, the cursor follows it (C-09).
        on_arrive():  optional, called when the cursor has arrived (for example the executor's hover move).
        """
        self.arrive(x, y, size=size, recheck=recheck)
        if on_arrive is not None:
            on_arrive()
        pending = self.press(size=size)
        if do_click is None:        # the caller clicks right now (e.g. the jev executor) and then calls clicked()
            return pending
        result = do_click()
        ev = self.clicked(pressed_after_ms)
        return {**ev, "result": result}

    def abort_press(self, reason):
        """The real input was not sent after press() (the target vanished in the last 120 ms). The ring has shown
        without a click: return the orphan-ripple event for the log (the take's gate sees it as Q-44)."""
        ev = {"type": "orphan_ripple", "t": (self.pending or {}).get("t", self._wall()), "x": self.x, "y": self.y,
              "reason": str(reason)[:200]}
        self._ripple = None
        self._press = None
        self._register()
        self.pending = None
        self._log(ev)
        return ev

    def clicked(self, pressed_after_ms=500):
        """Call right after the real click was sent (only needed when click() ran with do_click=None).
        Arms the pressed state for a slow page (Q-48) and returns the click event (logged at the press start)."""
        ev = dict(self.pending or {"type": "click", "t": self._wall(), "x": self.x, "y": self.y,
                                   "click_t": self._wall()})
        self._api(f"clicked({int(pressed_after_ms)})")
        self._log(ev)
        self.pending = None
        return ev

    def release(self):
        """End the pressed state (Q-48) early, e.g. when the step's done check holds."""
        return self._api("release()")

    def type_text(self, text, insert, *, submit=False, press_enter=None, read_value=None, set_value=None):
        """Type one character at a time at a flat 37 ms (Q-79). Enter only if the step submits, 200 ms after the
        last character. If the field does not end up equal to the text, the whole value is set and logged."""
        plan = T.typing_schedule(text, submit=submit and press_enter is not None)
        s = T.normalize_typed_text(text)
        t0 = self._mono()
        wall0 = self._wall()
        for off, ch in plan:
            self._sleep_until(t0 + off / 1000.0)
            if ch == "\n":
                if read_value is not None and set_value is not None:
                    self._verify(s, read_value, set_value)
                press_enter()
            else:
                insert(ch)
        fixed = False
        if not (submit and press_enter) and read_value is not None and set_value is not None:
            fixed = self._verify(s, read_value, set_value)
        ev = {"type": "typing", "t": wall0, "end": self._wall(), "chars": len(s), "submit": bool(submit),
              "value_set_whole": fixed}
        self._log(ev)
        return ev

    def _verify(self, s, read_value, set_value):
        v = read_value()
        if v != s:
            set_value(s)
            self._log({"type": "type_mismatch_value_set", "t": self._wall(), "expected_len": len(s),
                       "got_len": len(v) if isinstance(v, str) else None})
            return True
        return False

    def paste(self, value, insert_all):
        """A copied value appears at once (Q-55): the field goes from empty to the full value in one frame,
        150 ms after the action starts."""
        t0 = self._mono()
        self._sleep_until(t0 + T.PASTE_MS / 1000.0)
        wall = self._wall()
        insert_all(value)
        ev = {"type": "paste", "t": wall, "end": self._wall(), "chars": len(value)}
        self._log(ev)
        return ev
