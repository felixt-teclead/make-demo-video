"""Q-20 no solid frames; Q-21 no blank runs and no orphan frames.

Invariants (Q-01): no stretch of a flat screen lasts more than 3 frames, whatever its colour; every screen the viewer
sees lasts at least 0.3 s unless it is a plain blend between the screens before and after it.
"""
import numpy as np

SOLID_RANGE = 12        # Q-20: luma max - min < 12 (0.1 % extremes ignored) = solid
SOLID_MAX_FRAMES = 3    # Q-20: more than 3 frames (0.10 s) fails
WHITE_MEAN, BLACK_MEAN = 230, 25
SAME_SCREEN = 6.0       # Q-21: 16x9 thumbnails within a mean difference of 6 luma are the same screen
TINY_S = 0.3            # Q-21: a screen shorter than 0.3 s is tiny
BLEND_MAX_S = 0.5       # Q-21: a plain blend lasts at most 0.5 s
BLEND_RATIO = 1.25      # Q-21: d(A,x) + d(x,B) <= 1.25 d(A,B)
EDGE_INSET = 8          # Q-21 blank confirmation at full resolution
EXTREME_FRAC = 0.001


def run_clip(ctx):
    low = ctx.low
    n = ctx.n
    flat = low.reshape(n, -1)
    # cheap prefilter, then robust range on candidates only
    rng = np.full(n, 255.0)
    mean = flat.mean(axis=1)
    cand = np.where(flat.std(axis=1) < 8)[0]
    for i in cand:
        lo, hi = np.percentile(flat[i], [100 * EXTREME_FRAC, 100 * (1 - EXTREME_FRAC)])
        rng[i] = hi - lo
    solid = rng < SOLID_RANGE
    ctx.solid = solid
    ctx.frame_mean = mean
    _q20(ctx, solid, mean)
    ctx.thumbs = _thumbs(low)
    _q21(ctx, solid)


def _runs(mask):
    runs, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return runs


def _colour(m):
    if m > WHITE_MEAN:
        return "WHITE"
    if m < BLACK_MEAN:
        return "BLACK"
    return "flat colour (grey level %d)" % round(m)


def _q20(ctx, solid, mean):
    ctx.solid_runs = []
    for a, b in _runs(solid):
        nfr = b - a + 1
        ctx.solid_runs.append((a, b))
        if nfr > SOLID_MAX_FRAMES:
            m = float(mean[a:b + 1].mean())
            col = _colour(m)
            ctx.add("Q-20", ctx.t(a), ctx.t(b + 1),
                    f"{col} solid stretch of {nfr} frames ({nfr / ctx.fps:.2f} s, mean luma {m:.0f}) from "
                    f"{ctx.t(a):.2f} to {ctx.t(b + 1):.2f} s; a blank reads as a crash", frames=nfr, colour=col)


def _thumbs(low):
    n, h, w = low.shape
    re = np.linspace(0, h, 10).astype(int)[:-1]
    ce = np.linspace(0, w, 17).astype(int)[:-1]
    a = low.astype(np.float32)
    s = np.add.reduceat(np.add.reduceat(a, re, axis=1), ce, axis=2)
    cnt = np.outer(np.diff(np.r_[re, h]), np.diff(np.r_[ce, w]))
    return s / cnt


def _d(x, y):
    return float(np.abs(x - y).mean())


def _blank_full(ctx, i):
    """Q-21: confirm a blank candidate at full resolution (8 px inset, 0.1 % extremes ignored)."""
    fr = ctx.full(i, 1, "gray")
    if not len(fr):
        return False
    g = fr[0, EDGE_INSET:-EDGE_INSET, EDGE_INSET:-EDGE_INSET].ravel()
    lo, hi = np.percentile(g, [100 * EXTREME_FRAC, 100 * (1 - EXTREME_FRAC)])
    return (hi - lo) < SOLID_RANGE


