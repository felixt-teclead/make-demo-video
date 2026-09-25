#!/usr/bin/env python3
"""Make the page area of the one Chrome window cover the whole virtual screen exactly (Q-80, C-15), run by vc-chrome
after start.

Measured live (2026-09-25, Chrome 154 on Xvfb, no window manager, which the image deliberately does not have): the
kiosk "fullscreen" window comes up 1919x1079 (a 1 px black edge on the recording), and Chrome clamps any window of the
screen's size to that. A normal window of any other size is placed exactly, but it shows the tab strip and toolbar
(87 px). So this helper leaves fullscreen and places a normal window with its toolbar ABOVE the screen: left 0,
top -(toolbar height), width W, height H + toolbar. The page area is then exactly the screen and no browser bar is on
camera. It verifies what the page sees: innerWidth x innerHeight == W x H, screenX == 0 and the page's top edge
(screenY + outerHeight - innerHeight) == 0; if Chrome still places the window short, it compensates by that amount and
verifies again. It only reads state and sets the browser window (no page input, C-14).

Prints one JSON line (also written to /tmp/vc-logs/window.json); exit 0 when the page area is exactly the screen.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vccdp import CDP, CDPError  # noqa: E402

W, H = (int(x) for x in os.environ.get("VC_SCREEN", "1920x1080").split("x"))


def measure(c):
    return c.evaluate("({w: innerWidth, h: innerHeight, ow: outerWidth, oh: outerHeight, x: screenX, y: screenY})")


def page_top(v):
    """The page area's top edge on the screen: the window's top plus the browser bars above the page."""
    return v["y"] + (v["oh"] - v["h"])


def exact(v):
    return (v["w"], v["h"], v["x"], page_top(v)) == (W, H, 0, 0)


def fit(c, attempts=4):
    tid = c.call("Target.getTargetInfo")["targetInfo"]["targetId"]
    win = c.call("Browser.getWindowForTarget", targetId=tid)
    wid, history = win["windowId"], [{"bounds": win["bounds"], "view": measure(c)}]
    if exact(history[-1]["view"]):
        return True, history
    if win["bounds"].get("windowState") != "normal":
        c.call("Browser.setWindowBounds", windowId=wid, bounds={"windowState": "normal"})
        time.sleep(0.3)
    extra_w = extra_h = 0                       # what Chrome shaved off our last request
    for _ in range(attempts):
        v = measure(c)
        if exact(v):
            return True, history
        bars_w, bars_h = v["ow"] - v["w"], v["oh"] - v["h"]
        want = {"left": 0, "top": -bars_h, "width": W + bars_w + extra_w, "height": H + bars_h + extra_h}
        c.call("Browser.setWindowBounds", windowId=wid, bounds=want)
        time.sleep(0.3)
        got = c.call("Browser.getWindowForTarget", targetId=tid)["bounds"]
        extra_w += want["width"] - got.get("width", want["width"])
        extra_h += want["height"] - got.get("height", want["height"])
        history.append({"bounds": got, "view": measure(c), "asked": want})
    return exact(history[-1]["view"]), history


def main():
    deadline = time.time() + float(os.environ.get("VC_WINDOW_FIT_TIMEOUT", "30"))
    last = None
    while time.time() < deadline:
        try:
            with CDP.page() as c:
                ok, hist = fit(c)
                out = {"ok": ok, "screen": [W, H], "steps": hist}
                break
        except (OSError, CDPError) as e:
            last = str(e)
            time.sleep(0.5)
    else:
        out = {"ok": False, "screen": [W, H], "error": last or "no DevTools answer"}
    line = json.dumps(out)
    print(line)
    try:
        os.makedirs("/tmp/vc-logs", exist_ok=True)
        with open("/tmp/vc-logs/window.json", "w") as f:
            f.write(line + "\n")
    except OSError:
        pass
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
