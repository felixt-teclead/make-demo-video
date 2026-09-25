"""Q-31 (pans and scrolls are rigid, eased, one-directional) and Q-34 (the page paints every frame during motion).

Algorithm
1. Motion episodes from the 480x240 frame differences: seeds are moving frames (MAD >= 1.5) and runs of >= 6 changing
   frames (MAD > 0.5); an episode is the run of changing frames around a seed, with stalls of up to 3 still frames,
   extended over the adjacent faint changes (MAD > 0.05: the ease tails of a pan over a dark, sparse view, whose
   480x240 MAD stays below 0.5 although the view moves by up to 11 px per frame), padded by 0.25 s on each side; if motion reaches the padded window's edge, the window is widened once. A cheap
   480x240 pre-check skips episodes that no shift explains at all (page changes, fades) without a native decode.
2. The episode's native-resolution luma frames are decoded. Per frame pair, sticky bands (rows / columns whose band
   change energy, averaged over 16 px, is below 15 % of the largest band's) are excluded; the rest is the moving
   surface. Duplicated frames (99.9th
   percentile of the change below 24 grey levels) are marked.
3. Best shift per axis, coarse-then-fine: 1-D profile correlation over +-90 px, then the 2-D residual at 1 px steps
   around the three best candidates, then sub-pixel refinement (a linear blend of the neighbouring integer shifts, so
   sub-pixel rendering does not count as residual).
4. An episode is a pan when single shifts explain most of its changed frames, the shifted frames are at least a third
   of its moving part, and the surface is not a small widget; otherwise it is another change (page transition, fade,
   dialog) that belongs to Q-20 / Q-21 / Q-30.
5. Q-31 per pan: residual ratios (<= 0.8 per frame, <= 0.5 median; frames with pre-shift residual < 1 exempt, and
   unshifted settle repaints < 1.5 within 4 frames outside the shifted span), the
   other axis, the step limit (40 px per displayed frame, divided by the frame intervals a paint spans, capped at the
   episode's paint cadence), the ease (first / last step <= 0.6 x the largest, judged only on sides the clip shows),
   >= 3 steps, no direction change and no pace reversal (jitter), no stall > 3 frames, and no region that moves by a
   shift of its own (a coherent band of >= 4 blocks spread >= 400 px whose own shift differs by >= max(3 px, half the
   step): an element that rides along and stops part-way, or animates by itself). Such a band is traced over the
   whole episode: when it followed the page's shift from the pan's start and then pinned, the violation reports the
   ride-along (frames, seconds, px). One violation per episode (Q-06).
6. Q-34 per pan: neighbouring steps (two consecutive frame intervals that both show a new picture) differ by at most
   2 px (FAIL; frames Q-31 already reports are not counted twice). Duplicated frames inside the moving part. A duplicate is a FAIL when the pan also shows a double
   update (a step >= 1.8 x the local median right after a painted frame: the page's timer put two updates into one
   frame and none into another, the Why of Q-34). Duplicates in an evenly stepping pan are reported as a WARN
   ("dropped paints"). This is the owner's decision (OD-15, revised 2026-09-25): duplicates are fine when the pan
   looks fine; only a visible hitch (a duplicate followed by a double-size step, or a step that breaks the smoothness
   limits) fails, and evenly dropped paints are a WARN. The Q-90 clean pan (PASS, 98 of 252 frames repeated) stays a
   PASS. In a pan with such dropped paints the neighbour-step excess is part of the same WARN (the clean pan also has
   two 3 px neighbour steps). Q34_STRICT = True (the superseded 2026-09-24 rule: any duplicate fails) makes both FAIL;
   it stays off by default.
"""
import numpy as np

from ..model import MOVING_MAD, STILL_MAD

# default thresholds (section Q, Q-31 / Q-34)
STALL_MAX = 3            # still frames allowed inside an episode / a pan
PAD_S = 0.25             # pad on each side of the episode
STICKY_FRAC = 0.15       # rows/cols with change energy below 15 % of the largest are sticky
BAND_PX = 16             # ... energy measured per band of 16 px (a band, not one bright line, is 'the largest')
RATIO_FRAME = 0.8        # residual after shift <= 0.8 x before, every frame
RATIO_MEDIAN = 0.5       # ... and <= 0.5 x as the episode median
OTHER_AXIS_GAIN = 0.10   # a shift on the other axis improves the residual by no more than 10 %
EXEMPT_RESIDUAL = 1.0    # frames whose pre-shift residual is below 1 grey level are exempt from the proof
SETTLE_RESIDUAL = 1.5    # ... and so is an unshifted settle repaint just outside the shifted span below this (_judge)
STEP_MAX_PX = 40.0       # px per displayed frame, at 1920 wide
EASE_FRAC = 0.6          # first and last step <= 0.6 x the largest step
MIN_STEPS = 3
DUP_P999 = 24.0          # a frame pair whose 99.9th-percentile luma change is below this shows the same picture
DUP_RESIDUAL = 0.25      # a surface residual this small counts as no change
Q34_STRICT = False       # keep off: owner's rule (OD-15, revised 2026-09-25). True = superseded any-duplicate-fails
NEIGHBOUR_PX = 2.0       # Q-34: neighbouring steps differ by at most 2 px (px at 1920 wide)
NEIGHBOUR_SLACK = 0.25   # measurement resolution of a step (sub-pixel refinement in 0.25 px): 2 px measured on a
                         # 1910 px clip is not over a limit of 1.99 px
