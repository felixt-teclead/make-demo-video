"""Long loading after a click (owner decision 2026-09-25, FX-31): the cover holds 2.0 s, the rest of the wait is cut
out and the held pre-click frame cross-fades into the settled view in the site-switch fade's shape.

    python3 -m unittest tests.cut.test_bridge          (from the repo root; needs ffmpeg; ~1.5 min)

Plan level (small synthetic raw, plan_covers + render): a long skeleton is bridged, the rendered fade has the house
weights 1/6..5/6, a load that never settles or has typing inside is left alone, a hold inside is never trimmed. Take level (synthetic
takes, the real cutter and the real gate): a 3.4 s skeleton and a 3.4 s black after a click are bridged and the gate
PASSes with the summary in its report; a black with no click before it still FAILs Q-20.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests", "fakes"), os.path.join(ROOT, "loop")]

from vc.cut import analyze as an_mod  # noqa: E402
from vc.cut import cover  # noqa: E402
from vc.cut.render import render  # noqa: E402
from vc.cut.tools import tool  # noqa: E402

RW, RH = 640, 360
CLICK_K = 20


def _page(fn):
    row = [bytes(fn(x, y) for x in range(RW)) for y in range(16)]
    g = b"".join(row[y % 16] for y in range(RH))
    return bytes(v for v in g for _ in range(3))


OLD = None


def _pages():
    global OLD
    if OLD is None:
        OLD = (_page(lambda x, y: 40 if (x // 6) % 2 else 200),
               _page(lambda x, y: 30 if ((x // 8) + (y // 8)) % 2 else 220))
    return OLD


def _skeleton(j):
    """Soft placeholder bars with a moving light band (changes every frame: no standstill)."""
    x0 = (j * 20) % (RW + 80) - 80
    return _page(lambda x, y: (120 + (y % 16) // 4) + (10 if x0 <= x < x0 + 80 else 0))


def _raw(tmp, n_load, n_new=20):
    old, new = _pages()
    frames = [old] * (CLICK_K + 4) + [_skeleton(j) for j in range(n_load)] + [new] * n_new
    raw = os.path.join(tmp, "raw.mp4")
    p = subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s",
                        f"{RW}x{RH}", "-r", "30", "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p",
                        raw], input=b"".join(frames), capture_output=True)
    assert p.returncode == 0, p.stderr[-500:]
    return raw, frames


def _plan(tmp, raw, K, guard=None):
    an = an_mod.analyze(raw, os.path.join(tmp, "an"))
    plan = {"fmap": list(range(K)), "speed": [1.0] * K, "badge": [0.0] * K, "kept": list(range(K)), "drops": []}
    cc = {"clicks": [{"k": CLICK_K, "x": RW / 2.0, "y": RH / 2.0, "t": CLICK_K / 30.0, "label": "x"}],
          "stops": [CLICK_K]}
    cover.plan_covers(raw, an, [plan], [cc], RW, RH, tmp, fade_n=6, guard=guard)
    return plan


class PlanLevel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vc-bridge-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_long_skeleton_is_cut_and_faded(self):
        raw, frames = _raw(self.tmp, 102)                    # 3.4 s of skeleton after the click
        K = len(frames)
        plan = _plan(self.tmp, raw, K)
        cv = plan["covers"]
        self.assertEqual(len(cv), 1, cv)
        cv = cv[0]
        self.assertEqual(cv["status"], "bridged", cv)
        self.assertEqual(cv["summary"], "loading 3.4 s: cut to 2.0 s + fade")
        k0 = CLICK_K + 4
        self.assertEqual(cv["frames"], [k0, k0 + 60])
        self.assertEqual(cv["removed_frames"], 42)
        self.assertEqual(cv["fade_frames"], [k0 + 60, k0 + 65])   # 5 blend frames, then live
        self.assertEqual(len(plan["fmap"]), K - 42)
        self.assertEqual(plan["fmap"][k0 + 60], k0 + 102)    # the settled view follows the held frames
        self.assertEqual(len(plan["speed"]), K - 42)
        self.assertEqual(plan["drops"][-1]["reason"], "loading bridged")
        self.assertEqual(plan["cover_render"][0]["fade"], {"k": k0 + 59, "n": 6})
        out = os.path.join(self.tmp, "clip.mp4")
        render(raw, [{"fmap": plan["fmap"], "badge": plan["badge"], "path": out, "covers": plan["cover_render"]}],
               RW, RH, os.path.join(self.tmp, "render"), 4.0)
        got = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", out, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, check=True).stdout
        n = RW * RH
        got = [got[i:i + n] for i in range(0, len(got), n)]
        self.assertEqual(len(got), K - 42)
        A, B = got[k0 + 59], got[k0 + 66]

        def weight(f):
            num = sum((x - a) * (b - a) for x, a, b in zip(f[::7], A[::7], B[::7]))
            den = sum((b - a) ** 2 for a, b in zip(A[::7], B[::7]))
            return num / den
        for k in (k0, k0 + 30, k0 + 59):                     # held: the pre-click frame, no skeleton
            self.assertLess(sum(abs(a - b) for a, b in zip(got[k][::7], got[CLICK_K - 1][::7])) / len(A[::7]), 2)
        for j in range(1, 6):                                # the house fade: (1 - j/6) held + j/6 settled
            self.assertAlmostEqual(weight(got[k0 + 59 + j]), j / 6.0, delta=0.03)
        self.assertAlmostEqual(weight(got[k0 + 65]), 1.0, delta=0.02)

    def test_load_that_never_settles_is_left_alone(self):
        raw, frames = _raw(self.tmp, 150, n_new=0)           # the clip ends while it still loads
        K = len(frames)
        plan = _plan(self.tmp, raw, K)
        self.assertEqual(len(plan["fmap"]), K)
        self.assertFalse([c for c in plan["covers"] if c["status"] in ("covered", "bridged")], plan["covers"])

    def test_hold_inside_the_load_is_not_trimmed(self):
        """A logged hold that runs into the frames to cut: the cut starts where it ends (Q-51, holds are never
        trimmed); the cover holds until then."""
        raw, frames = _raw(self.tmp, 102)
        K = len(frames)
        k0 = CLICK_K + 4
        plan = _plan(self.tmp, raw, K, guard=lambda a, b: (None, k0 + 75))
        cv = plan["covers"][0]
        self.assertEqual(cv["status"], "bridged", cv)
        self.assertEqual(cv["frames"], [k0, k0 + 75])
        self.assertEqual(cv["removed_frames"], 27)
        self.assertIn("holds are never trimmed", cv["reason"])

    def test_hold_until_settled_covers_without_cut(self):
        raw, frames = _raw(self.tmp, 102)
        K = len(frames)
        plan = _plan(self.tmp, raw, K, guard=lambda a, b: (None, K))
        cv = plan["covers"][0]
        self.assertEqual(cv["status"], "covered", cv)
        self.assertEqual(len(plan["fmap"]), K)
        self.assertIn("covered through the hold", cv["reason"])

    def test_typing_inside_the_load_is_not_cut(self):
        raw, frames = _raw(self.tmp, 102)
        K = len(frames)
        plan = _plan(self.tmp, raw, K, guard=lambda a, b: ("a typing lies in it", None))
        self.assertEqual(len(plan["fmap"]), K)
        self.assertEqual(plan["covers"][0]["status"], "skipped")
        self.assertIn("cannot be cut: a typing", plan["covers"][0]["reason"])


STEPS = [{"name": "one", "actions": [{"kind": "click", "label": "Open", "hold": 2.0}]},
         {"name": "two", "actions": [{"kind": "click", "label": "Liste", "hold": 3.0, "hold_kind": "final"}]}]
SPEC = {"steps": [{"name": "one", "actions": [{"op": "click"}], "hold": 2.0},
                  {"name": "two", "actions": [{"op": "click"}], "hold": 3.0, "hold_kind": "final"}],
        "start": {"hold": 1.2}, "length": [1, 60]}
RESULT = {"steps": [{"name": "one", "ok": True, "verified": True}, {"name": "two", "ok": True, "verified": True}]}


def take_and_gate(tmp, defects):
    import synth_video
    from vc.cut.cutter import cut
    from vcloop import gatefeed
    d = os.path.join(tmp, "take")
    synth_video.build_take(d, STEPS, defects=defects)
    out, rec = cut(d)
    evp = gatefeed.build(out, SPEC, RESULT)
    p = subprocess.run([os.path.join(ROOT, "bin", "vc-gate"), out, "--events", evp, "--json", "--out",
                        os.path.join(out, "qa"), "--workers", "4"], capture_output=True, text=True)
    res = json.loads(p.stdout)
    with open(os.path.join(out, "qa", "report.txt")) as f:
        report = f.read()
    return rec, res, report


class TakeLevel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vc-bridge-take-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _bridged(self, kind):
        rec, res, report = take_and_gate(self.tmp, {kind: "two"})
        cv = [c for c in rec["clips"] if c["name"] == "two"][0]["covers"]
        self.assertEqual([c["status"] for c in cv], ["bridged"], cv)
        self.assertEqual(cv[0]["summary"], "loading 3.4 s: cut to 2.0 s + fade")
        self.assertEqual(res["verdict"], "PASS", [(v["check"], v["reason"]) for v in res["violations"]])
        self.assertFalse(res.get("warnings"), res.get("warnings"))
        self.assertIn("loading 3.4 s: cut to 2.0 s + fade", report)

    def test_long_skeleton_after_click_passes(self):
        self._bridged("slow_skeleton")

    def test_long_black_after_click_passes(self):
        self._bridged("slow_black")

    def test_black_without_click_fails(self):
        rec, res, report = take_and_gate(self.tmp, {"black": "two"})
        self.assertEqual(res["verdict"], "FAIL")
        self.assertTrue([v for v in res["violations"] if v["check"] == "Q-20" and v["clip"] == "two"],
                        res["violations"])
        self.assertFalse([c for c in rec["clips"][1]["covers"] if c["status"] == "bridged"])


if __name__ == "__main__":
    unittest.main()
