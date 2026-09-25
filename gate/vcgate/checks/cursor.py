"""Exactly one cursor (Q-40), no teleport across a splice (Q-42), continuity across clip boundaries (Q-43).

Pointer finder: a pointer is an arrow-shaped sprite: a bright arrow fill enclosed by a darker outline. It is found by
masked normalised cross-correlation (NCC) of the frame's luma with an arrow template (fill = 1, outline band = 0), so
it does not depend on the page colour behind it. The search runs at half resolution over the whole frame (FFT), and
every candidate is refined at native resolution in a small window.

Pointer shape: an arrow has a tail (the stem below the arrow head, left and right of it the page shows through). A
candidate whose tail region shows no contrast (a triangle, e.g. the grey or orange connector arrowheads of a diagram)
matches the arrow head only; it is a pointer only when it also has the demo cursor's look (then it counts, so a real
cursor that is partly hidden is never lost). v1-parity section 3: the static connector arrowheads of the app's step
cards were read as second pointers (c3).

Demo-cursor look (Q-73): white fill, neutral dark outline, soft black shadow. A pointer-shaped sprite whose fill is not
neutral white, or whose outline is not dark (a coloured or bright outline, e.g. the coral pointer with the orange edge
glow of the Q-90 fixture), does not have the demo cursor's look. The colour around the outline is the page, not the
sprite: the demo cursor parked on an orange active button (c1 "Swimlanes", v1-parity section 3) keeps its look. Q-40
fails when a sampled frame shows more than one pointer, or a pointer without the demo cursor's look.
"""
import numpy as np

# Arrow fill of the demo cursor at 1:1 (from acceptance/visual-baselines/cursor.png, luma >= 150); tip = (0, 0).
ARROW = [
    "##..........",
    "###.........",
    "####........",
    "#####.......",
    "######......",
    "#######.....",
    "#########...",
    "##########..",
    "###########.",
    "############",
    "########....",
    "#######.....",
    "###...##....",
    "##.....#....",
    ".......##...",
    ".......##...",
    "........#...",
]
PAD = 3
NCC_MIN = 0.70            # a pointer: NCC with the arrow template >= 0.70 at native resolution
NCC_COARSE = 0.45         # half-resolution candidate threshold
MAX_CAND = 12
SAMPLE_S = 0.5            # Q-40 sampling interval (OD-21)
JUMP_PX = 24              # Q-42 / Q-43 limit (px at 1920 wide)
GLIDE_PX = 2              # Q-42: gliding when the tip moved > 2 px per frame just before
BOUNDARY_FRAMES = 6       # Q-43
PLAIN_STD = 6.0           # Q-42: luma std-dev below which an area is plain page (an arrow there would be found)
NEAR_PX = 64              # local search window (half size) around the last known tip
FILL_CHROMA_MAX = 30      # the demo cursor's fill is neutral white
FILL_LUMA_MIN = 200
GLOW_MAX = 20             # (measured and reported only) chroma just around the arrow minus the page further out
OUTLINE_LUMA_MAX = 140    # the demo cursor's outline is dark (#111): median luma of the 1 px band around the fill
                          # (measured on the delivered takes and fixtures: demo cursor <= 114 on any page, the coral
                          # second pointer of the Q-90 fixtures >= 172)
TAIL_MIN = 0.10           # an arrow has a tail: tail contrast >= 0.10 x the head's contrast (demo cursor >= 0.14,
                          # triangle connector arrowheads <= 0.04; measured on c1-c3 and the Q-90 fixtures)


def _template():
    fill = np.array([[c == "#" for c in row] for row in ARROW], bool)
    h, w = fill.shape
    f = np.zeros((h + 2 * PAD, w + 2 * PAD), bool)
    f[PAD:PAD + h, PAD:PAD + w] = fill
    d = f.copy()
    for _ in range(2):
        e = d.copy()
        e[1:, :] |= d[:-1, :]
        e[:-1, :] |= d[1:, :]
        e[:, 1:] |= d[:, :-1]
        e[:, :-1] |= d[:, 1:]
        d = e
    return f, d & ~f