DOUBLE_WIN = 5           # frames either side for the local step median
DOUBLE_FRAC = 1.8        # a step >= 1.8 x the local median right after a painted frame is a double update
DOUBLE_EXCESS = 0.25     # ... and exceeds the median by >= 25 % of the episode's largest step
IND_DIFF = 24.0          # independent motion: pixels that changed and stay unexplained by more than this
IND_BLOCK = 32
IND_MIN_PX = 40          # unexplained pixels per 32 px block (at 1920 wide)
IND_MAX_BLOCKS = 24
IND_EXPLAIN = 0.5        # the block's own shift explains it: residual <= 0.5 x min(before, after the frame shift)
IND_MIN_OFFSET = 3       # own shift differs from the frame's by >= 3 px
IND_REL = 0.5            # ... and by >= half the frame's step (a visible deviation, not motion-masked)
IND_MIN_BLOCKS = 4       # a coherent group of >= 4 blocks with the same own shift (+-1 px)
IND_SPREAD_PX = 400      # spread across >= 400 px (at 1920 wide): a band, not a local glitch
SPIKE_PX = 5.0           # jitter: speed spike, both neighbouring step changes >= max(5 px, 30 % of the step)
SPIKE_FRAC = 0.3
PAN_MIN_SHARE = 1 / 3.0  # a pan shows a new, shifted picture on at least a third of its frames
PAN_MIN_AREA = 0.05      # a pan or scroll moves a surface of >= 5 % of the frame (not a small widget)
SEARCH_PX = 90           # shift search range per frame, px at 1920 wide
SEED_RUN = 6             # a run of this many changing frames seeds an episode even without a "moving" frame
FAINT_MAD = 0.05         # a seeded episode extends over adjacent faint changes (480x240 MAD above this, stalls of up
                         # to 3 still frames): the ease tails of a pan over a dark, sparse view (c3 04-pan t3: the
                         # 1-11 px ease-in steps of an 804 px pan have MAD 0.09-0.50, the 14 px peak only 0.67), so a
                         # MAD <= 0.5 "still" frame there still moves. A repeated frame is 0.00-0.01.


def _episodes(ctx):
    mad = ctx.mad
    n = ctx.n
    if n < 3:
        return []
    changing = mad > STILL_MAD
    moving = mad >= MOVING_MAD
    runs = []
    i = 1
    while i < n:
        if not changing[i]:
            i += 1
            continue
        s = e = i
        j = i + 1
        while j < n:
            if changing[j]:
                e = j
                j += 1
            elif j - e <= STALL_MAX:
                j += 1
            else:
                break
        runs.append((s, e))
        i = e + 1
    pad = int(round(PAD_S * ctx.fps))
    faint = mad > FAINT_MAD
    eps = []
    for s, e in runs:
        nchg = int(changing[s:e + 1].sum())
        if not moving[s:e + 1].any() and nchg < SEED_RUN:
            continue
        s, e = _extend_faint(faint, s, e, n)
        a = max(0, s - 1 - pad)     # frame k shows the change k-1 -> k, so the episode starts at s-1
        b = min(n - 1, e + pad)
        if eps and a <= eps[-1][1]:
            eps[-1] = (eps[-1][0], max(eps[-1][1], b))
        else:
            eps.append((a, b))
    return eps


def _extend_faint(faint, s, e, n):
    """Extend a seeded run [s, e] over the adjacent faint changes (stalls of up to STALL_MAX still frames): the whole
    motion, ease-in and ease-out included, is one episode even where its 480x240 MAD stays below the still level."""
    j, gap = s - 1, 0
    while j >= 1 and gap <= STALL_MAX:
        if faint[j]:
            s, gap = j, 0
        else:
            gap += 1
        j -= 1
    j, gap = e + 1, 0
    while j < n and gap <= STALL_MAX:
        if faint[j]:
            e, gap = j, 0
        else:
            gap += 1
        j += 1
    return s, e


def _prof_candidates(pa, pb, idx, R, top=3):
    """Shift candidates d (b[i] = a[i + d]) from 1-D profiles over the surface indices idx (all d at once)."""
    L = len(pa)
    d = np.arange(-R, R + 1)
    src = idx[None, :] + d[:, None]
    ok = (src >= 0) & (src < L)
    diff = np.abs(pb[idx][None, :] - pa[np.clip(src, 0, L - 1)])
    n = ok.sum(axis=1)
    scores = np.where(n >= 16, (diff * ok).sum(axis=1) / np.maximum(n, 1), np.inf)
    cands = []
    for k in np.argsort(scores):
        if not np.isfinite(scores[k]):
            break
        dd = int(d[k])
        if all(abs(dd - c) > 2 for c in cands):
            cands.append(dd)
        if len(cands) >= top:
            break
    if 0 not in cands:
        cands.append(0)
    return cands


