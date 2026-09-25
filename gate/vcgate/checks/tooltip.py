"""Q-71 no stray browser interface (S2): a tooltip-like box over the view.

QA definition change approved by the owner 2026-09-25 (self-improvement rule: detect what the parity viewer found):
the c2 take of 2026-09-25 06:48 showed Chrome's native "Prozesse" title tooltip mid-card for 2.3 s after the sidebar
click (the real input pointer stayed on the link). No other check saw it.

Invariant (Q-01): no small framed text box appears over the view as a unit and stays. A stray box is
- small and single-line: 14-64 px high, 20-640 px wide (1920-wide scale), at least as wide as high;
- framed: each of its four sides is a thin line (outermost 2 px) whose pixels changed when the box came or went and
  that is near-uniform in luma; the frame contrasts with the box's fill by >= 40 luma;
- text-bearing: the fill is mostly one luma, and 2-60 % of the inside differs from it by >= 60 luma (glyphs);
- a unit: it is found as one isolated change between two sampled frames (it appears, or it disappears, in one step),
  and the region's shapes before and after differ (edge correlation < 0.6; a control that only dims under a dialog
  backdrop keeps its shapes);
- not the clicked control's own state (focus ring, pressed state): a box containing a logged click point is skipped.
Frames are sampled every SAMPLE_EVERY-th frame at full resolution. A box on screen >= MIN_S (0.5 s) FAILs; shorter
boxes are only noted. One violation per box (Q-06). Pixel-only: runs without an event log too (then nothing is
skipped as a clicked control).
"""
import subprocess

import numpy as np

from .. import video

SAMPLE_EVERY = 3          # every 3rd frame (10 samples/s at 30 fps)
CHANGE_LUMA = 32          # a pixel changed between two samples
BLOCK = 8                 # change components are found on an 8 px block grid (8-connected)
MAX_BLOCKS = 4000         # a pair changing more blocks than this is a view change: no box search
H_MIN, H_MAX = 14, 64     # box height, px at 1920 wide
W_MIN, W_MAX = 20, 640    # box width
SIDE_SCORE = 0.75         # share of a side's pixels that changed and are near-uniform
SIDE_TOL = 32             # near-uniform: within 32 luma of the side's median
FRAME_CONTRAST = 40       # |frame luma - fill luma|
FILL_TOL = 20             # fill pixels: within 20 luma of the fill median
FILL_SHARE = 0.4          # at least 40 % of the inside is fill
GLYPH_LUMA = 60           # glyph pixels differ from the fill by >= 60
GLYPH_MIN, GLYPH_MAX = 0.02, 0.6
SAME_MAD = 8.0            # the box is still there while its region differs from the found box by < 8 luma
MIN_S = 0.5               # on screen >= 0.5 s: FAIL
CLICK_PAD = 12            # a box within 12 px of a logged click point is the clicked control's own state
SAME_STRUCTURE = 0.6      # edge correlation of the region before/after >= 0.6: the box only changed tone (a dim)