FILL, OUTLINE = _template()
MASK = FILL | OUTLINE
TH, TW = MASK.shape


def _shape_masks():
    rows = np.arange(TH)[:, None] + np.zeros((1, TW), int)
    cols = np.zeros((TH, 1), int) + np.arange(TW)[None, :]
    d = FILL.copy()
    d[1:, :] |= FILL[:-1, :]
    d[:-1, :] |= FILL[1:, :]
    d[:, 1:] |= FILL[:, :-1]
    d[:, :-1] |= FILL[:, 1:]
    ring1 = d & ~FILL                                           # the 1 px outline band
    tail = FILL & (rows >= PAD + 14)                            # the stem below the head
    beside = (rows >= PAD + 13) & (rows <= PAD + 16) & (((cols >= PAD + 4) & (cols <= PAD + 5)) |
                                                        ((cols >= PAD + 10) & (cols <= PAD + 11)))  # page left/right
    head = FILL & (rows < PAD + 10)
    head_out = OUTLINE & (rows < PAD + 10)
    return ring1, tail, beside, head, head_out


RING1, TAIL, BESIDE, HEAD, HEAD_OUT = _shape_masks()


def _luma(rgb):
    """8-bit luma (BT.601 weights, integer maths) as float32."""
    y = rgb[..., 0].astype(np.uint16) * 77
    y += rgb[..., 1].astype(np.uint16) * 150
    y += rgb[..., 2].astype(np.uint16) * 29
    return (y >> 8).astype(np.float32)


_TFFT = {}


def _fast(n):
    """Smallest 2^a 3^b 5^c >= n (fast FFT size)."""
    best = None
    p2 = 1
    while p2 < 2 * n:
        p3 = p2
        while p3 < 2 * n:
            p5 = p3
            while p5 < 2 * n:
                if p5 >= n and (best is None or p5 < best):
                    best = p5
                p5 *= 5
            p3 *= 3
        p2 *= 2
    return best


def _ncc_map(g, fill, mask):
    """Masked NCC of template (fill=1, outline=0 inside mask) at every top-left position. g: float32 (H, W)."""
    H, W = g.shape
    th, tw = mask.shape
    t = fill.astype(np.float32)
    m = mask.astype(np.float32)
    n = m.sum()
    tz = (t - (t * m).sum() / n) * m
    tnorm = np.sqrt((tz * tz).sum())
    sh = (_fast(H + th), _fast(W + tw))
    key = (sh, th, tw)
    if key not in _TFFT:
        _TFFT[key] = (np.fft.rfft2(tz[::-1, ::-1], s=sh), np.fft.rfft2(m[::-1, ::-1], s=sh))
    Tz, M = _TFFT[key]
    G = np.fft.rfft2(g, s=sh)
    G2 = np.fft.rfft2(g * g, s=sh)
    num = np.fft.irfft2(G * Tz, s=sh)[th - 1:H, tw - 1:W]
    s1 = np.fft.irfft2(G * M, s=sh)[th - 1:H, tw - 1:W]
    s2 = np.fft.irfft2(G2 * M, s=sh)[th - 1:H, tw - 1:W]
    var = s2 - s1 * s1 / n
    ok = var > n * 60.0          # std-dev under the mask of at least ~7.7 luma: flat areas hold no pointer
    out = np.zeros_like(num)
    out[ok] = num[ok] / (np.sqrt(var[ok]) * tnorm)
    return out


_TF = FILL[MASK].astype(np.float32)
_TF = _TF - _TF.mean()
_TFN = float(np.sqrt((_TF * _TF).sum()))


def _ncc_at(g, y, x):
    patch = g[y:y + TH, x:x + TW]
    if patch.shape != MASK.shape:
        return -1.0
    p = patch[MASK]
    p = p - p.mean()
    pp = float((p * p).sum())
    if pp / p.size < 60.0:
        return -1.0
    return float((p * _TF).sum() / (np.sqrt(pp) * _TFN))


