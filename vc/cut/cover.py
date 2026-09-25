"""Q-23 skeleton cover (S2, owner-approved 2026-09-25): after a click, hide placeholder frames behind the last
pre-click frame, with a round hole around the click where the live frames show (the ripple and the pointer play on).

Detection follows Q-23 exactly as the gate judges the delivered clip (section Q, Q-23; thresholds are Q-23's
defaults), on the delivered frame sequence (the clip's frame map), at half resolution:
- window: from the click's delivered frame to 2.4 s after it, stopped at the next glide or click and at the clip end;
  the pre-click frame is the delivered frame before the click, the settled view is the window's last frame;
- change area: |settled - pre| >= 16 luma, minus a disc of 110 px around the click; below 2 % of the frame there is
  no view change;
- a skeleton frame: the old view is gone (mean |frame - pre| over the change area >= 10) and the frame carries at
  most 25 % of the settled view's sharp edges (neighbour difference >= 24) in the change area; a plain blend of old
  and new (a cross-fade) is no skeleton.

Cover (fix 2026-09-25, parity viewer finding): the delivered frames from the first skeleton frame until the view is
settled (the first frame from which on every frame of the window carries the settled view: more than 25 % of its sharp
edges and closer to it than to the pre-click frame; so intermittent half-painted or not-quite-gone frames between
the skeletons are covered too), at most 2.0 s, show the pre-click frame. There is no live hole any more: the old
72 px hole showed the loading page through the ripple's translucent fill and, after the ripple had faded, plain
(card text vanishing, a dark arc, the next view's orange highlight). Instead the ripple is drawn onto the pre-click
frame from the overlay's own geometry (vc/overlay/overlay.js: 96 px ring, 5 px border #4da3ff, fill .38, 2 px white
halo .55, 18 px glow .5; scale 0.14 -> 1 on cubic-bezier(0.2,0.7,0.3,1) and opacity 0/1/1/.9/0 at 0/.16/.42/.68/1
over 520 ms), so the ripple keeps playing and the patch shrinks with it to nothing once it is gone. The ripple's
phase per frame comes from the live frames: its start is fitted per click on the opaque border ring (blue pixels
versus the model's ring band) over the frames after the click, which is independent of what page shows under it.
The pointer inside the patch is the pre-click frame's own (the ripple is drawn under it, as in the page).
Seam check (for the one place live and drawn frames meet): on the last live frame before the cover, while the
ripple shows, the drawn ripple must match the live one (mean |live - drawn| luma over the ripple's disc <= 16);
otherwise the cover is skipped and the reason logged. No ring found in the live frames: the cover draws no ripple
(there is none to keep). Every decision is logged in the cut record (clip `covers[]`).

Stdlib only (no numpy): frames are bytes; only the change-area pixels are visited.
"""
import math
import os
import subprocess

from .tools import FPS, tool

WINDOW_S = 2.4          # Q-23: look up to 2.4 s after each click
GONE_LUMA = 10.0        # old view gone: mean |frame - pre| over the change area >= 10
EDGE_SHARE = 0.25       # skeleton: <= 25 % of the settled view's sharp edges
MIN_AREA = 0.02         # the change area must cover >= 2 % of the frame
CLICK_EXCL_PX = 110     # the area within 110 px of the click is excluded
CHANGE_LUMA = 16        # a pixel is in the change area when |settled - pre| >= 16
EDGE_LUMA = 24          # sharp edge: neighbour difference >= 24 luma
BLEND_RESID = 0.15      # plain blend: least-squares residual <= 15 % of |settled - pre|
COVER_MAX_S = 2.0       # Q-23 fix direction: cover at most 2.0 s
SEAM_MAX = 16.0         # skip when the drawn ripple differs from the live one by more than 16 luma (last live frame)
# the overlay's ripple (vc/overlay/overlay.js, docs/overlay.md "Ripple size"); px of the 1920-wide video
RIPPLE_S = 0.520        # RIPPLE_MS
RIPPLE_D = 96.0         # ring outer diameter at scale 1
RIPPLE_BORDER = 5.0     # border 5px #4da3ff
RIPPLE_FILL = 0.38      # background rgba(77,163,255,.38)
RIPPLE_HALO = 2.0       # box-shadow 0 0 0 2px rgba(255,255,255,.55)
RIPPLE_HALO_A = 0.55
RIPPLE_GLOW_SIGMA = 9.0  # box-shadow 0 0 18px rgba(77,163,255,.5): blur 18 px = gaussian sigma 9
RIPPLE_GLOW_A = 0.5
RIPPLE_RGB = (77, 163, 255)
RIPPLE_EXTENT = 80      # patch radius: ring 48 + halo 2 + glow 3 sigma (27) + AA
RIPPLE_OPACITY = ((0.0, 0.0), (0.16, 1.0), (0.42, 1.0), (0.68, 0.9), (1.0, 0.0))
FIT_RANGE_S = (-0.25, 0.10)   # ripple start search around the logged click (s)
FIT_FRAMES = 20         # delivered frames after the click used for the ripple fit
FIT_MIN_BLUE = 150
FADE_TRACK_S = 0.354    # from here (opacity < 0.9) the drawn phase follows the live ring's fade
FADE_MAX_LAG_S = 0.15   # the live fade may run up to 0.15 s behind the clock
NEUTRAL_BR = 12         # fade tracking uses only ring-band pixels that are neutral grey in the pre-click frame
FADE_EXTRA = 6          # live frames decoded ahead for the fade tracking      # fewer ring-blue pixels in all fit frames: no ripple found
# the pointer (overlay.js ARROW, tip at the click): kept from the pre-click frame, drawn above the ripple
ARROW = ((0, 0), (0, 17.6), (4.1, 13.6), (8, 21.6), (11.1, 20.2), (7.5, 12.7), (14.85, 12.5))
ARROW_PAD = 1.5         # stroke (0.75) + anti-aliasing
PRECHECK_AREA = 0.01    # cheap 480x270 pre-check: below 1 % change area the click is not analysed