class _Surface:
    def __init__(self, rows, cols, H, W):
        self.rows, self.cols, self.H, self.W = rows, cols, H, W
        self.rs = rows[::2]
        self.cs = cols[::2]

    def residual(self, a, b, dy, dx, frac=0.0, axis=0):
        """Mean |b - a shifted by (dy, dx)| over the surface; frac blends toward the next shift on `axis`."""
        rs, cs = self.rs, self.cs
        ok = (rs + dy >= 0) & (rs + dy < self.H)
        oc = (cs + dx >= 0) & (cs + dx < self.W)
        if frac:
            if axis == 0:
                ok &= rs + dy + 1 < self.H
            else:
                oc &= cs + dx + 1 < self.W
        r, c = rs[ok], cs[oc]
        if len(r) < 8 or len(c) < 8:
            return np.inf
        B = b[np.ix_(r, c)]
        A = a[np.ix_(r + dy, c + dx)]
        if frac:
            A2 = a[np.ix_(r + dy + 1, c + dx)] if axis == 0 else a[np.ix_(r + dy, c + dx + 1)]
            A = A * (1 - frac) + A2 * frac
        return float(np.abs(B - A).mean())


def _best_shift(a, b, surf, rpa, rpb, cpa, cpb, R):
    r0 = surf.residual(a, b, 0, 0)
    out = {"r0": r0}
    if r0 < DUP_RESIDUAL:
        out[0] = out[1] = (0, r0, 0.0, r0)
        out["both"] = r0
        return out
    for axis in (0, 1):
        cands = _prof_candidates(rpa, rpb, surf.rows, R) if axis == 0 else _prof_candidates(cpa, cpb, surf.cols, R)
        bd, br = 0, r0
        for c in cands:
            for d in (c - 1, c, c + 1):
                if d == 0:
                    continue
                r = surf.residual(a, b, d if axis == 0 else 0, d if axis == 1 else 0)
                if r < br:
                    bd, br = d, r
        sub, sr = float(bd), br
        for lo in (bd - 1, bd):
            for f in (0.25, 0.5, 0.75):
                r = surf.residual(a, b, lo if axis == 0 else 0, lo if axis == 1 else 0, f, axis)
                if r < sr:
                    sub, sr = lo + f, r
        out[axis] = (bd, br, sub, sr)
    # both axes at once (for the "other axis improves by no more than 10 %" rule)
    out["both"] = surf.residual(a, b, out[0][0], out[1][0]) if out[0][0] and out[1][0] else min(out[0][1], out[1][1])
    return out


