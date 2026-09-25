"""Film the overlay on the local test page (test rig only; runs inside the throwaway container).

Stands in for the jev executor: the real click is CDP mousePressed/mouseReleased at the element centre, and typing is
Input.insertText per character, exactly the input path upstream jev-ultrafast uses.
"""

import json
import subprocess
import sys
import time

sys.path.insert(0, "/repo")
from cdp_min import CDP  # noqa: E402

from vc.overlay.jev_hooks import OnCamera  # noqa: E402

OUT = sys.argv[1] if len(sys.argv) > 1 else "/out"
events = []
c = CDP()
c.call("Page.enable")


def wait_load(url_part, timeout=10):
    t = time.time() + timeout
    while time.time() < t:
        try:
            if url_part in c.eval("location.href") and c.eval("document.readyState") == "complete":
                return
        except Exception:
            pass
        time.sleep(0.05)
    raise TimeoutError(url_part)


def center(sel):
    r = c.eval(f"(()=>{{const r=document.querySelector('{sel}').getBoundingClientRect();"
               "return [r.x+r.width/2,r.y+r.height/2,r.width,r.height]})()")
    return r


RECTS = "JSON.stringify([...document.querySelectorAll('body *')].map(e=>{const r=e.getBoundingClientRect();" \
        "return [e.tagName,e.id,r.x,r.y,r.width,r.height]}))"

c.call("Page.navigate", url="http://localhost:8001/p1.html")
wait_load("p1.html")
time.sleep(0.3)
before = c.eval(RECTS)
scroll_before = c.eval("[document.documentElement.scrollWidth,document.documentElement.scrollHeight]")



class ShimTab:
    """Stands in for M2's FilmedTab: node ids are CSS selectors registered in window.__jevFast.nodes."""
    call = staticmethod(c.call)

    def js(self, expr, await_promise=False):
        return c.eval(expr)

    def viewport(self):
        return (1920, 1080)

    def point(self, node):
        x, y, w, h = center(node)
        return {"x": x, "y": y, "w": w, "h": h}


tab = ShimTab()
emitted = []
cam = OnCamera(tab, emit=emitted.append, log=events.append)
ov = cam.ov
ov.install()
time.sleep(0.2)
after = c.eval(RECTS)
checks = {
    "layout_unchanged": before == after,
    "scroll_size_unchanged": scroll_before == c.eval(
        "[document.documentElement.scrollWidth,document.documentElement.scrollHeight]"),
    "hit_test_ignores_overlay": c.eval("document.elementFromPoint(1382,842).id"),
    "viewport": c.eval("[innerWidth, innerHeight]"),
    "body_children_unchanged": c.eval("document.body.querySelectorAll('vc-overlay').length") == 0,
}


def click(sel, kind="click"):
    """The M2 runner's order: point -> before_input (glide) -> re-read point -> before_click (press, ripple) -> input."""
    c.eval(f"(window.__jevFast ||= {{nodes: new Map()}}).nodes.set({json.dumps(sel)}, document.querySelector({json.dumps(sel)}))")
    action = {"id": sel + str(time.time()), "node": sel, "kind": kind, "own_label": sel}
    p = tab.point(sel)
    cam.before_input(action, p)
    q = tab.point(sel)
    cam.before_click(action, q)
    if kind == "fill":
        return action, q
    for t in ("mousePressed", "mouseReleased"):
        c.call("Input.dispatchMouseEvent", type=t, x=q["x"], y=q["y"], button="left", clickCount=1)
    cam.after_input(action, q, None)
    return emitted[-1]


rec = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "x11grab", "-draw_mouse", "0", "-framerate", "30",
                        "-video_size", "1920x1080", "-i", ":99", "-copyts", "-c:v", "libx264", "-preset", "ultrafast",
                        "-crf", "18", "-pix_fmt", "yuv420p", f"{OUT}/take.mkv"], stdin=subprocess.PIPE)
time.sleep(1.2)
rec_wall0 = time.time()  # rough; analysis uses frame-relative measures
marks = {}

for name in ("#t1", "#t2", "#t3", "#t4"):
    marks[name] = click(name)
    time.sleep(1.2)

action, q = click("#typ", kind="fill")
cam.type_text(tab, action, q, "Hallo  Demo-Welt, 1234 äöü", False)
marks["type"] = emitted[-1]
time.sleep(1.0)
cam.mark_copied("PR-2026-00417")
action, q = click("#pst", kind="fill")
cam.type_text(tab, action, q, "PR-2026-00417", False)
marks["paste"] = emitted[-1]
time.sleep(1.0)
marks["slow"] = click("#slow")
time.sleep(3.0)
marks["busy"] = click("#busy")
time.sleep(3.0)
marks["dlg"] = click("#dlg")
time.sleep(1.0)
marks["close"] = click("#close")
time.sleep(1.0)
page_log_p1 = c.eval("LOG")
marks["go"] = click("#go")
wait_load("p2.html")
time.sleep(1.5)
marks["back"] = click("#back")
wait_load("p1.html")
time.sleep(1.5)
state_after_nav = ov.state()
rec.communicate(b"q")

json.dump({"events": events, "emitted": emitted, "marks": marks, "page_log": page_log_p1, "checks": checks,
           "state_after_nav": state_after_nav, "rec_wall0": rec_wall0}, open(f"{OUT}/log.json", "w"), indent=1,
          default=str)
print(json.dumps(checks))