def _disc_outside(w, h, cx, cy, r):
    """Per-pixel flags (bytearray): 1 where the pixel lies farther than r from (cx, cy)."""
    out = bytearray(b"\x01") * (w * h)
    r2 = r * r
    y0, y1 = max(0, int(math.floor(cy - r))), min(h - 1, int(math.ceil(cy + r)))
    for y in range(y0, y1 + 1):
        dy2 = (y - cy) ** 2
        if dy2 >= r2:
            continue
        dx = math.sqrt(r2 - dy2)
        xa, xb = max(0, int(math.ceil(cx - dx))), min(w - 1, int(math.floor(cx + dx)))
        for x in range(xa, xb + 1):
            if (x - cx) ** 2 + dy2 < r2:
                out[y * w + x] = 0
    return out


def change_area(pre, settled, w, h, cx, cy, excl):
    """Change-area pixel indices: |settled - pre| >= CHANGE_LUMA, outside the disc of radius excl around the click."""
    outside = _disc_outside(w, h, cx, cy, excl) if cx is not None else None
    idx = [i for i, (a, b) in enumerate(zip(settled, pre)) if a - b >= CHANGE_LUMA or b - a >= CHANGE_LUMA]
    if outside is not None:
        idx = [i for i in idx if outside[i]]
    return idx


def edge_count(f, w, idx, limit=None):
    """Sharp-edge pixels of frame f among idx (difference >= EDGE_LUMA to the left or upper neighbour). With
    `limit`, counting stops as soon as the count exceeds it (the caller only needs to know that)."""
    n = 0
    chunk = 4096
    for j in range(0, len(idx), chunk):
        for i in idx[j:j + chunk]:
            v = f[i]
            if i % w and abs(v - f[i - 1]) >= EDGE_LUMA:
                n += 1
            elif i >= w and abs(v - f[i - w]) >= EDGE_LUMA:
                n += 1
        if limit is not None and n > limit:
            return n
    return n


def _gone(f, pre, idx):
    """Q-23 'old view gone': mean |f - pre| over idx >= GONE_LUMA (summing stops once it is certain)."""
    need = GONE_LUMA * len(idx)
    tot = 0
    chunk = 8192
    for j in range(0, len(idx), chunk):
        tot += sum(abs(f[i] - pre[i]) for i in idx[j:j + chunk])
        if tot >= need:
            return True
    return False


