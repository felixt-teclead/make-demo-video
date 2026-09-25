"""Ripples = clicks (Q-44) and ripples never stick (Q-45).

Detection (spec Q-44 defaults, calibrated on the Q-90 fixtures): per frame at native resolution, pixels in the ripple
colour band (blue is the largest channel, hue 200-225 deg, chroma >= RING_CHROMA; inside a sped-up stretch also the
paler band hue 195-230, chroma 25-59) are grouped into blobs; a blob is a ring when it is hollow and roughly round
(aspect <= 1.4, ring pixels <= 45 % of its box, >= 24 px across, >= 20 ring pixels). Rings in consecutive frames at
about the same place form one ripple instance.

Speed: a cheap prefilter on every 2nd pixel finds 16 px cells with band pixels; only candidate cells are analysed at
full resolution.
"""
import numpy as np

HUE_LO, HUE_HI = 200.0, 225.0
RING_CHROMA = 60          # spec default
RING_REL = 0.8            # ring pixels: chroma >= RING_REL x the blob's 98th-percentile chroma (see note)
PALE_HUE_LO, PALE_HUE_HI = 195.0, 230.0
PALE_CHROMA_LO, PALE_CHROMA_HI = 25, 59
MAX_ASPECT = 1.4
MAX_FILL = 0.45
MIN_ACROSS = 24           # px at 1920 wide
MIN_RING_PX = 20
MIN_STROKE = 0.5          # ring pixels >= MIN_STROKE x pi x diameter (a closed stroke, not a fragment; added)
MIN_SECTORS = 7           # ring pixels in >= 7 of 8 angular sectors around the centre (roundness; added)
MAX_VISIBLE_S = 0.9       # Q-45
MATCH_WINDOW_S = 1.5      # Q-44
MIN_FRAMES = 2            # a single (faint) frame is no visible ripple (owner decision, q44-click-ring-overpainted)
GROW_RATIO = 0.75         # a clean ripple is seen growing: first ring <= 0.75 x its peak diameter (Q-76; added)
EDGE_MARGIN = 16          # a ring whose box comes this close to the frame border counts as cut by the edge
CELL = 16                 # prefilter cell size in full-res px
LINK_PX = 60              # max centre distance to link rings in consecutive frames (px at 1920)
GAP_FRAMES = 2            # a ring may be missed for up to this many frames inside one instance

# Note on RING_REL (added to the spec's detection): the ripple's 38 % fill measures chroma 59-81 depending on the page
# behind it (dark page 60-66, the bluish dialog panel of the clean take 76-81), so with a fixed chroma floor the fill
# joins the ring and the blob is no longer hollow. The stroke is always the most saturated part (100-135), so inside
# each candidate blob only pixels within RING_REL of the blob's top chroma count as ring pixels.


def _band(px, pale):
    """px: (..., 3) int16. Returns bool mask of ripple-band pixels."""
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    mn = np.minimum(r, g)
    c = b - mn
    blue_max = (b >= g) & (g > r)
    cs = np.maximum(c, 1)
    hue = 240.0 + 60.0 * (r - g) / cs
    m = blue_max & (c >= RING_CHROMA) & (hue >= HUE_LO) & (hue <= HUE_HI)
    if pale:
        m |= blue_max & (c >= PALE_CHROMA_LO) & (c <= PALE_CHROMA_HI) & (hue >= PALE_HUE_LO) & (hue <= PALE_HUE_HI)
    return m, c


def _prefilter(sub):
    """Cheap band test on a subsampled int16 frame (no division)."""
    r, g, b = sub[..., 0], sub[..., 1], sub[..., 2]
    return (b - r >= RING_CHROMA) & (g > r) & (b >= g)