def _components(mask):
    """8-connected components of changed BLOCKxBLOCK blocks. Returns [(bx0, by0, bx1, by1)] in block units."""
    h, w = mask.shape
    bh, bw = h // BLOCK, w // BLOCK
    blk = mask[:bh * BLOCK, :bw * BLOCK].reshape(bh, BLOCK, bw, BLOCK).any(axis=(1, 3))
    ys, xs = np.nonzero(blk)
    if len(ys) == 0 or len(ys) > MAX_BLOCKS:
        return []
    todo = set(zip(ys.tolist(), xs.tolist()))
    out = []
    while todo:
        seed = todo.pop()
        stack, y0, y1, x0, x1 = [seed], seed[0], seed[0], seed[1], seed[1]
        while stack:
            y, x = stack.pop()
            y0, y1, x0, x1 = min(y0, y), max(y1, y), min(x0, x), max(x1, x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    n = (y + dy, x + dx)
                    if n in todo:
                        todo.remove(n)
                        stack.append(n)
        out.append((x0, y0, x1, y1))
    return out


def _side(vals, changed):
    """Line score of one side: share of pixels that changed and lie near the side's median luma."""
    if len(vals) < 4:
        return 0.0, 0.0
    med = float(np.median(vals))
    ok = changed & (np.abs(vals - med) <= SIDE_TOL)
    return float(ok.mean()), med


def box_test(img, diff, x0, y0, x1, y1, scale=1.0):
    """Is the changed region (inclusive pixel bbox) of `img` a framed, text-bearing single-line box?
    img: int16 gray frame showing the box; diff: bool mask of changed pixels. Returns dict or None."""
    w, h = x1 - x0 + 1, y1 - y0 + 1
    if not (H_MIN * scale <= h <= H_MAX * scale and W_MIN * scale <= w <= W_MAX * scale and w >= h):
        return None
    lines = {}
    cands = {"top": [y0, y0 + 1], "bottom": [y1, y1 - 1], "left": [x0, x0 + 1], "right": [x1, x1 - 1]}
    for side, idx in cands.items():
        best = (0.0, 0.0, None)
        for i in idx:
            if side in ("top", "bottom"):
                v, c = img[i, x0 + 2:x1 - 1], diff[i, x0 + 2:x1 - 1]
            else:
                v, c = img[y0 + 2:y1 - 1, i], diff[y0 + 2:y1 - 1, i]
            s, med = _side(v, c)
            if s > best[0]:
                best = (s, med, i)
        if best[0] < SIDE_SCORE:
            return None
        lines[side] = best
    frame_luma = float(np.median([lines[s][1] for s in lines]))
    iy0, iy1 = lines["top"][2] + 2, lines["bottom"][2] - 1
    ix0, ix1 = lines["left"][2] + 2, lines["right"][2] - 1
    if iy1 - iy0 < 4 or ix1 - ix0 < 8:
        return None
    inside = img[iy0:iy1, ix0:ix1]
    fill = float(np.median(inside))
    if abs(frame_luma - fill) < FRAME_CONTRAST:
        return None
    dev = np.abs(inside - fill)
    fill_share = float((dev <= FILL_TOL).mean())
    glyphs = float((dev >= GLYPH_LUMA).mean())
    if fill_share < FILL_SHARE or not (GLYPH_MIN <= glyphs <= GLYPH_MAX):
        return None
    return {"x0": int(x0), "y0": int(y0), "x1": int(x1), "y1": int(y1), "frame_luma": round(frame_luma),
            "fill_luma": round(fill), "glyphs": round(glyphs, 3),
            "sides": {s: round(lines[s][0], 2) for s in lines}}


def _edge_corr(p, q):
    """Normalised correlation of the gradient magnitudes of two equal-size int16 crops."""
    def g(a):
        return np.abs(np.diff(a, axis=1))[:-1, :].astype(np.float64) + np.abs(np.diff(a, axis=0))[:, :-1]
    ep, eq = g(p), g(q)
    den = float(np.sqrt((ep * ep).sum() * (eq * eq).sum()))
    return float((ep * eq).sum()) / den if den > 0 else 1.0


def boxes_between(a, b, scale=1.0):
    """Boxes that appear (shown in b) or disappear (shown in a) between two gray int16 frames.
    Returns [(box, 'appear'|'disappear')]."""
    diff = np.abs(b - a) >= CHANGE_LUMA
    out = []
    for bx0, by0, bx1, by1 in _components(diff):
        sub = diff[by0 * BLOCK:(by1 + 1) * BLOCK, bx0 * BLOCK:(bx1 + 1) * BLOCK]
        ys, xs = np.nonzero(sub)
        if len(ys) == 0:
            continue
        x0, x1 = bx0 * BLOCK + int(xs.min()), bx0 * BLOCK + int(xs.max())
        y0, y1 = by0 * BLOCK + int(ys.min()), by0 * BLOCK + int(ys.max())
        for img, how in ((b, "appear"), (a, "disappear")):
            box = box_test(img, diff, x0, y0, x1, y1, scale)
            if box is not None:
                sl = (slice(y0, y1 + 1), slice(x0, x1 + 1))
                corr = _edge_corr(a[sl], b[sl])
                if corr >= SAME_STRUCTURE:
                    break           # same shapes before and after: the view dimmed or lit, nothing came or went
                box["edge_corr"] = round(corr, 2)
                out.append((box, how))
                break
    return out


def find_boxes(frames, scale=1.0, clicks=()):
    """frames: sequence of 2-D uint8 gray frames (the samples, in order). clicks: [(x, y)] logged click points.
    Returns [{box, how, start, end}] with start/end as sample indices (inclusive), one per box (Q-06)."""
    fr = [f.astype(np.int16) for f in frames]
    pad = CLICK_PAD * scale
    found = []
    for k in range(1, len(fr)):
        for box, how in boxes_between(fr[k - 1], fr[k], scale):
            if any(box["x0"] - pad <= x <= box["x1"] + pad and box["y0"] - pad <= y <= box["y1"] + pad
                   for x, y in clicks):
                continue
            ref_i = k if how == "appear" else k - 1
            found.append((box, how, ref_i))
    return _episodes(fr, found)


def _episodes(fr, found):
    out = []
    for box, how, ref_i in found:
        sl = (slice(box["y0"], box["y1"] + 1), slice(box["x0"], box["x1"] + 1))
        ref = fr[ref_i][sl]

        def same(i):
            return float(np.abs(fr[i][sl] - ref).mean()) < SAME_MAD
        s = ref_i
        while s - 1 >= 0 and same(s - 1):
            s -= 1
        e = ref_i
        while e + 1 < len(fr) and same(e + 1):
            e += 1
        dup = next((o for o in out if o["start"] == s and o["end"] == e and _overlap(o["box"], box)), None)
        if dup is None:
            out.append({"box": box, "how": how, "start": s, "end": e})
        elif how == "appear":
            dup["how"] = "appear and disappear"
    return out


def _overlap(p, q):
    return not (p["x1"] < q["x0"] or q["x1"] < p["x0"] or p["y1"] < q["y0"] or q["y1"] < p["y0"])


def iter_samples(path, name, width, height, every=SAMPLE_EVERY):
    """Every `every`-th decoded frame, full resolution gray. Yields (frame_index, (h, w) uint8)."""
    cmd = [video.ffmpeg(), "-v", "error", "-nostdin", "-i", path, "-map", "0:v:0",
           "-vf", f"select=not(mod(n\\,{int(every)})),format=gray", "-fps_mode", "passthrough",
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    fb = width * height
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=fb * 2)
    i = 0
    try:
        while True:
            buf = p.stdout.read(fb)
            if len(buf) < fb:
                break
            yield i * every, np.frombuffer(buf, np.uint8).reshape(height, width)
            i += 1
    finally:
        p.stdout.close()
        p.wait()


_HOW = {"appear": "appears", "disappear": "disappears", "appear and disappear": "appears and disappears"}


def run_clip(ctx):
    clicks = [(float(c["x"]), float(c["y"])) for c in (ctx.ev.clicks if ctx.ev else [])
              if c.get("x") is not None and c.get("y") is not None]
    # pass 1 (bounded memory): candidate boxes between consecutive samples
    idx, prev, found, keep = [], None, [], {}
    pad = CLICK_PAD * ctx.scale
    for k, (fi, f) in enumerate(iter_samples(ctx.path, ctx.name, ctx.width, ctx.height)):
        cur = f.astype(np.int16)
        idx.append(fi)
        if prev is not None:
            for box, how in boxes_between(prev, cur, ctx.scale):
                if any(box["x0"] - pad <= x <= box["x1"] + pad and box["y0"] - pad <= y <= box["y1"] + pad
                       for x, y in clicks):
                    continue
                found.append((box, how, k if how == "appear" else k - 1))
        prev = cur
    if not found:
        return
    # pass 2: only the candidate regions of every sample, to measure how long each box stays
    regions = {}
    for k, (fi, f) in enumerate(iter_samples(ctx.path, ctx.name, ctx.width, ctx.height)):
        for j, (box, _how, _r) in enumerate(found):
            regions.setdefault(j, []).append(f[box["y0"]:box["y1"] + 1, box["x0"]:box["x1"] + 1].astype(np.int16))
    eps = []
    for j, (box, how, ref_i) in enumerate(found):
        seq = regions.get(j, [])
        if ref_i >= len(seq):
            continue
        sub = {**box, "x0": 0, "y0": 0, "x1": box["x1"] - box["x0"], "y1": box["y1"] - box["y0"]}
        for ep in _episodes(seq, [(sub, how, ref_i)]):
            ep["box"] = box
            if not any(e["start"] == ep["start"] and e["end"] == ep["end"] and _overlap(e["box"], box) for e in eps):
                eps.append(ep)
    step = SAMPLE_EVERY / ctx.fps
    lines = []
    for ep in eps:
        t0 = idx[ep["start"]] / ctx.fps
        t1 = min(ctx.duration, idx[ep["end"]] / ctx.fps + step)
        dur = t1 - t0
        b = ep["box"]
        where = f"{b['x1'] - b['x0'] + 1}x{b['y1'] - b['y0'] + 1} px at ({b['x0']},{b['y0']})"
        lines.append(f"{t0:.2f}-{t1:.2f} s {where}")
        if dur >= MIN_S:
            ctx.add("Q-71", t0, t1,
                    f"stray tooltip-like box {where} on screen {t0:.2f}-{t1:.2f} s ({dur:.1f} s): a small framed "
                    f"text box over the view that {_HOW[ep['how']]} as a unit (a browser title tooltip or bubble)",
                    severity="FAIL", box=b)
    ctx.note("Q-71", "tooltip-like boxes: " + "; ".join(lines))