def detect(frames, w, h, cx, cy, scale):
    """Q-23 on a window of grey frames (bytes, w x h): frames[0] is the pre-click frame, frames[-1] the settled view.
    cx, cy: the click at this resolution (None: no exclusion); scale: this resolution's px per 1920-video px.
    Returns {judged, area, skeleton[window indices], gone[bool per index], why}. Identical frames (a static
    screen decodes to the same bytes) are judged once."""
    if len(frames) < 3:
        return {"judged": False, "why": "window too short", "skeleton": [], "area": 0.0}
    pre, settled = frames[0], frames[-1]
    idx = change_area(pre, settled, w, h, cx, cy, CLICK_EXCL_PX * scale)
    frac = len(idx) / float(w * h)
    if frac < MIN_AREA:
        return {"judged": True, "why": f"no view change ({100 * frac:.1f} % of the frame)", "skeleton": [],
                "area": frac}
    n_es = max(edge_count(settled, w, idx), 1)
    limit = EDGE_SHARE * n_es
    d = None
    skel, gone = [], [False] * len(frames)
    memo = {}
    for k in range(1, len(frames) - 1):
        f = frames[k]
        if f in memo:
            g, is_skel = memo[f]
        else:
            g, is_skel = _gone(f, pre, idx), False
            if g and edge_count(f, w, idx, limit) <= limit:
                if d is None:
                    d = [settled[i] - pre[i] for i in idx]
                    dd = float(sum(x * x for x in d)) or 1.0
                v = [f[i] - pre[i] for i in idx]
                a = sum(x * y for x, y in zip(v, d)) / dd
                resid = math.sqrt(sum((x - a * y) ** 2 for x, y in zip(v, d)) / dd)
                is_skel = not (0.0 <= a <= 1.0 and resid <= BLEND_RESID)   # a plain cross-fade is no skeleton
            memo[f] = (g, is_skel)
        gone[k] = g
        if is_skel:
            skel.append(k)
    settled_from = None
    if skel:
        settled_from = len(frames) - 1          # the window's last frame is the settled view by definition
        for k in range(len(frames) - 2, skel[-1], -1):
            if not _settled(frames[k], pre, settled, w, idx, limit):
                break
            settled_from = k
    return {"judged": True, "why": "", "skeleton": skel, "gone": gone, "area": frac, "settled_from": settled_from}


def _settled(f, pre, settled, w, idx, limit):
    """The frame carries the settled view: more than EDGE_SHARE of its sharp edges in the change area, and it is
    closer to it than to the pre-click frame (mean |difference| over the change area)."""
    if edge_count(f, w, idx, limit) <= limit:
        return False
    ds = dp = 0
    for i in idx:
        v = f[i]
        ds += abs(v - settled[i])
        dp += abs(v - pre[i])
    return ds < dp


def decide(frames, w, h, cx, cy, scale, max_frames=int(round(COVER_MAX_S * FPS))):
    """Detect and decide one click's cover on its window. Returns the decision dict (window indices): the cover
    runs from the first skeleton frame k0 to the settled frame k1 (exclusive), i.e. through every intermittent
    non-skeleton frame until the view stays settled. The ripple seam is checked later on the full-resolution frames
    (plan_covers)."""
    r = detect(frames, w, h, cx, cy, scale)
    out = {"skeleton_frames": len(r["skeleton"]), "area": round(r["area"], 4)}
    if not r["skeleton"]:
        out.update(status="none", reason=r["why"] or "no skeleton frames")
        return out
    k0, k1 = r["skeleton"][0], r["settled_from"]
    out.update(k0=k0, k1=k1, skeleton=r["skeleton"], skeleton_end=r["skeleton"][-1] + 1)
    if cx is None:
        out.update(status="skipped", reason="click position unknown (no ripple position to keep)")
    elif k1 - k0 > max_frames:
        out.update(status="skipped", reason=f"cover until the view is settled ({k1 - k0} frames) is longer than "
                                            f"{COVER_MAX_S} s")
    elif any(r["gone"][k] for k in range(1, k0)):
        out.update(status="skipped", reason="the view changed before the skeleton frames (not right after the click)")
    else:
        out.update(status="covered", reason="")
    return out


# ---------------------------------------------------------------------------------------------- the drawn ripple
def _bezier(p1x, p1y, p2x, p2y, x):
    """CSS cubic-bezier(p1x, p1y, p2x, p2y) at progress x."""
    lo, hi = 0.0, 1.0
    for _ in range(40):
        t = (lo + hi) / 2
        if 3 * (1 - t) ** 2 * t * p1x + 3 * (1 - t) * t * t * p2x + t ** 3 < x:
            lo = t
        else:
            hi = t
    t = (lo + hi) / 2
    return 3 * (1 - t) ** 2 * t * p1y + 3 * (1 - t) * t * t * p2y + t ** 3


