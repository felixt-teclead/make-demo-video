"""Q-23 skeleton cover (vc/cut/cover.py): detection, the cover until the view is settled, the drawn ripple (its
fit, its seam, its shrinking to nothing) and the rendered cover.

    python3 -m unittest tests.cut.test_cover          (from the repo root; needs ffmpeg for the render test)

Synthetic frames exercise each rule; real-frame fixtures (zlib): 6 frames of c1-four-angles t3 around the click
"Urlaubsantrag" (480x270 grey), 10 delivered frames of c2-find-and-read 070742 t1 around "Prozesse" (480x270 grey:
skeletons, then frames just under Q-23's 'gone' line that are still not the settled view), and raw frames 67-84 of
the same click (rgb24, 113x113 around the click: the live ripple over the old view and over the loading page).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from vc.cut import analyze as an_mod  # noqa: E402
from vc.cut import cover  # noqa: E402
from vc.cut.render import render  # noqa: E402
from vc.cut.tools import tool  # noqa: E402

W, H = 192, 108          # synthetic frames: scale 0.1 of the 1920-wide video (hole 7.2 px, exclusion 11 px)
SC = W / 1920.0
CX, CY = 96.0, 54.0
DATA = os.path.join(HERE, "data")
PRIVATE = ("real-frame fixture {} missing: cut from screen recordings of a private app, not in the public repo; "
           "put the files in tests/cut/data/ to run this test")


def needs(name):
    return unittest.skipUnless(os.path.exists(os.path.join(DATA, name)), PRIVATE.format(name))


def frame(fn):
    return bytes(fn(x, y) for y in range(H) for x in range(W))


def stripes(x, y):        # the old view: sharp vertical stripes
    return 40 if (x // 3) % 2 else 200


def checker(x, y):        # the settled new view: a sharp checker
    return 30 if ((x // 4) + (y // 4)) % 2 else 220


def bars(x, y):           # a skeleton: soft grey placeholder bars, no sharp edges
    return 120 + (y % 20) // 4


def near_click(fn, keep, r=20):
    """fn outside the disc of radius r around the click, `keep` inside (the part of the page the click does not
    change: the ring at the hole edge then shows no seam)."""
    return lambda x, y: keep(x, y) if (x - CX) ** 2 + (y - CY) ** 2 < r * r else fn(x, y)


class Detect(unittest.TestCase):
    def window(self, n_skel=5, skel=bars, ring_same=True, lead=2):
        pre = frame(stripes)
        wrap = (lambda fn: near_click(fn, stripes)) if ring_same else (lambda fn: fn)
        return ([pre] + [pre] * lead + [frame(wrap(skel))] * n_skel + [frame(wrap(checker))] * 3)

    def test_skeleton_run_is_found_and_covered(self):
        fr = self.window()
        d = cover.decide(fr, W, H, CX, CY, SC)
        self.assertEqual(d["status"], "covered", d)
        self.assertEqual((d["k0"], d["k1"]), (3, 8))           # old view (2 frames) -> 5 skeletons -> settled
        self.assertEqual(d["skeleton_frames"], 5)

    def test_cover_continues_until_the_view_is_settled(self):
        """skeletons, then frames that are no skeleton by Q-23 (old view not quite gone: a faint shell, and a half-
        painted frame) but not the settled view either: the cover runs on to the first frame from which the view
        stays settled (the gate would count the faint shell as skeleton once its encode lifts it over 10 luma)."""
        pre = frame(stripes)
        sk = frame(bars)
        shell = bytes(int(0.9 * p + 0.1 * q) for p, q in zip(pre, sk))          # mean |f - pre| just under 10
        half = frame(lambda x, y: checker(x, y) if y < 20 else bars(x, y))       # a sixth painted
        new = frame(checker)
        fr = [pre, sk, sk, shell, half, shell, new, new, new]
        r = cover.detect(fr, W, H, CX, CY, SC)
        self.assertEqual(r["skeleton"], [1, 2, 4])
        self.assertEqual(r["settled_from"], 6)
        d = cover.decide(fr, W, H, CX, CY, SC)
        self.assertEqual(d["status"], "covered", d)
        self.assertEqual((d["k0"], d["k1"], d["skeleton_end"]), (1, 6, 5))

    def test_settled_view_that_flickers_back_is_covered_on(self):
        """settled, a skeleton again, settled: the cover ends only where the view stays settled."""
        pre, sk, new = frame(stripes), frame(bars), frame(checker)
        d = cover.decide([pre, sk, new, sk, new, new], W, H, CX, CY, SC)
        self.assertEqual((d["k0"], d["k1"]), (1, 4))

    def test_cross_fade_is_no_skeleton(self):
        pre, new = frame(stripes), frame(checker)
        fades = [bytes(int(round((1 - a) * p + a * q)) for p, q in zip(pre, new)) for a in (0.2, 0.4, 0.6, 0.8)]
        d = cover.decide([pre] + fades + [new] * 2, W, H, CX, CY, SC)
        self.assertEqual(d["status"], "none", d)

    def test_no_view_change(self):
        pre = frame(stripes)
        d = cover.decide([pre] * 6, W, H, CX, CY, SC)
        self.assertEqual(d["status"], "none")
        self.assertIn("no view change", d["reason"])

    def test_longer_than_two_seconds_is_skipped(self):
        fr = self.window(n_skel=8)
        d = cover.decide(fr, W, H, CX, CY, SC, max_frames=7)
        self.assertEqual(d["status"], "skipped")
        self.assertIn("longer than", d["reason"])

    def test_unknown_click_position_is_skipped(self):
        d = cover.decide(self.window(), W, H, None, None, SC)
        self.assertEqual(d["status"], "skipped")

    def test_half_painted_frames_between_skeletons_are_covered(self):
        """skeleton, a half-painted frame that is still a skeleton by Q-23 (few sharp edges), skeleton -> one run."""
        half = near_click(lambda x, y: checker(x, y) if y < 12 else bars(x, y), stripes)
        pre = frame(stripes)
        sk = frame(near_click(bars, stripes))
        fr = [pre, sk, frame(half), sk, frame(near_click(checker, stripes)), frame(near_click(checker, stripes))]
        d = cover.decide(fr, W, H, CX, CY, SC)
        self.assertEqual(d["status"], "covered", d)
        self.assertEqual((d["k0"], d["k1"]), (1, 4))


@needs("c1t3-urlaubsantrag-480x270.gray.z")
class RealFrames(unittest.TestCase):
    """c1-four-angles t3, click "Urlaubsantrag" at (1096.5, 168.25): raw frames 61 (pre-click), 64 (ripple, old view),
    67 and 74 (skeleton), 77 (settled list), 134 (the window's settled view); 480x270 grey."""

    @classmethod
    def setUpClass(cls):
        w, h = 480, 270
        with open(os.path.join(HERE, "data", "c1t3-urlaubsantrag-480x270.gray.z"), "rb") as f:
            data = zlib.decompress(f.read())
        cls.frames = [data[i:i + w * h] for i in range(0, len(data), w * h)]
        cls.w, cls.h, cls.sc = w, h, w / 1920.0

    def test_skeleton_frames_detected_and_covered(self):
        d = cover.decide(self.frames, self.w, self.h, 1096.5 * self.sc, 168.25 * self.sc, self.sc)
        self.assertEqual(d["skeleton"], [2, 3])
        self.assertEqual(d["status"], "covered", d)
        self.assertEqual((d["k0"], d["k1"]), (2, 4))


def _load(name):
    with open(os.path.join(HERE, "data", name), "rb") as f:
        return zlib.decompress(f.read())


@needs("c2t1-prozesse-settle-480x270.gray.z")
class RealSettle(unittest.TestCase):
    """c2-find-and-read 070742 t1, click "Prozesse" at (143.5, 202): delivered frames 66 (pre-click), 72, 80, 94
    (skeleton), 95, 99, 103 (the empty shell, mean |f - pre| 9.8: no skeleton by Q-23 on the raw, but the gate
    counted them on the encoded clip: 9 frames, FAIL), 104, 120, 139 (the settled list)."""

    def test_cover_runs_until_the_list_is_settled(self):
        w, h = 480, 270
        data = _load("c2t1-prozesse-settle-480x270.gray.z")
        frames = [data[i:i + w * h] for i in range(0, len(data), w * h)]
        sc = w / 1920.0
        d = cover.decide(frames, w, h, 143.5 * sc, 202 * sc, sc)
        self.assertEqual(d["status"], "covered", d)
        self.assertEqual(d["skeleton"], [1, 2, 3])
        self.assertEqual(d["skeleton_end"], 4)            # the old cover stopped here (after delivered frame 94)
        self.assertEqual(d["k1"], 7)                      # now: through 95..103, up to the settled frame 104


@needs("c2t1-prozesse-ripple-113.rgb.z")
class RealRipple(unittest.TestCase):
    """The same click on the raw frames 67 (pre-click) to 84, rgb24 113x113 around the click: the ripple starts over
    the old view (68-72); from 73 on the loading page shows through its fill and around it."""
    X, Y, T = 143.5, 202.0, 2.2614
    BOX = (87, 146, 113, 113)

    @classmethod
    def setUpClass(cls):
        data = _load("c2t1-prozesse-ripple-113.rgb.z")
        n = 113 * 113 * 3
        cls.crops = [data[i:i + n] for i in range(0, len(data), n)]
        cls.times = [j / 30.0 for j in range(67, 67 + len(cls.crops))]
        cls.fit = cover.fit_ripple(cls.crops[1:], cls.times[1:], cls.BOX, cls.X, cls.Y, cls.T)
        cls.mask = cover.arrow_mask(cls.BOX, cls.X, cls.Y)
        cls.start, cls.refine_seam = cover.refine_start(cls.crops[0], cls.crops[1:6], cls.times[1:6], cls.BOX,
                                                        cls.X, cls.Y, cls.T + (cls.fit["delta_s"] or 0), cls.mask)

    def draw(self, j):
        return cover.draw_ripple(self.crops[0], self.BOX, self.X, self.Y, self.times[j] - self.start, self.mask)

    def test_ring_found_through_the_loading_page(self):
        self.assertTrue(self.fit["found"], self.fit)
        self.assertLessEqual(abs(self.fit["delta_s"]), 0.034, self.fit)      # starts within a frame of the click
        self.assertLess(self.fit["mismatch"], 0.45, self.fit)

    def test_drawn_ripple_joins_the_live_one(self):
        """On the live frames over the old view (69-72) the drawn ripple (start fitted on the ring, refined on these
        frames) matches the live one: seam <= 16 luma on each, the last one (the cover's seam) well under it."""
        self.assertLess(self.refine_seam, 12)
        for j in range(2, 6):
            s, op = cover.ripple_phase(self.times[j] - self.start)
            self.assertGreater(op, 0, j)
            seam = cover.ripple_seam(self.crops[j], self.draw(j), self.BOX, self.X, self.Y, s)
            self.assertLessEqual(seam, cover.SEAM_MAX, (j, seam))

    def test_drawn_area_shrinks_with_the_ripple_to_nothing(self):
        """Per frame only the ripple's current extent differs from the pre-click frame (no fixed hole); the extent
        follows the ring and is 0 once the ripple is gone (tau >= 520 ms); no live pixel is used."""
        pre = self.crops[0]
        dist = cover._dists(self.BOX, self.X, self.Y)
        ext = []
        for j in range(1, len(self.crops)):
            tau = self.times[j] - self.start
            s, op = cover.ripple_phase(tau)
            out = self.draw(j)
            changed = [dist[i // 3] for i in range(0, len(out), 3) if out[i:i + 3] != pre[i:i + 3]]
            ext.append(max(changed) if changed else 0.0)
            if op <= 0:
                self.assertEqual(out, pre, j)
            else:
                r = cover.RIPPLE_D / 2 * s + cover.RIPPLE_HALO * s + 3 * cover.RIPPLE_GLOW_SIGMA * s + 1
                self.assertLessEqual(ext[-1], r, (j, ext[-1], r))
            self.assertEqual(out, cover.draw_ripple(pre, self.BOX, self.X, self.Y, tau, self.mask))
        self.assertEqual(ext[-1], 0.0)                        # raw 84: the ripple is over
        self.assertTrue(any(e > 0 for e in ext))

    def test_no_loading_page_in_the_drawn_frames(self):
        """Over the loading page (73-78) the live disc differs from the old view by the loading content; the drawn
        frame differs from the pre-click frame only by the ripple's own blue tint (b >= r everywhere it changed)."""
        pre = self.crops[0]
        for j in range(6, 12):
            out = self.draw(j)
            for i in range(0, len(out), 3):
                if out[i:i + 3] != pre[i:i + 3] and not self.mask[i // 3]:
                    self.assertGreaterEqual(out[i + 2] - pre[i + 2], out[i] - pre[i] - 1, (j, i))

    def test_pointer_kept_from_the_pre_click_frame(self):
        out = self.draw(8)
        pre = self.crops[0]
        for i, m in enumerate(self.mask):
            if m:
                self.assertEqual(out[3 * i:3 * i + 3], pre[3 * i:3 * i + 3])


class Rendered(unittest.TestCase):
    """End to end on a small synthetic raw (1280x720 rgb, the ripple drawn by the model with its start 2 frames after
    the logged click, the loading page visible through it): plan_covers fits the ripple and decides, render draws.
    Covered frames show the pre-click frame with the drawn ripple (no loading page inside or around it); the first
    frame after the cover is live."""

    def test_cover_render(self):
        rw, rh = 1280, 720
        px = rw / 1920.0
        cx, cy = 640.0, 360.0
        tmp = tempfile.mkdtemp(prefix="vc-cover-")
        try:
            def page(fn):
                row = [bytes(fn(x, y) for x in range(rw)) for y in range(16)]
                g = b"".join(row[y % 16] for y in range(rh))
                return bytes(v for v in g for _ in range(3))             # grey rgb24
            old = page(lambda x, y: 40 if (x // 6) % 2 else 200)
            new = page(lambda x, y: 30 if ((x // 8) + (y // 8)) % 2 else 220)
            skel = page(lambda x, y: 120 + (y % 16) // 4)
            box = cover.ripple_box(cx, cy, rw, rh, px)
            x0, y0, bw, bh = box
            t_click, start = 20 / 30.0, 22 / 30.0

            def with_ripple(base, j):
                crop = b"".join(base[((y0 + y) * rw + x0) * 3:((y0 + y) * rw + x0 + bw) * 3] for y in range(bh))
                d = cover.draw_ripple(crop, box, cx, cy, j / 30.0 - start, None, px)
                f = bytearray(base)
                for y in range(bh):
                    f[((y0 + y) * rw + x0) * 3:((y0 + y) * rw + x0 + bw) * 3] = d[y * bw * 3:(y + 1) * bw * 3]
                return bytes(f)
            frames = ([with_ripple(old, j) for j in range(24)] + [with_ripple(skel, j) for j in range(24, 34)]
                      + [with_ripple(new, j) for j in range(34, 44)])
            raw = os.path.join(tmp, "raw.mp4")
            p = subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s",
                                f"{rw}x{rh}", "-r", "30", "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt",
                                "yuv444p", raw], input=b"".join(frames), capture_output=True)
            self.assertEqual(p.returncode, 0, p.stderr[-500:])
            an = an_mod.analyze(raw, os.path.join(tmp, "an"))
            K = len(frames)
            plan = {"fmap": list(range(K))}
            cc = {"clicks": [{"k": 20, "x": cx, "y": cy, "t": t_click, "label": "x"}], "stops": [20]}
            cover.plan_covers(raw, an, [plan], [cc], rw, rh, tmp)
            rec = plan["covers"]
            self.assertEqual(len(rec), 1, rec)
            self.assertEqual(rec[0]["status"], "covered", rec[0])
            self.assertEqual(rec[0]["frames"], [24, 34])
            rp = rec[0]["ripple"]
            self.assertTrue(rp["found"], rp)
            self.assertLessEqual(abs(rp["delta_s"] - 2 / 30.0), 0.02, rp)
            self.assertGreater(rp["drawn_frames"], 0)
            out = os.path.join(tmp, "clip.mp4")
            render(raw, [{"fmap": plan["fmap"], "badge": [0.0] * K, "path": out, "covers": plan["cover_render"]}],
                   rw, rh, os.path.join(tmp, "render"), 4.0)
            got = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", out, "-f", "rawvideo", "-pix_fmt", "rgb24",
                                  "-"], capture_output=True, check=True).stdout
            n = rw * rh * 3
            got = [got[i:i + n] for i in range(0, len(got), n)]
            self.assertEqual(len(got), K)

            def mad(a, b, region):
                s_ = c = 0
                for y in range(0, rh, 3):
                    for x in range(0, rw, 3):
                        inb = x0 <= x < x0 + bw and y0 <= y < y0 + bh
                        if inb == region:
                            i = (y * rw + x) * 3 + 1
                            s_ += abs(a[i] - b[i])
                            c += 1
                return s_ / c
            for k in (24, 28, 33):   # covered: the old view with the drawn ripple, inside the box too
                want = with_ripple(old, k)
                self.assertLess(mad(got[k], want, False), 3, k)
                self.assertLess(mad(got[k], want, True), 4, k)
                self.assertGreater(mad(got[k], frames[k], True), 10, k)      # the live box shows the loading page
            for k in (23, 34, 35):   # not covered
                self.assertLess(mad(got[k], frames[k], False), 3, k)
            self.assertGreater(mad(got[34], frames[23], False), 20)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
