"""Q-23 no skeleton frames (S2).

Invariant (Q-01): after a click the old view gives way to the settled new view; no frame in between shows a view that
is neither (placeholder bars, a half-loaded page, an empty shell). Per logged click:

- window: from the click to 2.4 s after it, stopped at the next glide or click and at the clip end;
- the settled view is the window's last frame; the view changes where the settled frame differs from the pre-click
  frame by >= CHANGE_LUMA (half resolution), minus a disc of 110 px around the click (the ripple and the pointer);
  a change area below 2 % of the frame is no view change (nothing to judge);
- a frame is a skeleton frame when the old view is gone (mean difference from the pre-click frame >= 10 luma over the
  change area) and it carries at most 25 % of the settled view's sharp edges in that area (edge pixel count, so a view
  that settles a few px off still counts as carrying them);
- a plain blend of old and new (a cross-fade: the frame fits (1-a)*old + a*new with a small residual) is no skeleton.

1-2 skeleton frames for a click are a WARN, more than 2 a FAIL; one violation per click (Q-06).
"""
import numpy as np

from .. import video

WINDOW_S = 2.4          # look up to 2.4 s after each click
GONE_LUMA = 10.0        # old view gone: mean |frame - pre| over the change area >= 10
EDGE_SHARE = 0.25       # skeleton: <= 25 % of the settled view's sharp edges
MIN_AREA = 0.02         # the change area must cover >= 2 % of the frame
CLICK_EXCL_PX = 110     # area within 110 px of the click is excluded
CHANGE_LUMA = 16        # a half-res pixel is part of the change area when |settled - pre| >= 16
EDGE_LUMA = 24          # sharp edge: neighbour difference >= 24 luma at half resolution
BLEND_RESID = 0.15      # plain blend: least-squares residual <= 15 % of |settled - pre| (RMS); real fades fit to ~2 %
WARN_MAX = 2            # 1-2 frames WARN, more FAIL


def _edges(a):
    a = a.astype(np.int16)
    e = np.zeros(a.shape, bool)
    e[:, 1:] |= np.abs(a[:, 1:] - a[:, :-1]) >= EDGE_LUMA
    e[1:, :] |= np.abs(a[1:, :] - a[:-1, :]) >= EDGE_LUMA
    return e


def _click_xy(ctx, c):
    if c.get("x") is not None and c.get("y") is not None:
        return float(c["x"]), float(c["y"]), "logged"
    # no logged position: the click's own ripple (Q-44 detection) marks it
    near = [r for r in ctx.ripples if abs(r["t0"] - float(c["t"])) <= 0.5]
    if near:
        r = min(near, key=lambda r: abs(r["t0"] - float(c["t"])))
        return float(r["x"]), float(r["y"]), "ripple"
    return None, None, "unknown"


def analyse_click(ctx, c, end_t):
    """Returns dict(frames=[frame indices], judged=bool, why=str, area=float)."""
    fc = ctx.f(float(c["t"]))
    f0 = max(0, fc - 1)                       # the pre-click frame
    f1 = min(ctx.n - 1, ctx.f(end_t))
    if f1 - fc < 2:
        return {"frames": [], "judged": False, "why": "window too short"}
    w = (ctx.width // 2) & ~1
    h = (ctx.height // 2) & ~1
    fr = video.decode_scaled(ctx.path, ctx.name, w, h, f0, f1 - f0 + 1, ctx.fps)
    if len(fr) < 3:
        return {"frames": [], "judged": False, "why": "window not decodable"}
    pre = fr[0].astype(np.int16)
    settled = fr[-1].astype(np.int16)
    diff = settled - pre
    area = np.abs(diff) >= CHANGE_LUMA
    x, y, src = _click_xy(ctx, c)
    if x is not None:
        sx, sy = w / ctx.width, h / ctx.height
        yy, xx = np.ogrid[:h, :w]
        r = ctx.px(CLICK_EXCL_PX) * sx
        area &= ((xx - x * sx) ** 2 + (yy - y * sy) ** 2) > r * r
    frac = float(area.mean())
    if frac < MIN_AREA:
        return {"frames": [], "judged": True, "why": f"no view change ({100 * frac:.1f} % of the frame)", "area": frac}
    es = _edges(settled) & area
    n_es = max(int(es.sum()), 1)
    d = diff[area].astype(np.float64)
    dd = float((d * d).sum()) or 1.0
    out = []
    for k in range(1, len(fr) - 1):
        f = fr[k].astype(np.int16)
        v = (f - pre)[area].astype(np.float64)
        if float(np.abs(v).mean()) < GONE_LUMA:
            continue                           # the old view is still there
        share = int((_edges(f) & area).sum()) / n_es
        if share > EDGE_SHARE:
            continue                           # carries the new view's detail
        a = float((v * d).sum()) / dd
        resid = float(np.sqrt(((v - a * d) ** 2).sum() / dd))
        if 0.0 <= a <= 1.0 and resid <= BLEND_RESID:
            continue                           # a plain cross-fade between old and new
        out.append(f0 + k)
    return {"frames": out, "judged": True, "why": "", "area": frac, "pos": src}


def run_clip(ctx):
    for cv in ctx.record.get("covers") or []:
        if cv.get("status") == "bridged":     # report only: the gate judges the bridged clip like any other
            lab = f" \"{cv['label']}\"" if cv.get("label") else ""
            ctx.note("Q-23", f"cutter: click{lab} at {cv.get('click_t', 0):.2f} s: {cv.get('summary')}")
    if ctx.ev is None:
        ctx.note("Q-23", "not judged: no event log (skeleton frames are counted per logged click)")
        return
    clicks = ctx.ev.clicks
    if not clicks:
        return
    stops = sorted([float(c["t"]) for c in clicks] + [float(c["glide_t"]) for c in clicks if c.get("glide_t") is not None]
                   + [float(a["t"]) for a in ctx.ev.actions if a.get("type") in ("glide", "move") and a.get("t") is not None])
    counts = []
    for c in clicks:
        t = float(c["t"])
        nxt = [s for s in stops if s > t + 1e-6]
        end_t = min([t + WINDOW_S, ctx.duration - 1.0 / ctx.fps] + ([nxt[0] - 1.0 / ctx.fps] if nxt else []))
        r = analyse_click(ctx, c, end_t)
        n = len(r["frames"])
        counts.append(f"{t:.2f} s: {n}" if r["judged"] else f"{t:.2f} s: not judged ({r['why']})")
        if not n:
            continue
        lab = f" \"{c['label']}\"" if c.get("label") else ""
        fr = r["frames"]
        t0, t1 = fr[0] / ctx.fps, (fr[-1] + 1) / ctx.fps
        sev = "WARN" if n <= WARN_MAX else "FAIL"
        ctx.add("Q-23", t0, t1,
                f"{n} skeleton frame{'s' if n != 1 else ''} after the click{lab} at {t:.2f} s: the old view is gone "
                f"but the frame shows at most {int(EDGE_SHARE * 100)} % of the settled view's detail "
                f"({'1-2 frames warn' if sev == 'WARN' else 'more than 2 fail'})",
                severity=sev, frames=n, click_t=t)
    ctx.note("Q-23", "skeleton frames per click: " + "; ".join(counts))