def ripple_phase(tau):
    """(scale, opacity) of the overlay's ripple tau seconds after its start; opacity 0 before and after."""
    if tau < 0 or tau >= RIPPLE_S:
        return 0.14, 0.0
    p = tau / RIPPLE_S
    s = 0.14 + 0.86 * _bezier(0.2, 0.7, 0.3, 1.0, p)
    for (a, va), (b, vb) in zip(RIPPLE_OPACITY, RIPPLE_OPACITY[1:]):
        if a <= p <= b:
            return s, va + (vb - va) * (p - a) / (b - a)
    return s, 0.0


def _cov(x):
    return 0.0 if x <= 0 else 1.0 if x >= 1 else x


def ripple_rgba(d, s, px=1.0):
    """The ripple group at distance d (video px) from its centre at scale s, before the group opacity:
    (premultiplied rgb, alpha). Layers bottom to top: glow, halo (both outside the ring only), fill + border."""
    R = RIPPLE_D / 2 * s * px
    bw, hw, sg = RIPPLE_BORDER * s * px, RIPPLE_HALO * s * px, RIPPLE_GLOW_SIGMA * s * px
    inside = _cov(R - d + 0.5)
    border = _cov(d - (R - bw) + 0.5) * inside
    ea = border + (inside - border) * RIPPLE_FILL
    out = 1.0 - inside
    halo = _cov(R + hw - d + 0.5) * out * RIPPLE_HALO_A
    glow = out * RIPPLE_GLOW_A * 0.5 * math.erfc((d - R) / (sg * math.sqrt(2))) if sg > 0 else 0.0
    c, a = (0.0, 0.0, 0.0), 0.0
    for col, al in ((RIPPLE_RGB, glow), ((255, 255, 255), halo), (RIPPLE_RGB, ea)):
        c = tuple(col[i] * al + c[i] * (1 - al) for i in range(3))
        a = al + a * (1 - al)
    return c, a


def _seg_dist(px_, py_, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px_ - ax) * dx + (py_ - ay) * dy) / (dx * dx + dy * dy or 1.0)))
    return math.hypot(px_ - ax - t * dx, py_ - ay - t * dy)


def arrow_mask(box, cx, cy, px=1.0):
    """Flags (bytearray over the box x0, y0, w, h): 1 where the pointer (the arrow with its tip at the click,
    padded by ARROW_PAD) covers the pixel."""
    x0, y0, w, h = box
    poly = [(cx + ax * px, cy + ay * px) for ax, ay in ARROW]
    edges = list(zip(poly, poly[1:] + poly[:1]))
    out = bytearray(w * h)
    for y in range(max(0, int(cy) - 3 - y0), min(h, int(cy + 25 * px) + 3 - y0)):
        for x in range(max(0, int(cx) - 3 - x0), min(w, int(cx + 17 * px) + 3 - x0)):
            X, Y = x0 + x + 0.5, y0 + y + 0.5
            inside = False
            for (ax, ay), (bx, by) in edges:
                if (ay > Y) != (by > Y) and X < ax + (Y - ay) * (bx - ax) / (by - ay):
                    inside = not inside
            if inside or min(_seg_dist(X, Y, ax, ay, bx, by) for (ax, ay), (bx, by) in edges) <= ARROW_PAD * px:
                out[y * w + x] = 1
    return out


def ripple_box(cx, cy, W, H, px=1.0):
    """The patch box (x0, y0, w, h) around the click that holds the whole ripple (RIPPLE_EXTENT)."""
    r = int(math.ceil(RIPPLE_EXTENT * px))
    x0, y0 = max(0, int(cx) - r), max(0, int(cy) - r)
    return x0, y0, min(W, int(cx) + r + 1) - x0, min(H, int(cy) + r + 1) - y0