def _band_energy(e):
    """Change energy of the band around each row (column): the mean over BAND_PX. A sticky band is a band of rows,
    so its energy is compared band against band; one very bright line (a dashed connector, a dotted grid row) must
    not set 'the largest' so high that most of a sparse, rigidly moving diagram counts as sticky (c3 horizontal pan,
    v1-parity section 3: frames with a 2-10 px shift were left with 5-14 surface rows and read as stalls)."""
    w = max(1, int(BAND_PX) // 2)       # the energy profile is on a 2 px grid
    if len(e) < w:
        return e
    return np.convolve(e, np.ones(w) / w, mode="same")


def _frame_surface(a, b, H, W):
    """Moving surface of one frame pair: rows and columns whose band change energy is >= 15 % of the largest band's
    (sticky bands excluded per frame, so a header that pins mid-pan leaves the surface once it stops)."""
    d = np.abs(b[::2, ::2] - a[::2, ::2])
    re = _band_energy(d.sum(axis=1))
    ce = _band_energy(d.sum(axis=0))
    if re.max() <= 0:
        return None
    rows = np.where(re >= STICKY_FRAC * re.max())[0] * 2
    cols = np.where(ce >= STICKY_FRAC * ce.max())[0] * 2
    if len(rows) < 8 or len(cols) < 8:
        return None
    return _Surface(rows, cols, H, W)


def _independent(ctx, a, b, bs):
    """Regions of the frame that move by a shift of their own (an element that rides along and stops part-way, or
    moves by itself): 32 px blocks of changed pixels that the frame's shift does not explain, each tested for its own
    best shift. Returns the largest coherent group [(y, x, offset_main, offset_other, pixels)] or []."""
    if bs["r0"] < EXEMPT_RESIDUAL:
        return []
    ax = 0 if bs[0][3] <= bs[1][3] else 1
    s = int(round(bs[ax][2]))
    if abs(s) < 2:
        return []
    if ax == 1:
        a, b = a.T, b.T
    H, W = a.shape
    # cheap pre-check on a 2 px grid: enough changed-but-unexplained pixels at all?
    ag, bg = a[::2, ::2], b[::2, ::2]
    sg = s // 2
    Ag = np.full_like(ag, np.nan)
    if sg >= 0:
        Ag[:ag.shape[0] - sg] = ag[sg:]
    else:
        Ag[-sg:] = ag[:ag.shape[0] + sg]
    with np.errstate(invalid="ignore"):
        ug = int(((np.abs(bg - ag) > IND_DIFF) & (np.abs(bg - Ag) > IND_DIFF)).sum()) * 4
    if ug < IND_MIN_BLOCKS * IND_MIN_PX * ctx.scale ** 2:
        return []
    A = np.full_like(a, np.nan)
    if s >= 0:
        A[:H - s] = a[s:]
    else:
        A[-s:] = a[:H + s]
    with np.errstate(invalid="ignore"):
        U = (np.abs(b - a) > IND_DIFF) & (np.abs(b - A) > IND_DIFF)
    bsz = IND_BLOCK
    if U.sum() < IND_MIN_BLOCKS * IND_MIN_PX * ctx.scale ** 2:
        return []
    Hb, Wb = H // bsz, W // bsz
    cnt = U[:Hb * bsz, :Wb * bsz].reshape(Hb, bsz, Wb, bsz).sum(axis=(1, 3))
    minpx = IND_MIN_PX * ctx.scale ** 2
    cand = sorted(((int(cnt[y, x]), y, x) for y, x in zip(*np.where(cnt >= minpx))), reverse=True)[:IND_MAX_BLOCKS]
    hits = []
    lo_d, hi_d = min(0, s) - 2, max(0, s) + 2
    ap = np.pad(a, ((0, 0), (3, 3)), mode="edge")
    for c, by, bx in cand:
        ys, xs = np.where(U[by * bsz:(by + 1) * bsz, bx * bsz:(bx + 1) * bsz])
        ys = ys + by * bsz
        xs = xs + bx * bsz + 3
        bv = b[ys, xs - 3]

        def res(dy, dx):
            yy = ys + dy
            ok = (yy >= 0) & (yy < H)
            if ok.mean() < 0.95:
                return np.inf
            return float(np.abs(bv[ok] - ap[yy[ok], xs[ok] + dx]).mean())

        # coarse-then-fine: own shift along the pan axis first, then the other axis around the best candidates
        along = sorted((res(dy, 0), dy) for dy in range(lo_d, hi_d + 1))
        best = (along[0][0], along[0][1], 0)
        for _, dy in along[:2] + [(0, s)]:
            for dx in (-3, -2, -1, 1, 2, 3):
                r = res(dy, dx)
                if r < best[0]:
                    best = (r, dy, dx)
        rs = float(np.nanmean(np.abs(bv - A[ys, xs - 3])))
        ps = float(np.abs(bv - a[ys, xs - 3]).mean())
        r, dy, dx = best
        if r <= IND_EXPLAIN * min(rs, ps) and max(abs(dy - s), abs(dx)) >= max(IND_MIN_OFFSET, IND_REL * abs(s)) \
                and (dy, dx) != (0, 0):
            hits.append((by * bsz, bx * bsz, dy - s, dx, c))
    best_group = []
    for h in hits:
        g = [k for k in hits if abs(k[2] - h[2]) <= 1 and abs(k[3] - h[3]) <= 1]
        if len(g) > len(best_group):
            best_group = g
    if len(best_group) < IND_MIN_BLOCKS:
        return []
    xs_ = [k[1] for k in best_group]
    if max(xs_) - min(xs_) + bsz < ctx.px(IND_SPREAD_PX):
        return []
    return best_group


def _band_offset(F, T, y0, x0, lo, hi):
    """Offset o in [lo, hi] along axis 0 at which F[y0 + o : y0 + o + h, x0 : x0 + w] best matches T.
    Returns (o, residual) or None. 1-D row-profile candidates, then the 2-D residual around them."""
    h, w = T.shape
    H = F.shape[0]
    lo, hi = max(lo, -y0), min(hi, H - h - y0)
    if hi < lo:
        return None
    cols = F[:, x0:x0 + w].mean(axis=1)
    tp = T.mean(axis=1)
    offs = np.arange(lo, hi + 1)
    sc = np.array([np.abs(cols[y0 + o:y0 + o + h] - tp).mean() for o in offs])
    best = None
    for k in np.argsort(sc)[:3]:
        for o in range(int(offs[k]) - 1, int(offs[k]) + 2):
            if lo <= o <= hi:
                r = float(np.abs(F[y0 + o:y0 + o + h:2, x0:x0 + w:2] - T[::2, ::2]).mean())
                if best is None or r < best[1]:
                    best = (o, r)
    return best


def _ride_along(ctx, fr, per, i, group):
    """Trace the band that moved by a shift of its own at frame pair i (per[i], fr[i] -> fr[i + 1]) over the whole
    episode: where was it in every frame? A sticky element that rides along with the pan and then pins shows as a
    band whose position follows the page's shift from the pan's start and then stays put. Returns {first, last, px,
    band, what} (per indices of the frames in which it rode along) or None when the band did not ride with the pan
    (it animates by itself: the caller keeps the per-frame report)."""
    bs = per[i]
    ax = 0 if bs[0][3] <= bs[1][3] else 1        # as in _independent: its blocks are in these coordinates
    if ax == 1:
        fr = np.transpose(fr, (0, 2, 1))
    k, H, W = fr.shape
    y0 = min(b[0] for b in group)
    y1 = max(b[0] for b in group) + IND_BLOCK
    x0 = min(b[1] for b in group)
    x1 = max(b[1] for b in group) + IND_BLOCK
    T = fr[i + 1, y0:y1, x0:x1]
    if T.size == 0 or float(T.std()) < 2.0:
        return None
    shift = np.array([p[ax][2] if (not p.get("dup") and p[ax][3] < p["r0"]) else 0.0 for p in per])
    reach = int(np.ceil(np.abs(shift[:i + 1]).sum())) + 8
    reach = min(reach, int(ctx.px(400)))
    pos = [None] * k
    for j in range(k):
        m = _band_offset(fr[j], T, y0, x0, -reach, reach)
        if m is None:
            return None
        pos[j] = m
    # the band must be found (its own look) in every frame, else it is not one element that rides and pins
    rt = [r for _, r in pos]
    if max(rt) > max(6.0, 0.35 * float(T.std())):
        return None
    off = np.array([o for o, _ in pos], float)
    final = off[i + 1]
    # it pins: from frame i + 1 on it stays at its final place
    if np.any(np.abs(off[i + 1:] - final) > 1):
        return None
    moved = [j for j in range(1, i + 2) if abs(off[j] - off[j - 1]) > 0.5]
    if not moved:
        return None
    first, last = moved[0], moved[-1]
    px = abs(off[first - 1] - final)
    if px < max(IND_MIN_OFFSET, 2):
        return None
    # it rode WITH the pan: in the frames it moved (before the last, partial one) its step equals the page's step
    full = [j for j in moved if j < last]
    agree = [j for j in full if abs(abs(off[j] - off[j - 1]) - abs(shift[j - 1])) <= max(2.0, 0.25 * abs(shift[j - 1]))]
    if full and len(agree) < 0.75 * len(full):
        return None
    what = ("rows" if ax == 0 else "columns") + f" {y0}-{y1} at their pinned place"
    # moved[] indexes frames of fr; per index = frame index - 1 (per[j - 1] is fr[j - 1] -> fr[j])
    return {"first": first - 1, "last": last - 1, "px": float(px), "band": [int(y0), int(y1), int(x0), int(x1)],
            "what": what}


def _analyse(ctx, a0, b0):
    fr = ctx.full(a0, b0 - a0 + 1, "gray").astype(np.float32)
    k = len(fr)
    if k < 3:
        return None
    H, W = fr.shape[1:]
    R = max(4, int(round(ctx.px(SEARCH_PX))))
    per = []
    for i in range(1, k):
        a, b = fr[i - 1], fr[i]
        g = np.abs(b[::2, ::2] - a[::2, ::2])
        dup = float((g >= DUP_P999).mean()) <= 0.001     # 99.9th percentile of the change below DUP_P999
        surf = None if dup else _frame_surface(a, b, H, W)
        if surf is None:
            z = (0, 0.0, 0.0, 0.0)
            per.append({"r0": 0.0, 0: z, 1: z, "both": 0.0, "dup": True})
            continue
        rpa = a[:, surf.cols].mean(axis=1)
        rpb = b[:, surf.cols].mean(axis=1)
        cpa = a[surf.rows, :].mean(axis=0)
        cpb = b[surf.rows, :].mean(axis=0)
        bs = _best_shift(a, b, surf, rpa, rpb, cpa, cpb, R)
        bs["dup"] = False
        bs["area"] = float((np.ptp(surf.rows) + 2) * (np.ptp(surf.cols) + 2)) / float(H * W)
        per.append(bs)
    return {"per": per, "fr": fr}


def _judge(ctx, a0, b0, an):
    """Evaluate the Q-31 / Q-34 rules on one analysed episode. Returns (kind, ok, info)."""
    per = an["per"]
    r0 = np.array([p["r0"] for p in per])
    proof = r0 >= EXEMPT_RESIDUAL
    tot = [sum(abs(p[ax][2]) for p, pr in zip(per, proof) if pr and p[ax][3] < 0.8 * p["r0"]) for ax in (0, 1)]
    ax = 0 if tot[0] >= tot[1] else 1
    info = {"axis": "y" if ax == 0 else "x"}
    shift = np.array([p[ax][2] if p[ax][3] < p["r0"] else 0.0 for p in per])
    res = np.array([min(p[ax][3], p["r0"]) for p in per])
    both = np.array([p["both"] for p in per])
    ratio = np.where(proof, res / np.maximum(r0, 1e-6), 0.0)
    painted = np.array([not p.get("dup", p["r0"] < DUP_RESIDUAL) for p in per])
    shifted = painted & (np.abs(shift) >= 1.0)
    nproof = int(proof.sum())
    explained = int((proof & (ratio <= RATIO_FRAME) & (np.abs(shift) >= 1)).sum())
    areas = [p.get("area", 0.0) for p, sh in zip(per, shifted) if sh]
    if shifted.sum() < MIN_STEPS or nproof == 0 or explained < max(2, 0.5 * nproof) \
            or float(np.median(areas)) < PAN_MIN_AREA:
        return "other", False, info
    sh_idx = np.where(shifted)[0]
    if shifted.sum() < PAN_MIN_SHARE * (sh_idx[-1] - sh_idx[0] + 1):
        return "other", False, info    # sporadic updates (late content, a spinner), not a sustained camera move
    idx = np.where(shifted)[0]
    f0, f1 = int(idx[0]), int(idx[-1])
    # settle repaints: right before the first / after the last shifted frame, a frame that no shift explains but that
    # changes by less than SETTLE_RESIDUAL is the page settling at rest (a sub-pixel ease tail re-rasterised at its
    # integer position; Q-90 clean pan frame 254: r0 1.03 over a sparse surface, 0.12 over the frame), not a moving
    # frame: exempt from the proof like the sub-pixel tails below 1 grey level
    near = np.zeros(len(per), bool)
    near[max(0, f0 - STALL_MAX - 1):f0] = True
    near[f1 + 1:f1 + STALL_MAX + 2] = True
    settle = near & proof & (r0 < SETTLE_RESIDUAL) & ~shifted
    if settle.any():
        proof = proof & ~settle
        ratio = np.where(proof, ratio, 0.0)
    reasons, bad = [], []
    badr = np.where(proof & (ratio > RATIO_FRAME))[0]
    med = float(np.median(ratio[proof]))
    if len(badr):
        reasons.append(f"{len(badr)} frame(s) that no single shift explains (residual up to {ratio[badr].max():.2f} x "
                       f"the unshifted one, limit {RATIO_FRAME}), first at {ctx.t(a0 + 1 + badr[0]):.2f} s")
        bad += list(badr)
    if med > RATIO_MEDIAN:
        reasons.append(f"median residual {med:.2f} x after the shift (limit {RATIO_MEDIAN})")
    gain = np.where(proof, (res - np.minimum(res, both)) / np.maximum(res, 1e-6), 0.0)
    bado = np.where(proof & (gain > OTHER_AXIS_GAIN) & ~np.isin(np.arange(len(per)), badr))[0]
    if len(bado):
        reasons.append(f"{len(bado)} frame(s) also move on the other axis (diagonal)")
        bad += list(bado)
    ind = [i for i, p in enumerate(per) if p.get("indep")]
    if ind:
        g = per[ind[0]]["indep"]
        along, other = int(np.median([k[2] for k in g])), int(np.median([k[3] for k in g]))
        ride = _ride_along(ctx, an.get("fr"), per, ind[0], g) if an.get("fr") is not None else None
        if ride:
            r0f, r1f = ride["first"], ride["last"]
            reasons.append(f"a band of the view ({ride['what']}) rides along with the pan for {r1f - r0f + 1} "
                           f"frame(s), {ctx.t(a0 + 1 + r0f):.2f}-{ctx.t(a0 + 1 + r1f):.2f} s, {ride['px']:.0f} px, "
                           f"and then stops (pins) while the rest moves on: a sticky element that rides along for "
                           f"the first part of the pan")
            info["ride_along"] = {"t0": round(ctx.t(a0 + 1 + r0f), 3), "t1": round(ctx.t(a0 + 1 + r1f), 3),
                                  "frames": r1f - r0f + 1, "px": round(ride["px"], 1), "band": ride["band"]}
            bad += list(range(r0f, r1f + 1)) + ind
        else:
            reasons.append(f"{len(ind)} frame(s) in which part of the view moves by a shift of its own, first at "
                           f"{ctx.t(a0 + 1 + ind[0]):.2f} s ({len(g)} blocks, {along:+d} px along and {other:+d} px "
                           f"across the pan relative to it: an element rides along and stops, or animates by itself)")
            bad += ind
    main = int(np.sign(shift[shifted].sum())) or 1
    rev = np.where(shifted & (np.sign(shift) == -main))[0]
    if len(rev):
        reasons.append(f"jitter: the direction reverses {len(rev)} time(s), first at {ctx.t(a0 + 1 + rev[0]):.2f} s "
                       f"({abs(shift[rev]).max():.0f} px back)")
        bad += list(rev)
    spikes = []
    for i in range(f0 + 1, f1):
        if not (shifted[i - 1] and shifted[i] and shifted[i + 1]):
            continue
        s0, s1, s2 = abs(shift[i - 1]), abs(shift[i]), abs(shift[i + 1])
        d1, d2 = s1 - s0, s2 - s1
        thr = max(ctx.px(SPIKE_PX), SPIKE_FRAC * min(s0, s2))
        if np.sign(d1) != np.sign(d2) and abs(d1) >= thr and abs(d2) >= thr:
            spikes.append(i)
    if spikes:
        i = spikes[0]
        reasons.append(f"jitter: the motion reverses its pace {len(spikes)} time(s) ({abs(shift[i - 1]):.0f} -> "
                       f"{abs(shift[i]):.0f} -> {abs(shift[i + 1]):.0f} px per frame at {ctx.t(a0 + 1 + i):.2f} s)")
        bad += spikes
    npaint = int(painted[f0:f1 + 1].sum())
    cadence = (f1 - f0 + 1) / max(1, npaint)
    cap = max(1, int(round(cadence)))
    steps = np.zeros(len(per))
    last = None
    for i in range(f0, f1 + 1):
        if painted[i]:
            span = 1 if last is None else i - last
            steps[i] = abs(shift[i]) / max(1, min(span, cap))
            last = i
    smax = float(steps.max())
    lim = ctx.px(STEP_MAX_PX)
    over = np.where(steps > lim)[0]
    if len(over):
        reasons.append(f"step of {abs(shift[over]).max():.0f} px at {ctx.t(a0 + 1 + over[0]):.2f} s "
                       f"(limit {lim:.0f} px per frame)")
        bad += list(over)
    sidx = np.where(shifted)[0]
    first, lastst = steps[sidx[0]], steps[sidx[-1]]
    # a motion already under way at the clip's first frame (or still under way at its last) has its ease outside
    # the clip; the ease is judged only on the sides the clip shows
    cut_in = a0 + 1 + f0 <= 1
    cut_out = a0 + 1 + f1 >= ctx.n - 1
    if (first > EASE_FRAC * smax and not cut_in) or (lastst > EASE_FRAC * smax and not cut_out):
        reasons.append(f"not eased: first step {first:.0f} px, last {lastst:.0f} px, largest {smax:.0f} px")
    stall = longest = 0
    for i in range(f0, f1 + 1):
        stall = stall + 1 if not shifted[i] else 0
        longest = max(longest, stall)
    if longest > STALL_MAX:
        reasons.append(f"stall of {longest} frames inside the pan")
    dups = [a0 + 1 + i for i in range(f0, f1 + 1) if not painted[i]]
    # double updates: a frame right after a painted frame that carries about two frames' worth of motion (the page's
    # timer put two updates into one frame and none into another; the Why of Q-34)
    doubles = []
    pidx = [i for i in range(f0, f1 + 1) if painted[i] and abs(shift[i]) >= 1]
    for i in pidx:
        if i - 1 < f0 or not painted[i - 1]:
            continue
        near = [abs(shift[j]) for j in pidx if j != i and abs(j - i) <= DOUBLE_WIN]
        if len(near) < 3:
            continue
        med = float(np.median(near))
        if abs(shift[i]) >= DOUBLE_FRAC * med and abs(shift[i]) - med >= max(ctx.px(4), DOUBLE_EXCESS * smax):
            doubles.append(a0 + 1 + i)
    st = steps[shifted]
    total = float(np.abs(shift[shifted]).sum())
    direc = {("y", 1): "down", ("y", -1): "up", ("x", 1): "right", ("x", -1): "left"}[(info["axis"], main)]
    info.update({"f0": a0 + 1 + f0, "f1": a0 + 1 + f1, "total": total, "dir": direc, "max_step": smax,
                 "median_ratio": med, "reasons": reasons, "bad": sorted(set(int(b) for b in bad)),
                 "dups": dups, "doubles": doubles, "cadence": cadence,
                 "max_delta": float(np.abs(np.diff(st)).max()) if len(st) > 1 else 0.0,
                 "steps": steps, "shift": shift, "ratio": ratio})
    dup_fail = bool(dups) and (Q34_STRICT or bool(doubles))
    info["dup_fail"] = dup_fail
    # Q-34 neighbour steps: two consecutive frame intervals that both show a new, shifted picture differ by at most
    # 2 px. Steps next to a duplicate span two intervals and belong to the duplicate rule; frames Q-31 already
    # reports (a pace reversal, a jump) are not counted again (Q-06).
    badset = set(int(b) for b in bad)
    neigh = []
    for i in range(f0 + 2, f1 + 1):
        if i in badset or i - 1 in badset:
            continue
        if all(painted[k] and shifted[k] for k in (i - 2, i - 1, i)):
            d = abs(abs(shift[i]) - abs(shift[i - 1]))
            if d > ctx.px(NEIGHBOUR_PX) + NEIGHBOUR_SLACK:
                neigh.append((a0 + 1 + i, float(d)))
    info["neighbour"] = neigh
    # with duplicates (dropped paints) the unevenness is the duplicate rule's case: a WARN unless there is a double
    # update (owner's rule, OD-15 revised 2026-09-25; Q34_STRICT stays off)
    neigh_fail = bool(neigh) and (not dups or Q34_STRICT)
    info["neighbour_fail"] = neigh_fail
    return "pan", not reasons and not dup_fail and not neigh_fail, info


def _maybe_pan(ctx, a0, b0):
    """Cheap pre-check on the 480x240 frames: does some non-zero shift explain at least two changed frames?
    Episodes that fail it (page changes, fades, dialogs) are not decoded at native resolution."""
    lo = ctx.low[a0:b0 + 1].astype(np.float32)
    hits = 0
    for i in range(1, len(lo)):
        a, b = lo[i - 1], lo[i]
        d = np.abs(b - a)
        if d.mean() < STILL_MAD * 0.2:
            continue
        re, ce = d.sum(axis=1), d.sum(axis=0)
        rows = np.where(re >= STICKY_FRAC * re.max())[0]
        cols = np.where(ce >= STICKY_FRAC * ce.max())[0]
        if len(rows) < 4 or len(cols) < 4:
            continue
        r0 = float(d[np.ix_(rows, cols)].mean())
        best = r0
        for axis in (0, 1):
            idx, L = (rows, a.shape[0]) if axis == 0 else (cols, a.shape[1])
            for sh in range(-12, 13):
                sel = idx[(idx + sh >= 0) & (idx + sh < L)]
                if sh == 0 or len(sel) < 4:
                    continue
                if axis == 0:
                    r = np.abs(b[np.ix_(sel, cols)] - a[np.ix_(sel + sh, cols)]).mean()
                else:
                    r = np.abs(b[np.ix_(rows, sel)] - a[np.ix_(rows, sel + sh)]).mean()
                best = min(best, float(r))
        if best <= 0.6 * r0:
            hits += 1
            if hits >= 2:
                return True
    return False


def run_clip(ctx, debug=False):
    pad = int(round(PAD_S * ctx.fps))
    for (a0, b0) in _episodes(ctx):
        if not _maybe_pan(ctx, a0, b0):
            ctx.episodes.append({"start": a0, "end": b0, "kind": "other", "ok": False, "jump_points": 0})
            continue
        an = _analyse(ctx, a0, b0)
        if an is not None:
            per = an["per"]
            grow_a = not per[0].get("dup") and a0 > 0
            grow_b = not per[-1].get("dup") and b0 < ctx.n - 1
            if grow_a or grow_b:   # the motion fills the window: widen it once
                a0 = max(0, a0 - pad) if grow_a else a0
                b0 = min(ctx.n - 1, b0 + pad) if grow_b else b0
                an = _analyse(ctx, a0, b0) or an
        if an is None:
            ctx.episodes.append({"start": a0, "end": b0, "kind": "other", "ok": False, "jump_points": 0})
            continue
        kind, ok, info = _judge(ctx, a0, b0, an)
        if kind == "pan":   # only pans get the (costlier) independent-motion test, then are judged again
            fr = an["fr"]
            for i, p in enumerate(an["per"]):
                if not p.get("dup"):
                    p["indep"] = _independent(ctx, fr[i], fr[i + 1], p)
            kind, ok, info = _judge(ctx, a0, b0, an)
        an.pop("fr", None)
        if debug:
            _dump(ctx, a0, b0, an, kind, info)
        ctx.episodes.append({"start": a0, "end": b0, "kind": kind, "ok": bool(ok),
                             "jump_points": len(info.get("bad", []))})
        if kind != "pan":
            continue
        t0, t1 = ctx.t(a0), ctx.t(b0)
        head = f"{info['dir']} pan of {info['total']:.0f} px"
        if info["reasons"]:
            ctx.add("Q-31", t0, t1, f"{head} is not one smooth rigid shift: " + "; ".join(info["reasons"])
                    + f" ({len(info['bad'])} bad frame(s), one episode)", jump_points=len(info["bad"]),
                    **({"ride_along": info["ride_along"]} if info.get("ride_along") else {}))
        nb = info["neighbour"]
        nb_text = (f"neighbouring steps differ by up to {max(d for _, d in nb):.1f} px in {len(nb)} place(s), first "
                   f"at {ctx.t(nb[0][0]):.2f} s (limit {ctx.px(NEIGHBOUR_PX):.1f} px: an uneven pan)") if nb else ""
        if info["dup_fail"]:
            ctx.add("Q-34", t0, t1, f"{head}: {len(info['dups'])} duplicated frame(s) during the motion (the page "
                    f"paints about every {info['cadence']:.1f} frames)"
                    + (f", with {len(info['doubles'])} double update(s) (first at {ctx.t(info['doubles'][0]):.2f} s)"
                       if info["doubles"] else "") + ("; " + nb_text if nb else "") + "; the page must paint every frame",
                    duplicates=len(info["dups"]), double_updates=len(info["doubles"]), neighbour=len(nb))
        elif info["neighbour_fail"]:
            ctx.add("Q-34", t0, t1, f"{head}: {nb_text}; a pan shows a new, evenly spaced picture on every frame",
                    neighbour=len(nb), max_delta=max(d for _, d in nb))
        elif info["dups"]:
            ctx.add("Q-34", t0, t1, f"{head}: {len(info['dups'])} repeated frame(s) during the motion (about every "
                    f"{info['cadence']:.1f} frames a new picture) but even steps, no double update: dropped paints, "
                    f"not a page timer fault" + ("; " + nb_text if nb else ""), severity="WARN",
                    duplicates=len(info["dups"]), neighbour=len(nb))
        if ok:
            ctx.note("Q-31", f"smooth scroll, {info['total']:.0f} px {info['dir']} in "
                     f"{(info['f1'] - info['f0'] + 1) / ctx.fps:.2f} s, max step {info['max_step']:.0f} px")


def _dump(ctx, a0, b0, an, kind, info):
    print(f"episode {a0}-{b0} ({ctx.t(a0):.2f}-{ctx.t(b0):.2f} s) kind={kind}")
    for i, p in enumerate(an["per"]):
        extra = f" step={info['steps'][i]:5.1f} ratio={info['ratio'][i]:.2f}" if "steps" in info else ""
        print(f"  {a0 + 1 + i:4d} r0={p['r0']:6.2f} y={p[0][2]:+6.2f} ry={p[0][3]:6.2f} x={p[1][2]:+6.2f} "
              f"rx={p[1][3]:6.2f} both={p['both']:6.2f} indep={len(p.get('indep') or [])}{extra}")
    for k in ("reasons", "cadence", "max_step", "median_ratio", "total", "max_delta"):
        if k in info:
            print("  ", k, info[k])
    print("   areas", [round(p.get("area", 0), 3) for p in an["per"] if not p.get("dup")][:12])
    if "dups" in info:
        print("   dups", len(info["dups"]))
