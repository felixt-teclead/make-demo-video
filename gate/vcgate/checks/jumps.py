"""Q-30 every hard jump is explained.

A hard jump is a frame whose scene score (ffmpeg scdet style, 0-100 scale at 480 px wide: the mean absolute frame
difference in percent, reduced by the previous frame's difference) is above 0.45. Jumps less than 0.1 s apart form one
group. A group is explained only by a splice, a click, a step mark or hold start, late content (Q-30 rule), or smooth
motion (a passing Q-31 episode). Anything else is an unexplained hard jump.
"""
import numpy as np

SCENE_THRESHOLD = 0.45   # Q-30 default
GROUP_GAP_S = 0.1
SPLICE_WIN_S = 0.1
CLICK_WIN_S = 1.0
MARK_WIN_S = 1.0
LATE_MAX_S = 2.5
MOVING_MAD = 1.5


def scene_scores(mad):
    mafd = np.asarray(mad) * 100.0 / 256.0
    sc = np.zeros(len(mafd))
    if len(mafd) > 1:
        sc[1:] = np.minimum(mafd[1:], np.abs(np.diff(mafd)))
    return sc


def groups(idx, gap):
    out = []
    for i in idx:
        if out and i - out[-1][-1] < gap:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def run_clip(ctx):
    sc = scene_scores(ctx.mad)
    jumps = np.where(sc > SCENE_THRESHOLD)[0]
    grp = groups([int(i) for i in jumps], max(1, int(round(GROUP_GAP_S * ctx.fps))))
    splices = ctx.splices
    solid_runs = getattr(ctx, "solid_runs", [])
    orphans = getattr(ctx, "orphans", [])
    screens = getattr(ctx, "screens", [])
    tiny = int(round(0.3 * ctx.fps))

    visible_splices = set()
    unexplained, unjudged = [], []
    for g in grp:
        f0, f1 = g[0], g[-1]
        t0, t1 = ctx.t(f0), ctx.t(f1)
        why = None
        # 5. smooth motion (or already covered by a failing Q-31 episode: one defect, one violation)
        for e in ctx.episodes:
            if e.get("kind") == "pan" and e["start"] <= f0 and f1 <= e["end"]:
                why = "smooth motion" if e.get("ok") else "covered by Q-31"
                break
        # 1. a cutter splice within 0.1 s whose frame changes or where the jump starts
        if why is None:
            for s in splices:
                if t0 - SPLICE_WIN_S - 1e-6 <= s <= t1 + SPLICE_WIN_S + 1e-6:
                    fs = int(np.ceil(s * ctx.fps - 1e-6))   # first frame after the splice
                    if (0 <= fs < ctx.n and ctx.mad[fs] >= MOVING_MAD) or fs == f0:
                        why = f"splice at {s:.2f} s"
                        visible_splices.add(s)
                        break
        # 3. the clip's own step mark (the clip start) is always known
        if why is None and t0 <= MARK_WIN_S and ctx.ev is None:
            why = "step mark at 0.00 s"
        if why is None and ctx.ev is None:
            unjudged.append((t0, t1))
            continue
        if why is None:
            for c in ctx.ev.clicks:
                if abs(c["t"] - t0) <= CLICK_WIN_S or abs(c["t"] - t1) <= CLICK_WIN_S:
                    why = f"click at {c['t']:.2f} s"
                    break
        if why is None:
            # a hold the cut removed (t None) has no start in this clip; Q-51 reports it
            for m in list(ctx.ev.marks) + [float(h["t"]) for h in ctx.ev.holds if h.get("t") is not None]:
                if abs(m - t0) <= MARK_WIN_S:
                    why = f"step mark or hold start at {m:.2f} s"
                    break
        if why is None:
            why = _late_content(ctx, f0, f1, t0, solid_runs, orphans, screens, tiny)
            if not why.startswith("not late content"):
                continue
            unexplained.append((t0, t1, f0, f1, why))

    for t0, t1, f0, f1, why in unexplained:
        edge = ""
        for a, b in solid_runs:
            if abs(f0 - a) <= 2 or abs(f0 - (b + 1)) <= 2:
                edge = "; it sits at the edge of a solid stretch"
                break
        near = [s for s in splices if abs(s - t0) <= SPLICE_WIN_S]
        reason = (f"unexplained hard jump at {t0:.2f} s"
                  + (f" (group to {t1:.2f} s)" if f1 > f0 else "")
                  + (", no splice within 0.1 s" if not near else ", the splice frame does not change")
                  + ", no click, mark or hold start within 1.0 s"
                  + ", " + why[len("not late content: "):] + edge)
        ctx.add("Q-30", t0, t1 + 1.0 / ctx.fps, reason)
    if splices:
        ctx.note("Q-30", f"{len(visible_splices)} of {len(splices)} cutter splices are visible as hard jumps")
    if unjudged:
        ctx.note("Q-30", "not judged (no event log): hard-jump groups at "
                 + ", ".join(f"{a:.2f} s" for a, b in unjudged))


def _late_content(ctx, f0, f1, t0, solid_runs, orphans, screens, tiny):
    """Q-30 rule 4: the page's answer to the last click, at most 2.5 s after it, no glide in between, a clean
    switch between two established screens with no blank or orphan within 2 frames."""
    before = [c for c in ctx.ev.clicks if c["t"] <= t0]
    if not before:
        return "not late content: no click before it"
    last = before[-1]
    dt = t0 - last["t"]
    if dt > LATE_MAX_S:
        return f"not late content: it comes {dt:.2f} s after the last click at {last['t']:.2f} s (more than {LATE_MAX_S} s)"
    for c in ctx.ev.clicks:
        g = c.get("glide_t", c["t"])
        if last["t"] < g < t0:
            return f"not late content: a glide starts at {g:.2f} s, between the click and the change"
    for a, b in list(solid_runs) + list(orphans):
        if a - 2 <= f1 and f0 <= b + 2:
            return "not late content: a blank or orphan frame lies within 2 frames of it"
    # established screens on both sides: no movement for 0.3 s before the jump and 0.3 s after it
    pre = ctx.mad[max(1, f0 - tiny):f0]
    post = ctx.mad[f1 + 1:f1 + 1 + tiny]
    est_before = len(pre) > 0 and float(pre.max()) < MOVING_MAD
    est_after = len(post) > 0 and float(post.max()) < MOVING_MAD
    if not (est_before and est_after):
        return "not late content: it is not a clean switch between two established screens"
    return f"late content {dt:.2f} s after the click at {last['t']:.2f} s"