def _q21(ctx, solid):
    th = ctx.thumbs
    n = ctx.n
    if n < 3:
        ctx.screens = [(0, n - 1)]
        return
    # screens: chains of frames whose thumbnails stay within SAME_SCREEN of the previous frame
    d = np.zeros(n)
    d[1:] = np.abs(np.diff(th, axis=0)).mean(axis=(1, 2))
    starts = [0] + [i for i in range(1, n) if d[i] > SAME_SCREEN]
    screens = [(s, (starts[k + 1] - 1) if k + 1 < len(starts) else n - 1) for k, s in enumerate(starts)]
    ctx.screens = screens
    ctx.orphans = []
    tiny = max(1, int(round(TINY_S * ctx.fps)))
    blend_max = int(round(BLEND_MAX_S * ctx.fps))

    # group consecutive tiny screens into one anomaly (Q-06), with the established screens around them
    k = 0
    while k < len(screens):
        s, e = screens[k]
        if e - s + 1 >= tiny:
            k += 1
            continue
        k2 = k
        while k2 + 1 < len(screens) and screens[k2 + 1][1] - screens[k2 + 1][0] + 1 < tiny:
            k2 += 1
        a0, a1 = screens[k][0], screens[k2][1]
        k = k2 + 1
        nfr = a1 - a0 + 1
        blank = [i for i in range(a0, a1 + 1) if solid[i] or float(np.ptp(th[i])) < SOLID_RANGE]
        blank = [i for i in blank if _blank_full(ctx, i)]
        if a0 == 0 or a1 == n - 1:
            if a0 == 0 and a1 == n - 1:
                continue  # the whole clip is one unsettled stretch: no screen to place anything against
            # touches the clip edge (review Major 5): no screen on that side, so it cannot be a plain blend between
            # two screens; the cutter never fades inside a clip. A tiny screen at the edge is a flash.
            _edge_flash(ctx, a0, a1, nfr, blank)
            continue
        A, B = th[a0 - 1], th[a1 + 1]
        dab = _d(A, B)
        via = [_d(A, th[i]) + _d(th[i], B) for i in range(a0, a1 + 1)]
        blend = nfr <= blend_max and not blank and all(v <= BLEND_RATIO * dab for v in via)
        if blend:
            continue
        # the frames that are neither blank-free blends nor part of A or B: report those (one violation per group)
        bad = [a0 + j for j, v in enumerate(via) if v > BLEND_RATIO * dab] + blank
        bad = sorted(set(bad)) or list(range(a0, a1 + 1))
        b0, b1 = bad[0], bad[-1]
        nb = len(bad)
        ctx.orphans.append((b0, b1))
        before, after = _screen_name(ctx, a0 - 1), _screen_name(ctx, a1 + 1)
        what = "blank run" if blank else "orphan"
        ctx.add("Q-21", ctx.t(b0), ctx.t(b1 + 1),
                f"{what} of {nb} frame{'s' if nb != 1 else ''} at {ctx.t(b0):.2f}"
                + (f"-{ctx.t(b1 + 1):.2f}" if nb > 1 else "")
                + f" s between the screen {before} and the screen {after}; it is not a plain blend of them"
                + (f" (the unsettled stretch {ctx.t(a0):.2f}-{ctx.t(a1 + 1):.2f} s has {nfr} frames)" if nfr != nb else ""),
                frames=nb, blank=bool(blank), d_ab=round(dab, 2), d_via=round(max(via), 2))


def _edge_flash(ctx, a0, a1, nfr, blank):
    ctx.orphans.append((a0, a1))
    start = a0 == 0
    other = _screen_name(ctx, a1 + 1 if start else a0 - 1)
    what = "blank run" if blank else "orphan"
    ctx.add("Q-21", ctx.t(a0), ctx.t(a1 + 1),
            f"{what} of {nfr} frame{'s' if nfr != 1 else ''} at {ctx.t(a0):.2f}"
            + (f"-{ctx.t(a1 + 1):.2f}" if nfr > 1 else "")
            + f" s at the clip {'start' if start else 'end'}, {'before' if start else 'after'} the screen {other}: "
              f"a screen that flashes up for less than {TINY_S} s at a clip edge is not a blend of two screens",
            frames=nfr, blank=bool(blank), edge="start" if start else "end")


def _screen_name(ctx, i):
    for s, e in ctx.screens:
        if s <= i <= e:
            return f"{ctx.t(s):.2f}-{ctx.t(e + 1):.2f} s"
    return "?"