def _dists(box, cx, cy):
    x0, y0, w, h = box
    return [math.hypot(x0 + (i % w) + 0.5 - cx, y0 + (i // w) + 0.5 - cy) for i in range(w * h)]


def draw_ripple(pre, box, cx, cy, tau, mask=None, px=1.0, dist=None):
    """The pre-click crop (rgb24 bytes over box) with the ripple drawn at phase tau under the pointer (mask).
    Returns bytes; equals `pre` once the ripple is gone. Depends on the pre-click frame only (no live pixels)."""
    s, op = ripple_phase(tau)
    if op <= 0:
        return bytes(pre)
    dist = dist or _dists(box, cx, cy)
    out = bytearray(pre)
    table = {}
    for i, d in enumerate(dist):
        if mask is not None and mask[i]:
            continue
        q = int(d * 8)
        v = table.get(q)
        if v is None:
            c, a = ripple_rgba((q + 0.5) / 8.0, s, px)
            v = table[q] = (c[0] * op, c[1] * op, c[2] * op, 1.0 - a * op)
        if v[3] >= 0.999:
            continue
        j = 3 * i
        out[j] = min(255, int(pre[j] * v[3] + v[0] + 0.5))
        out[j + 1] = min(255, int(pre[j + 1] * v[3] + v[1] + 0.5))
        out[j + 2] = min(255, int(pre[j + 2] * v[3] + v[2] + 0.5))
    return bytes(out)


def _blue(r, g, b):
    return abs(r - RIPPLE_RGB[0]) + abs(g - RIPPLE_RGB[1]) + abs(b - RIPPLE_RGB[2]) < 90


def fit_ripple(crops, times, box, cx, cy, t_click, px=1.0):
    """Fit the ripple start on live crops (rgb24 over box; times: their video times). The border ring is opaque, so
    its blue pixels do not depend on the page under it (old view, skeleton or new view). Returns
    {found, delta_s, mismatch, blue_px} with the ripple start at t_click + delta_s; mismatch: the share of ring-band
    and blue pixels that disagree at the best fit."""
    dist = _dists(box, cx, cy)
    rmax = RIPPLE_D / 2 * px + 4
    nb = int(rmax * 4) + 1
    hists = []
    n_blue = 0
    for cr in crops:
        tot, blue = [0] * nb, [0] * nb
        for i, d in enumerate(dist):
            if d < rmax:
                q = int(d * 4)
                tot[q] += 1
                j = 3 * i
                if _blue(cr[j], cr[j + 1], cr[j + 2]):
                    blue[q] += 1
        n_blue += sum(blue)
        cb, ct = [0], [0]
        for q in range(nb):
            cb.append(cb[-1] + blue[q])
            ct.append(ct[-1] + tot[q])
        hists.append((cb, ct))
    if n_blue < FIT_MIN_BLUE:
        return {"found": False, "delta_s": None, "mismatch": None, "blue_px": n_blue}
    best = None
    steps = int(round((FIT_RANGE_S[1] - FIT_RANGE_S[0]) * 120))
    for st in range(steps + 1):
        dl = FIT_RANGE_S[0] + st / 120.0
        err = band_px = 0
        for (cb, ct), t in zip(hists, times):
            s, op = ripple_phase(t - t_click - dl)
            nblue = cb[-1]
            if op > 0.5:
                R = RIPPLE_D / 2 * s * px
                a = max(0, min(nb, int((R - RIPPLE_BORDER * s * px + 1) * 4)))
                b = max(0, min(nb, int((R - 1) * 4) + 1))
                inb_blue, inb = cb[b] - cb[a], ct[b] - ct[a]
                err += (inb - inb_blue) + (nblue - inb_blue)
                band_px += inb
            else:
                err += nblue
        key = (err, abs(dl))
        if best is None or key < best[0]:
            best = (key, dl, band_px)
    (err, _), dl, band_px = best
    return {"found": True, "delta_s": round(dl, 4), "mismatch": round(err / float(max(1, band_px + n_blue)), 3),
            "blue_px": n_blue}


def refine_start(pre, crops, times, box, cx, cy, start, mask=None, px=1.0):
    """Refine the fitted ripple start on the live frames over the old view (crops/times: the frames between the
    click and the cover, where the page under the ripple is still the pre-click view): the start within +-3/120 s
    that minimises the mean ripple seam (drawn versus live) over those frames. Returns (start, mean seam or None)."""
    dist = _dists(box, cx, cy)
    best = None
    for st in range(-3, 4):
        s0 = start + st / 120.0
        vals = []
        for c, t in zip(crops, times):
            s, op = ripple_phase(t - s0)
            if op > 0:
                vals.append(ripple_seam(c, draw_ripple(pre, box, cx, cy, t - s0, mask, px, dist), box, cx, cy, s, px))
        if not vals:
            continue
        key = (sum(vals) / len(vals), abs(st))
        if best is None or key < best[0]:
            best = (key, s0)
    if best is None:
        return start, None
    return best[1], round(best[0][0], 2)


def _band_br(rgb, band):
    return sum(rgb[3 * i + 2] - rgb[3 * i] for i in band) / float(max(1, len(band)))


def track_fade(live, pre, band, dist, tau_clock, tau_prev, px=1.0):
    """The ripple phase of a live frame in its fade: the tau in [tau_clock - FADE_MAX_LAG_S, tau_clock] (not before
    tau_prev) whose drawn ring band (the opaque border at full size) is as blue (mean b - r) as the live one; ties go
    to the later tau. The border covers the page, so this barely depends on the page under it."""
    want = _band_br(live, band)
    lo = tau_clock - FADE_MAX_LAG_S
    if tau_prev is not None:
        lo = max(lo, tau_prev)
    best = None
    steps = max(0, int(round((tau_clock - lo) * 240)))
    for st in range(steps + 1):
        tau = tau_clock - st / 240.0
        s, op = ripple_phase(tau)
        tab = {}
        tot = 0.0
        for i in band:
            q = int(dist[i] * 8)
            v = tab.get(q)
            if v is None:
                c, a = ripple_rgba((q + 0.5) / 8.0, s, px)
                v = tab[q] = (c[0] * op, c[2] * op, 1.0 - a * op)
            tot += (pre[3 * i + 2] * v[2] + v[1]) - (pre[3 * i] * v[2] + v[0])
        e = abs(tot / max(1, len(band)) - want)
        if best is None or e < best[0] - 0.5:
            best = (e, tau)
    return best[1] if best else tau_clock


def _luma(rgb, i):
    j = 3 * i
    return 0.299 * rgb[j] + 0.587 * rgb[j + 1] + 0.114 * rgb[j + 2]


def ripple_seam(live, drawn, box, cx, cy, s, px=1.0):
    """Mean |live - drawn| luma over the ripple's disc (ring + halo + 4 px) on one frame."""
    r = (RIPPLE_D / 2 + RIPPLE_HALO) * s * px + 4
    idx = [i for i, d in enumerate(_dists(box, cx, cy)) if d <= r]
    return sum(abs(_luma(live, i) - _luma(drawn, i)) for i in idx) / max(1, len(idx))


# ---------------------------------------------------------------------------------------------------- on a take
def click_windows(plan, clicks_k, stops_k):
    """Delivered windows (f0, fe) per click of one clip, as the gate cuts them: pre-click frame f0 = kc - 1, last
    frame fe = min(kc + 2.4 s, next glide/click - 1, K - 1)."""
    K = len(plan["fmap"])
    out = []
    for c in clicks_k:
        kc = c["k"]
        nxt = [s for s in stops_k if s > kc]
        fe = min([kc + int(round(WINDOW_S * FPS)), K - 1] + ([min(nxt) - 1] if nxt else []))
        f0 = max(0, kc - 1)
        out.append((c, f0, fe))
    return out


def _seek(frame):
    """Input seek that lands exactly on raw frame `frame` (half a frame early; ffmpeg's accurate seek drops the rest)."""
    return ["-ss", "%.6f" % ((frame - 0.5) / FPS)] if frame > 0 else []


def decode_range(raw, s, e, w, h):
    """Raw frames s..e (inclusive) at w x h grey, as a list of bytes."""
    p = subprocess.run([tool("ffmpeg"), "-v", "error", "-nostdin"] + _seek(s) + ["-i", raw, "-map", "0:v:0",
                        "-frames:v", str(e - s + 1), "-fps_mode", "passthrough",
                        "-vf", f"scale={w}:{h}:flags=area,format=gray",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
    n = w * h
    if p.returncode != 0 or len(p.stdout) != n * (e - s + 1):
        raise RuntimeError(f"cover decode of raw frames {s}-{e} failed: " + p.stderr.decode(errors="replace")[-1500:])
    return [p.stdout[i:i + n] for i in range(0, len(p.stdout), n)]


def _precheck(an, fmap, f0, fe, x, y, W):
    """Cheap test on the analysis' 480x270 frames: does the view change over >= 1 % of the frame?"""
    from .analyze import MW, MH
    a, b = an.mid(fmap[f0]), an.mid(fmap[fe])
    s = MW / float(W)          # click position: px of this video; thresholds: px of the 1920-wide video
    idx = change_area(a, b, MW, MH, None if x is None else x * s, None if y is None else y * s,
                      CLICK_EXCL_PX * MW / 1920.0)
    return len(idx) / float(MW * MH) >= PRECHECK_AREA


def plan_covers(raw, an, plans, clip_clicks, W, H, work):
    """For every clip plan and its clicks, decide the covers. clip_clicks[i]: list of {k, x, y, t, label} plus
    stops (delivered frames of every click and glide) under key 'stops'. Sets plan['covers'] (the record entries)
    and plan['cover_render'] ([{k0, k1, png, patch}]) on each plan. Returns the list of cover images to make."""
    w, h = (W // 2) & ~1, (H // 2) & ~1
    pos = w / float(W)         # click position (px of this video) -> half resolution
    scale = w / 1920.0         # thresholds are px of the 1920-wide video (Q conventions)
    jobs = []
    for p, cc in zip(plans, clip_clicks):
        p["covers"], p["cover_render"] = [], []
        for c, f0, fe in click_windows(p, cc["clicks"], cc["stops"]):
            if fe - c["k"] < 2 or f0 >= c["k"]:
                continue
            if not _precheck(an, p["fmap"], f0, fe, c.get("x"), c.get("y"), W):
                continue
            jobs.append((p, c, f0, fe))
    if not jobs:
        return []
    images = []
    px = W / 1920.0            # ripple geometry: px of the 1920-wide video
    for p, c, f0, fe in jobs:
        fm = p["fmap"]
        raw_frames = decode_range(raw, fm[f0], fm[fe], w, h)
        frames = [raw_frames[fm[k] - fm[f0]] for k in range(f0, fe + 1)]
        x, y = c.get("x"), c.get("y")
        cx, cy = (None, None) if x is None or y is None else (float(x) * pos, float(y) * pos)
        d = decide(frames, w, h, cx, cy, scale)
        if d["status"] == "none":
            continue
        entry = {"click_t": round(c["k"] / FPS, 4), "src_t": c.get("t"), "label": c.get("label"),
                 "x": x, "y": y, "status": d["status"], "reason": d["reason"],
                 "skeleton_frames": d["skeleton_frames"], "seam": None, "ripple": None,
                 "change_area": d["area"], "pre_frame": f0, "pre_src_frame": fm[f0]}
        if "k0" in d:
            entry["skeleton_run"] = [f0 + d["k0"], f0 + d["skeleton_end"]]
        rp = None
        if d["status"] == "covered":
            k0, k1 = f0 + d["k0"], f0 + d["k1"]
            rp = ripple_plan(raw, fm, c, f0, k0, k1, fe, W, H, px, os.path.join(work, f"cover-{len(images):02d}"))
            entry["ripple"], entry["seam"] = rp["record"], rp["seam"]
            if rp["seam"] is not None and rp["seam"] > SEAM_MAX:
                entry.update(status="skipped", reason=f"seam {rp['seam']:.1f} luma between the live and the drawn "
                                                      f"ripple > {SEAM_MAX:g}")
        if entry["status"] == "covered":
            entry.update(frames=[k0, k1], clip_start=round(k0 / FPS, 4), clip_end=round(k1 / FPS, 4),
                         covered_frames=k1 - k0, src_frames=[fm[k0], fm[k1 - 1] + 1])
            img = os.path.join(work, f"cover-{len(images):02d}.png")
            images.append({"raw_frame": fm[f0], "path": img})
            p["cover_render"].append({"k0": k0, "k1": k1, "png": img, "patch": rp["patch"]})
        else:
            entry.update(frames=None, covered_frames=0,
                         skeleton_window=[f0 + d["k0"], f0 + d["k1"]] if "k0" in d else None)
        p["covers"].append(entry)
    if images:
        make_images(raw, images)
    return images


def decode_rgb(raw, s, e, box):
    """Raw frames s..e (inclusive), full resolution rgb24, cropped to box (x0, y0, w, h); list of bytes. The crop
    follows the full-frame conversion, so the pixels equal the cover image's (make_images)."""
    x0, y0, bw, bh = box
    p = subprocess.run([tool("ffmpeg"), "-v", "error", "-nostdin"] + _seek(s) + ["-i", raw, "-map", "0:v:0",
                        "-frames:v", str(e - s + 1), "-fps_mode", "passthrough",
                        "-vf", f"format=rgb24,crop={bw}:{bh}:{x0}:{y0}",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    n = bw * bh * 3
    if p.returncode != 0 or len(p.stdout) != n * (e - s + 1):
        raise RuntimeError(f"cover decode of raw frames {s}-{e} failed: " + p.stderr.decode(errors="replace")[-1500:])
    return [p.stdout[i:i + n] for i in range(0, len(p.stdout), n)]


def ripple_plan(raw, fm, c, f0, k0, k1, fe, W, H, px, stem):
    """The drawn ripple of one cover: fit its start on the live frames after the click, draw it onto the pre-click
    frame for every covered frame while it shows (PNG patches stem-NNN.png from k0 on), check the seam on the last
    live frame before the cover. Returns {record, seam, patch: None | {pattern, x, y, n}}."""
    cx, cy = float(c["x"]), float(c["y"])
    box = ripple_box(cx, cy, W, H, px)
    kc = f0 + 1
    kf = min(fe, kc + FIT_FRAMES - 1)
    ks = [f0] + list(range(kc, kf + 1))
    crops_raw = decode_rgb(raw, fm[f0], max(fm[kf], fm[k0 - 1]), box)
    crop = {k: crops_raw[fm[k] - fm[f0]] for k in set(ks) | set(range(kc, k0))}
    pre = crop[f0]
    t_click = float(c["t"]) if c.get("t") is not None else kc / FPS
    fit = fit_ripple([crop[k] for k in ks[1:]], [fm[k] / FPS for k in ks[1:]], box, cx, cy, t_click, px)
    rec = {"found": fit["found"], "delta_s": fit["delta_s"], "fit_mismatch": fit["mismatch"],
           "blue_px": fit["blue_px"], "box": list(box), "drawn_frames": 0}
    if not fit["found"]:
        return {"record": rec, "seam": None, "patch": None}
    mask = arrow_mask(box, cx, cy, px)
    dist = _dists(box, cx, cy)
    old_view = list(range(kc, k0))                  # live frames between the click and the cover (old view)
    start, rec["refine_seam"] = refine_start(pre, [crop[k] for k in old_view], [fm[k] / FPS for k in old_view],
                                             box, cx, cy, t_click + fit["delta_s"], mask, px)
    rec["start_s"] = round(start - t_click, 4)
    seam_v = None
    if k0 - 1 >= kc:
        s_, op = ripple_phase(fm[k0 - 1] / FPS - start)
        if op > 0:
            drawn = draw_ripple(pre, box, cx, cy, fm[k0 - 1] / FPS - start, mask, px, dist)
            seam_v = round(ripple_seam(crop[k0 - 1], drawn, box, cx, cy, s_, px), 2)
    # the fade: the live ripple can run a few frames behind the clock while the page loads (c1 t1: 2 frames); its
    # phase is tracked on the live ring band so the drawn ripple never ends before the live one (no faint ring
    # reappearing after the cover)
    band = [i for i, d in enumerate(dist) if (RIPPLE_D / 2 - RIPPLE_BORDER + 0.5) * px <= d <= (RIPPLE_D / 2 - 0.5) * px
            and not mask[i] and abs(pre[3 * i + 2] - pre[3 * i]) <= NEUTRAL_BR]
    n, tau_prev, lag = 0, None, 0.0
    for k in range(k0, k1):
        tau = fm[k] / FPS - start
        if tau >= FADE_TRACK_S:
            if fm[k] - fm[f0] >= len(crops_raw):
                crops_raw += decode_rgb(raw, fm[f0] + len(crops_raw), min(fm[k] + FADE_EXTRA, fm[k1 - 1]), box)
            tau = track_fade(crops_raw[fm[k] - fm[f0]], pre, band, dist, tau, tau_prev, px)
            lag = max(lag, fm[k] / FPS - start - tau)
        if tau_prev is not None:
            tau = max(tau, tau_prev)
        tau_prev = tau
        if ripple_phase(tau)[1] <= 0 and tau >= 0:
            break
        write_png(f"{stem}-{n:03d}.png", box[2], box[3], draw_ripple(pre, box, cx, cy, tau, mask, px, dist))
        n += 1
    rec["drawn_frames"], rec["fade_lag_s"] = n, round(lag, 4)
    patch = {"pattern": f"{stem}-%03d.png", "x": box[0], "y": box[1], "n": n} if n else None
    return {"record": rec, "seam": seam_v, "patch": patch}


def write_png(path, w, h, rgb):
    """rgb24 bytes -> PNG (stdlib)."""
    import struct
    import zlib

    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xffffffff)
    rows = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def make_images(raw, images):
    """Each cover image is its pre-click frame (rgb24, opaque; no hole: the ripple is drawn as patches)."""
    for im in images:
        p = subprocess.run([tool("ffmpeg"), "-v", "error", "-nostdin", "-y"] + _seek(im["raw_frame"])
                           + ["-i", raw, "-map", "0:v:0", "-frames:v", "1", "-fps_mode", "passthrough",
                              "-vf", "format=rgb24", im["path"]],
                           capture_output=True, text=True)
        if p.returncode != 0 or not os.path.exists(im["path"]):
            raise RuntimeError("cover image failed: " + p.stderr[-1500:])
