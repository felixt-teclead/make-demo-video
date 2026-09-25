"""Live test of the camera's wheel pan (a canvas without a scroll container, moved by a transform on the wheel).

Run inside the recorder (its Chrome, under the browser lock), with tests/visual/site served on port 8766:
  bin/vc-lock wheel-pan bin/vc-env exec sh -c 'cd /opt/vc/tests/visual/site && (python3 -m http.server 8766 \
      >/dev/null 2>&1 &); sleep 1; python3 /opt/vc/tests/visual/wheel_pan.py http://127.0.0.1:8766/wheelpan.html'
"""
import sys

from vcjev.browser import FilmedTab

from vc.overlay import camera

t = FilmedTab("http://127.0.0.1:9222")
t.load_start_url(sys.argv[1])
action = {"op": "pan", "to": {"type": "text_visible", "value": "Ziel weit unten"}, "direction": "down", "seconds": 2.0,
          "pause_animations": True}
r = camera.move(t, action, check=lambda to: t.check(to).get("ok"), sleep=lambda s: None)
trace = t.js("TRACE")
anim = t.js("ANIM")
state_now = t.js("document.getAnimations()[0].playState")
t.close()
moves = [a - b for a, b in zip(trace, trace[1:]) if a != b]
checks = {
    "pan ok and in view": r.get("ok") is True,
    "wheel mode": r["event"].get("scroller") == "wheel",
    "target centred (clip centre 400, card centre 2350 -> 1950 px)": abs(abs(r["event"]["distance"]) - 1950) <= 2,
    "one direction": all(m > 0 for m in moves),
    "whole pixels after calibration": all(float(v).is_integer() for v in trace[-5:]),
    "no step above 40 px (Q-31)": max(moves) <= 40.5,
    "eased: first and last steps small": moves[0] <= 10 and moves[-1] <= 10,
}
# animations paused >= 10 frames (0.33 s, > the gate's 0.25 s episode pad) before the first and after the last move
first = next(i for i in range(1, len(trace)) if trace[i] != trace[i - 1])
last = max(i for i in range(1, len(trace)) if trace[i] != trace[i - 1])
checks["animations paused 10 frames before the first move"] = all(v is False for v in anim[first - 10:first])
checks["animations still paused 9 frames after the last move"] = all(v is False for v in anim[last:last + 9])
checks["animations resumed afterwards"] = state_now == "running"
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
print(r, "steps:", len(moves), "max:", max(moves))
sys.exit(0 if all(checks.values()) else 1)