def _components(cells):
    """8-connected components of a small boolean grid. Returns list of lists of (row, col)."""
    seen = np.zeros_like(cells, bool)
    out = []
    H, W = cells.shape
    for r0, c0 in zip(*np.nonzero(cells)):
        if seen[r0, c0]:
            continue
        stack = [(r0, c0)]
        seen[r0, c0] = True
        comp = []
        while stack:
            r, c = stack.pop()
            comp.append((r, c))
            for rr in (r - 1, r, r + 1):
                for cc in (c - 1, c, c + 1):
                    if 0 <= rr < H and 0 <= cc < W and cells[rr, cc] and not seen[rr, cc]:
                        seen[rr, cc] = True
                        stack.append((rr, cc))
        out.append(comp)
    return out


def find_rings(frame, scale=1.0, pale=False, planar=False):
    """Rings in one frame: RGB (h, w, 3) uint8, or planar gbrp (3, h, w) with planar=True.
    Returns list of dict(x, y, d, px)."""
    if planar:
        H, W = frame.shape[1:]
        g, b, r = frame[0, ::2, ::2], frame[1, ::2, ::2], frame[2, ::2, ::2]

        def region(y0, y1, x0, x1):
            return np.stack([frame[2, y0:y1, x0:x1], frame[0, y0:y1, x0:x1], frame[1, y0:y1, x0:x1]],
                            -1).astype(np.int16)
    else:
        H, W = frame.shape[:2]
        r, g, b = frame[::2, ::2, 0], frame[::2, ::2, 1], frame[::2, ::2, 2]

        def region(y0, y1, x0, x1):
            return frame[y0:y1, x0:x1].astype(np.int16)
    if pale:
        m = _band(np.stack([r, g, b], -1).astype(np.int16), True)[0]
    else:
        # uint8 arithmetic: b - r wraps when b < r, which the b > r term excludes
        m = (b > r) & ((b - r) >= RING_CHROMA) & (g > r) & (b >= g)
    if not m.any():
        return []
    step = CELL // 2
    gh, gw = -(-m.shape[0] // step), -(-m.shape[1] // step)
    pad = np.zeros((gh * step, gw * step), bool)
    pad[:m.shape[0], :m.shape[1]] = m
    cnt = pad.reshape(gh, step, gw, step).sum(axis=(1, 3))
    cells = cnt >= 2
    if not cells.any():
        return []
    rings = []
    min_across = MIN_ACROSS * scale
    for comp in _components(cells):
        rs = [p[0] for p in comp]
        cs = [p[1] for p in comp]
        y0, y1 = max(min(rs) * CELL - 2, 0), min((max(rs) + 1) * CELL + 2, H)
        x0, x1 = max(min(cs) * CELL - 2, 0), min((max(cs) + 1) * CELL + 2, W)
        if (y1 - y0) < min_across * 0.8 and (x1 - x0) < min_across * 0.8:
            continue
        reg, chroma = _band(region(y0, y1, x0, x1), pale)
        if reg.sum() < MIN_RING_PX:
            continue
        top = np.percentile(chroma[reg], 98)
        reg &= chroma >= RING_REL * top
        n = int(reg.sum())
        if n < MIN_RING_PX:
            continue
        ys, xs = np.nonzero(reg)
        by0, by1, bx0, bx1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        h, w = by1 - by0, bx1 - bx0
        # a ring cut by the frame edge: judge its size and roundness by the uncut side
        em = EDGE_MARGIN * scale
        at_edge_y = (y0 + by0 <= em) or (y0 + by1 >= H - em)
        at_edge_x = (x0 + bx0 <= em) or (x0 + bx1 >= W - em)
        edge = at_edge_x or at_edge_y
        across = max(h, w)
        if across < min_across:
            continue
        lo, hi = min(h, w), max(h, w)
        if not edge and hi > MAX_ASPECT * lo:
            continue
        if edge and lo < 0.5 * hi:
            continue
        fill = n / float(h * w)
        if fill > MAX_FILL:
            continue
        # hollow: the middle third of the box is mostly free of ring pixels
        cy0, cy1 = by0 + h // 3, by1 - h // 3
        cx0, cx1 = bx0 + w // 3, bx1 - w // 3
        if cy1 > cy0 and cx1 > cx0 and not edge:
            if reg[cy0:cy1, cx0:cx1].mean() > 0.5 * fill:
                continue
        # a ring, not a fragment: enough stroke pixels for its size, spread all around its centre
        if n < MIN_STROKE * np.pi * across:
            continue
        if not edge:
            ang = np.arctan2(ys - (by0 + by1) / 2.0, xs - (bx0 + bx1) / 2.0)
            sectors = np.unique(((ang + np.pi) / (2 * np.pi) * 8).astype(int) % 8).size
            if sectors < MIN_SECTORS:
                continue
        cx = x0 + (bx0 + bx1) / 2.0
        cy = y0 + (by0 + by1) / 2.0
        if at_edge_y and not at_edge_x:
            cy = (y0 + by1 - w / 2.0) if y0 + by0 <= em else (y0 + by0 + w / 2.0)
        if at_edge_x and not at_edge_y:
            cx = (x0 + bx1 - h / 2.0) if x0 + bx0 <= em else (x0 + bx0 + h / 2.0)
        rings.append({"x": round(float(cx), 1), "y": round(float(cy), 1), "d": int(across), "px": n,
                      "fill": round(fill, 3)})
    return rings


def stream_planar(ctx, chunk=16):
    """Every frame of the clip as planar gbrp (3, h, w) uint8 (planes are contiguous: cheap per-channel math)."""
    import subprocess
    from .. import video
    fb = ctx.width * ctx.height * 3
    cmd = [video.ffmpeg(), "-v", "error", "-nostdin", "-i", ctx.path, "-map", "0:v:0",
           "-f", "rawvideo", "-pix_fmt", "gbrp", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=fb * 2)
    i = 0
    try:
        while True:
            buf = p.stdout.read(fb * chunk)
            n = len(buf) // fb
            if n == 0:
                break
            arr = np.frombuffer(buf, np.uint8, n * fb).reshape(n, 3, ctx.height, ctx.width)
            for k in range(n):
                yield i, arr[k]
                i += 1
            if n < chunk:
                break
    finally:
        p.stdout.close()
        p.wait()


def detect(ctx, debug=False, on_frame=None):
    """Scan every frame; group rings into ripple instances. Returns list of instances.
    on_frame(i, gbrp_frame) is called for every frame (lets the cursor check share this decode)."""
    speed_any = bool(ctx.speedups)
    link = LINK_PX * ctx.scale
    tracks = []
    for i, frame in stream_planar(ctx):
        if on_frame is not None:
            on_frame(i, frame)
        if True:
            pale = speed_any and ctx.speed_at(i / ctx.fps) > 1.0
            found = find_rings(frame, ctx.scale, pale, planar=True)
            if debug and found:
                print(i, round(i / ctx.fps, 2), found)
            for r in found:
                best = None
                for tr in tracks:
                    if 0 < i - tr["last"] <= GAP_FRAMES + 1:
                        dd = ((tr["x"] - r["x"]) ** 2 + (tr["y"] - r["y"]) ** 2) ** 0.5
                        if dd <= link and (best is None or dd < best[0]):
                            best = (dd, tr)
                if best:
                    tr = best[1]
                    tr["frames"].append(i)
                    tr["last"] = i
                    tr["x"], tr["y"] = r["x"], r["y"]
                    tr["dmax"] = max(tr["dmax"], r["d"])
                else:
                    tracks.append({"frames": [i], "last": i, "x": r["x"], "y": r["y"], "x0": r["x"], "y0": r["y"],
                                   "dmax": r["d"], "d0": r["d"]})
    out = []
    for tr in tracks:
        if len(tr["frames"]) < MIN_FRAMES:
            continue
        f0, f1 = tr["frames"][0], tr["frames"][-1]
        out.append({"t0": round(f0 / ctx.fps, 3), "t1": round((f1 + 1) / ctx.fps, 3),
                    "x": int(round(tr["x0"])), "y": int(round(tr["y0"])), "frames": len(tr["frames"]),
                    "diameter": int(tr["dmax"]), "first_diameter": int(tr["d0"]),
                    "grows": bool(tr["d0"] <= GROW_RATIO * tr["dmax"] or f0 == 0
                                  or ctx.speed_at(f0 / ctx.fps) > 1.0)})
    out.sort(key=lambda r: r["t0"])
    return out


def run_clip(ctx):
    from . import cursor
    coll = cursor.Collector(ctx)
    ctx.ripples = detect(ctx, on_frame=coll.feed)
    ctx.cursor_collector = coll
    # Q-45: never stick
    for r in ctx.ripples:
        vis = r["t1"] - r["t0"]
        limit = MAX_VISIBLE_S / max(ctx.speed_at(r["t0"]), 1.0)
        if vis > limit + 1e-6:
            ctx.add("Q-45", r["t0"], r["t1"],
                    f"ripple at ({r['x']}, {r['y']}) stays visible {vis:.2f} s (limit {limit:.2f} s)",
                    x=r["x"], y=r["y"], seconds=round(vis, 3))
    ctx.note("Q-44", f"{len(ctx.ripples)} ripple(s) detected"
             + (": " + ", ".join(f"{r['t0']:.2f}-{r['t1']:.2f} s at ({r['x']}, {r['y']})" for r in ctx.ripples)
                if ctx.ripples else ""))
    if ctx.ev is None:
        return
    _match(ctx)


def _match(ctx):
    clicks = list(ctx.ev.clicks)
    rip = list(ctx.ripples)
    pairs = []
    for ri, r in enumerate(rip):
        if not r["grows"]:
            continue   # appeared already expanded: its start was painted over, not a clean ripple for a click
        for ci, c in enumerate(clicks):
            dt = abs(r["t0"] - float(c["t"]))
            if dt <= MATCH_WINDOW_S:
                pairs.append((dt, ri, ci))
    pairs.sort()
    used_r, used_c = set(), set()
    for dt, ri, ci in pairs:
        if ri in used_r or ci in used_c:
            continue
        used_r.add(ri)
        used_c.add(ci)
    for ri, r in enumerate(rip):
        if ri in used_r:
            continue
        if not r["grows"] and any(abs(r["t0"] - float(c["t"])) <= MATCH_WINDOW_S for c in clicks):
            continue   # reported with its click below
        if not clicks:
            why = "the clip has no logged click"
        else:
            near = [c for c in clicks if abs(r["t0"] - float(c["t"])) <= MATCH_WINDOW_S]
            why = ("its click already has a ripple (two ripples for one click)" if near
                   else f"no logged click within {MATCH_WINDOW_S} s")
        ctx.add("Q-44", r["t0"], r["t1"], f"ripple without a click at ({r['x']}, {r['y']}): {why}",
                x=r["x"], y=r["y"])
    for ci, c in enumerate(clicks):
        if ci in used_c:
            continue
        lab = f" \"{c['label']}\"" if c.get("label") else ""
        where = f" at ({c['x']}, {c['y']})" if c.get("x") is not None else ""
        part = [r for r in rip if not r["grows"] and abs(r["t0"] - float(c["t"])) <= MATCH_WINDOW_S]
        if part:
            r = part[0]
            why = (f"no clean ripple: the ring appears at {r['t0']:.2f} s already {r['first_diameter']} px across "
                   f"(peak {r['diameter']} px), so its start was painted over")
        else:
            why = f"no visible ripple (no ring of {MIN_FRAMES}+ frames within {MATCH_WINDOW_S} s)"
        ctx.add("Q-44", float(c["t"]), None, f"click{lab}{where} without a clean ripple: {why}")
    if len(rip) != len(clicks):
        ctx.note("Q-44", f"{len(rip)} ripple(s) for {len(clicks)} logged click(s)")