_HALF = None


def _half_template():
    global _HALF
    if _HALF is None:
        f = FILL.astype(np.float32)
        m = MASK.astype(np.float32)
        h2, w2 = TH // 2 * 2, TW // 2 * 2
        f2 = f[:h2, :w2].reshape(h2 // 2, 2, w2 // 2, 2).mean(axis=(1, 3))
        m2 = m[:h2, :w2].reshape(h2 // 2, 2, w2 // 2, 2).mean(axis=(1, 3)) > 0
        _HALF = (f2, m2)
    return _HALF


def find_pointers(rgb):
    """All arrow-shaped sprites in one RGB frame. Returns list of dict(x, y, score), (x, y) = the tip."""
    g = _luma(rgb)
    H, W = g.shape
    h2, w2 = H // 2 * 2, W // 2 * 2
    gh = g[:h2, :w2].reshape(h2 // 2, 2, w2 // 2, 2).mean(axis=(1, 3))
    f2, m2 = _half_template()
    c = _ncc_map(gh, f2, m2)
    cands = []
    top = np.flatnonzero(c >= NCC_COARSE)
    top = top[np.argsort(c.flat[top])[::-1]]
    for idx in top:
        if c.flat[idx] < NCC_COARSE or len(cands) >= MAX_CAND:
            break
        y, x = divmod(int(idx), c.shape[1])
        if any(abs(y - cy) <= 6 and abs(x - cx) <= 6 for cy, cx in cands):
            continue
        cands.append((y, x))
    out = []
    for cy, cx in cands:
        best = (-1.0, 0, 0)
        for y in range(max(0, cy * 2 - 3), min(H - TH, cy * 2 + 3) + 1):
            for x in range(max(0, cx * 2 - 3), min(W - TW, cx * 2 + 3) + 1):
                s = _ncc_at(g, y, x)
                if s > best[0]:
                    best = (s, y, x)
        if best[0] >= NCC_MIN:
            s, y, x = best
            tip = (x + PAD, y + PAD)
            if not any(abs(tip[0] - o["x"]) <= 4 and abs(tip[1] - o["y"]) <= 4 for o in out):
                out.append({"x": int(tip[0]), "y": int(tip[1]), "score": round(s, 3)})
    return out


def look(rgb, p):
    """The look of a pointer with tip (x, y): fill colour, and coloured glow around it."""
    H, W = rgb.shape[:2]
    y0, x0 = p["y"] - PAD, p["x"] - PAD
    if y0 < 0 or x0 < 0 or y0 + TH > H or x0 + TW > W:
        return {"fill_chroma": 0.0, "fill_luma": 255.0, "glow": 0.0, "edge": True}
    patch = rgb[y0:y0 + TH, x0:x0 + TW].astype(np.int16)
    e = FILL.copy()
    e[1:, :] &= FILL[:-1, :]
    e[:-1, :] &= FILL[1:, :]
    e[:, 1:] &= FILL[:, :-1]
    e[:, :-1] &= FILL[:, 1:]
    fp = patch[e]
    fchroma = float(np.median(fp.max(-1) - fp.min(-1)))
    fluma = float(np.median(_luma(fp)))
    R = 14
    ya, yb = max(p["y"] - R, 0), min(p["y"] + 17 + R, H)
    xa, xb = max(p["x"] - R, 0), min(p["x"] + 12 + R, W)
    region = rgb[ya:yb, xa:xb].astype(np.int16)
    chroma = region.max(-1) - region.min(-1)
    yy, xx = np.mgrid[ya:yb, xa:xb]
    dx = np.maximum(np.maximum(p["x"] - xx, xx - (p["x"] + 12)), 0)
    dy = np.maximum(np.maximum(p["y"] - yy, yy - (p["y"] + 17)), 0)
    dist = np.maximum(dx, dy)
    near = chroma[(dist >= 1) & (dist <= 5)]
    far = chroma[(dist >= 10) & (dist <= 14)]
    glow = float(np.median(near) - np.median(far)) if near.size and far.size else 0.0
    g = _luma(patch.astype(np.uint8))
    head_c = float(np.median(g[HEAD]) - np.median(g[HEAD_OUT]))
    tail_c = float(np.median(g[TAIL]) - np.median(g[BESIDE]))
    return {"fill_chroma": fchroma, "fill_luma": fluma, "glow": glow, "edge": False,
            "outline_luma": float(np.median(g[RING1])),
            "tail": round(tail_c / head_c, 3) if head_c > 0 else 0.0}


def is_demo_look(lk):
    if lk.get("edge"):
        return True
    return lk["fill_chroma"] <= FILL_CHROMA_MAX and lk["fill_luma"] >= FILL_LUMA_MIN and \
        lk["outline_luma"] <= OUTLINE_LUMA_MAX


def is_pointer(p):
    """Pointer-shaped: it has an arrow's tail, or it is the demo cursor (whose tail may be hidden)."""
    return p["demo"] or p["look"].get("edge") or p["look"].get("tail", 1.0) >= TAIL_MIN


def _classify(rgb, ps):
    out = []
    for p in ps:
        p["look"] = look(rgb, p)
        p["demo"] = is_demo_look(p["look"])
        if is_pointer(p):
            out.append(p)
    return out


def analyse_frame(rgb):
    return _classify(rgb, find_pointers(rgb))


def q40_bad(ps):
    """Q-40 on one sampled frame's pointers: more than one pointer, or a pointer without the demo cursor's look."""
    return len(ps) > 1 or any(not p["demo"] for p in ps)


def analyse_near(rgb, tip, r=NEAR_PX):
    """Pointers within r px of a known tip (a crop search; much cheaper than the full frame)."""
    H, W = rgb.shape[:2]
    x0, y0 = max(int(tip[0]) - r, 0), max(int(tip[1]) - r, 0)
    x1, y1 = min(int(tip[0]) + r, W), min(int(tip[1]) + r, H)
    crop = rgb[y0:y1, x0:x1]
    if crop.shape[0] < 2 * TH or crop.shape[1] < 2 * TW:
        return analyse_frame(rgb)
    ps = find_pointers(crop)
    for p in ps:
        p["x"] += x0
        p["y"] += y0
    return _classify(rgb, ps)


# ---------------------------------------------------------------- checks

class Collector:
    """Finds pointers in the frames the cursor checks need, fed frame by frame (in order) from a shared decode."""

    def __init__(self, ctx):
        n = ctx.n
        self.fps = ctx.fps
        step = max(1, int(round(SAMPLE_S * ctx.fps)))
        self.sample = set(range(0, n, step)) | ({n - 1} if n else set())
        self.head = list(range(min(BOUNDARY_FRAMES, n)))
        self.tail = list(range(max(0, n - BOUNDARY_FRAMES), n))
        self.splice_frames = {}
        for s in ctx.splices:
            fs = _first_after(s, ctx.fps)
            self.splice_frames[s] = [f for f in range(fs - 4, fs + 3) if 0 <= f < n]
        self.need = self.sample | set(self.head) | set(self.tail) | \
            {f for fl in self.splice_frames.values() for f in fl}
        self.after_frames = {f for s_, fl in self.splice_frames.items() for f in fl
                             if f >= _first_after(s_, ctx.fps)}
        self.kept = {}
        self.found = {}
        self.last = None   # last demo-cursor tip seen: search near it first (cheap), else the whole frame

    def feed(self, i, frame, planar=True):
        if i not in self.need:
            return
        rgb = np.stack([frame[2], frame[0], frame[1]], -1) if planar else frame
        ps = None
        if i not in self.sample and self.last is not None:
            ps = analyse_near(rgb, self.last)
            if not any(p["demo"] for p in ps):
                ps = None
        if ps is None:
            ps = analyse_frame(rgb)
        self.found[i] = ps
        if i in self.after_frames:
            self.kept[i] = rgb
        demo = [p for p in ps if p["demo"]]
        if demo:
            self.last = (demo[0]["x"], demo[0]["y"])

    def complete(self):
        return self.need.issubset(self.found.keys())


def run_clip(ctx):
    n = ctx.n
    if n == 0:
        return
    coll = getattr(ctx, "cursor_collector", None)
    if coll is None or not coll.complete():
        coll = Collector(ctx)
        for i, rgb in _decode_selected(ctx, sorted(coll.need)):
            coll.feed(i, rgb, planar=False)
    sample, head, tail = coll.sample, coll.head, coll.tail
    splice_frames, kept, found = coll.splice_frames, coll.kept, coll.found

    def tip(i):
        ps = [p for p in found.get(i, []) if p["demo"]]
        if not ps:
            return None
        ps.sort(key=lambda p: -p["score"])
        return [ps[0]["x"], ps[0]["y"]]

    ctx.tips_head = [tip(i) for i in head]
    ctx.tips_tail = [tip(i) for i in tail]

    # Q-40: one violation per clip, listing the frames and positions
    bad = [(i, found[i]) for i in sorted(sample) if i in found and q40_bad(found[i])]
    if bad:
        desc = []
        for i, ps in bad[:5]:
            desc.append(f"{i / ctx.fps:.2f} s " + ", ".join(
                f"({p['x']}, {p['y']}){'' if p['demo'] else ' not the demo cursor look'}" for p in ps))
        ctx.add("Q-40", bad[0][0] / ctx.fps, bad[-1][0] / ctx.fps,
                f"{'a second pointer' if any(len(ps) > 1 for _, ps in bad) else 'a pointer that is not the demo cursor'}"
                f" in {len(bad)} of {len(sample)} sampled frames: {'; '.join(desc)}{'; ...' if len(bad) > 5 else ''}",
                frames=[i for i, _ in bad],
                positions=[[[p["x"], p["y"], p["demo"]] for p in ps] for _, ps in bad])

    # Q-42: splices
    lim = JUMP_PX * ctx.scale
    for s, fl in splice_frames.items():
        fs = _first_after(s, ctx.fps)
        before = [(f, tip(f)) for f in fl if f < fs and tip(f)]
        after = [(f, tip(f)) for f in fl if f >= fs and tip(f)]
        if before and not after:
            # found before, not found after: if the spot where it must be shows plain page, it is gone from there
            fb, tb = before[-1]
            plain = [f for f in fl if f >= fs and f in kept and _plain_at(kept[f], tb, lim)]
            if plain and len(plain) == len([f for f in fl if f >= fs]):
                ctx.add("Q-42", s, None,
                        f"cursor gone across the splice: tip ({tb[0]}, {tb[1]}) at {fb / ctx.fps:.2f} s, and no "
                        f"cursor within {lim:.0f} px of that spot after the splice (plain page there); it teleported "
                        f"or vanished", distance=None)
                continue
        if not before or not after:
            ctx.add("Q-42", s, None, "cursor not found around the splice (4 frames before to 2 after)",
                    severity="WARN")
            continue
        fb, tb = before[-1]
        fa, ta = after[0]
        pred = tb
        if len(before) >= 2:
            fp, tp = before[-2]
            vx, vy = (tb[0] - tp[0]) / (fb - fp), (tb[1] - tp[1]) / (fb - fp)
            if (vx * vx + vy * vy) ** 0.5 > GLIDE_PX:
                pred = (tb[0] + vx * (fa - fb), tb[1] + vy * (fa - fb))
        d = ((ta[0] - pred[0]) ** 2 + (ta[1] - pred[1]) ** 2) ** 0.5
        if d > lim:
            ctx.add("Q-42", s, None,
                    f"cursor teleports {d:.0f} px across the splice: tip ({tb[0]}, {tb[1]}) at {fb / ctx.fps:.2f} s "
                    f"-> ({ta[0]}, {ta[1]}) at {fa / ctx.fps:.2f} s (limit {lim:.0f} px)", distance=round(d, 1))
        else:
            ctx.note("Q-42", f"splice at {s:.2f} s: cursor moves {d:.0f} px")


def _plain_at(rgb, tip, r):
    """True when the area where the arrow would be drawn (tip within r px) is plain page: no cursor can hide there."""
    H, W = rgb.shape[:2]
    x0, y0 = max(int(tip[0] - r), 0), max(int(tip[1] - r), 0)
    x1, y1 = min(int(tip[0] + r + TW), W), min(int(tip[1] + r + TH), H)
    g = _luma(rgb[y0:y1, x0:x1])
    return g.size > 0 and float(g.std()) < PLAIN_STD


def _first_after(t, fps):
    """First frame that shows the footage after a splice at clip time t."""
    import math
    return int(math.ceil(t * fps - 1e-6))


def _decode_selected(ctx, frames):
    """Decode the clip once and return only the listed frames (ffmpeg select filter; key frames are sparse, so
    seeking per sample would decode far more)."""
    import subprocess
    from .. import video
    frames = sorted(set(int(f) for f in frames if 0 <= f < ctx.n))
    if not frames:
        return []
    expr = "+".join(f"eq(n\\,{f})" for f in frames)
    cmd = [video.ffmpeg(), "-v", "error", "-nostdin", "-i", ctx.path, "-map", "0:v:0",
           "-vf", f"select={expr}", "-vsync", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    fb = ctx.width * ctx.height * 3
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:     # a partial decode would scan fewer frames and look clean (Q-02)
        raise video.Unmeasurable(ctx.name, "decode failed (" + (p.stderr.decode(errors="replace").strip()
                                                                  .splitlines() or ["?"])[-1] + ")")
    n = len(p.stdout) // fb
    arr = np.frombuffer(p.stdout, np.uint8, n * fb).reshape(n, ctx.height, ctx.width, 3)
    return list(zip(frames[:n], arr))


def _runs(frames):
    """Group sorted frame numbers into (start, count) runs, merging gaps of up to 3 frames."""
    runs = []
    for f in frames:
        if runs and f - (runs[-1][0] + runs[-1][1]) <= 3:
            runs[-1][1] = f - runs[-1][0] + 1
        else:
            runs.append([f, 1])
    return runs


def run_take(take, results):
    """Q-43: the tip in the last 6 frames of clip N vs the first 6 frames of clip N+1, in play order."""
    out = []
    by_name = {r["name"]: r for r in results if r and "abort" not in r}
    order = [c["name"] for c in take.clips if c["name"] in by_name]
    for a, b in zip(order, order[1:]):
        ra, rb = by_name[a], by_name[b]
        ta = [t for t in ra.get("tips_tail", []) if t]
        tb = [t for t in rb.get("tips_head", []) if t]
        if not ta or not tb:
            # Q-43: the cursor exists from the first frame and never pops in (Gate: FAIL)
            out.append({"check": "Q-43", "severity": "FAIL", "clip": b, "t0": 0.0, "t1": None,
                        "reason": f"cursor not found at the boundary {a} -> {b} "
                                  f"({'end of ' + a if not ta else 'start of ' + b}, 6 frames searched): the cursor "
                                  f"must be there from the first frame and never pop in"})
            continue
        lim = JUMP_PX * ra.get("scale", 1.0)
        pa, pb = ta[-1], tb[0]
        d = ((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2) ** 0.5
        if d > lim:
            out.append({"check": "Q-43", "severity": "FAIL", "clip": b, "t0": 0.0, "t1": None,
                        "reason": f"cursor jumps {d:.0f} px across the clip boundary {a} -> {b}: tip "
                                  f"({pa[0]}, {pa[1]}) at the end of {a} -> ({pb[0]}, {pb[1]}) at the start of {b} "
                                  f"(limit {lim:.0f} px)",
                        "detail": {"distance": round(d, 1)}})
    return out
