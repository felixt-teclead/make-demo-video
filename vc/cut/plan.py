"""The cut plan: which raw frames each clip keeps, at which speed, and why (Q-50 to Q-56, Q-15, Q-22).

All numbers are in raw frames (30 fps). The output of plan_clip() is the clip's frame map (one raw frame per
delivered frame), its per-frame speed and badge opacity, and the cut-record entries.
"""
import math

from .tools import FPS

# Quality rules of section Q (F-19: not knobs).
STILL = 0.5                # MAD <= 0.5 is still
MOVING = 1.5               # MAD >= 1.5 is moving
CAP_STILL = 1.5            # Q-50: a still stretch keeps at most 1.5 s
CAP_NEAR = 2.0             # Q-50: a near-still (merged) stretch keeps at most 2.0 s
MERGE_GAP = 0.6            # Q-50: near-still stretches less than 0.6 s apart merge ...
MERGE_GAP_MAD = 2.0        # ... if the gap itself is near-still
TAIL_KEEP = 0.5            # Q-50: keep the last 0.5 s before the motion that follows
MIN_CUT = 3                # frames; smaller removals are not worth a splice
PROT_BEFORE_GLIDE = 0.2    # Q-52 hint: protect from 0.2 s before the glide ...
PROT_BEFORE_CLICK = 1.0    # ... and at least 1.0 s before the click
PROT_AFTER_CLICK = 1.2     # ... to 1.2 s after the click
PROT_AFTER_NAV_CLICK = 0.65  # a click that navigates: protection ends at 0.65 s
TRANSITION_MAX = 0.3       # Q-22: up to 0.3 s of transitional frames after a blank
EASE_OUT_FRAMES = 3.0      # Q-56/Q-77: the 0.4 s speed ease at 4x = ~3 delivered frames (0.1 s)
MIN_SPEEDUP = 1.0          # s of raw time; shorter waits are not worth a badge
BADGE_OPACITY = 0.8        # Q-77
SPLICE_JOIN_MAD = 1.0      # the two frames a splice joins must look the same (160x90 MAD)
CURSOR_GUARD_PX = 40       # no pixel change within this distance of the known cursor tip across a splice


def _mask_range(mask, a, b, val=True):
    for f in range(max(0, a), min(len(mask), b)):
        mask[f] = val


def runs_of(flags, a, b):
    """Maximal runs [s, e) inside [a, b) where flags[f] is true."""
    out, s = [], None
    for f in range(a, b):
        if flags[f] and s is None:
            s = f
        elif not flags[f] and s is not None:
            out.append((s, f))
            s = None
    if s is not None:
        out.append((s, b))
    return out


class Planner:
    def __init__(self, an, tl, factor=4.0):
        self.an = an
        self.tl = tl
        self.factor = float(factor)
        n = an.n
        # frames protected from any splice or speed change
        self.prot = [False] * n
        self.prot_why = [None] * n
        self.hold = [False] * n
        for h in tl.holds:
            _mask_range(self.hold, h["start"], h["end"])
        blank = [an.blank(f) for f in range(n)]
        self.blank = blank
        for c in tl.clicks:
            ct = c["t"]
            start = ct - PROT_BEFORE_CLICK
            if c["glide"] is not None:
                start = min(start, c["glide"] - PROT_BEFORE_GLIDE)
            nav = c["navigates"] or any(blank[f] for f in range(c["frame"], min(n, c["frame"] + FPS)))
            c["navigates"] = nav
            end = ct + (PROT_AFTER_NAV_CLICK if nav else PROT_AFTER_CLICK)
            for f in range(max(0, int(math.floor(start * FPS))), min(n, int(math.ceil(end * FPS)) + 1)):
                self.prot[f] = True
                self.prot_why[f] = "click"
        for t in tl.typing:
            for f in range(max(0, int((t["start"] - PROT_BEFORE_CLICK) * FPS)), min(n, int(math.ceil((t["end"] + 0.3) * FPS)) + 1)):
                self.prot[f] = True
                self.prot_why[f] = self.prot_why[f] or "typing"
        for sp in tl.spans:
            for f in range(max(0, int((sp["start"] - 0.25) * FPS)), min(n, int(math.ceil((sp["end"] + 0.25) * FPS)) + 1)):
                self.prot[f] = True
                self.prot_why[f] = self.prot_why[f] or sp["type"]

    # ------------------------------------------------------------------ helpers
    def cursor_at(self, f):
        pos = None
        for c in self.tl.clicks:
            if c["frame"] <= f and c.get("x") is not None:
                pos = (float(c["x"]), float(c["y"]))
        return pos

    def splice_ok(self, x, y, width):
        """May frame x-1 be followed directly by frame y? (the frames between are removed)"""
        an = self.an
        jm = an.thumb_mad(x - 1, y)
        if jm > SPLICE_JOIN_MAD:
            return False, {"join_mad": round(jm, 3), "why": "frames differ"}
        cur = self.cursor_at(y)
        if cur is not None:
            sc = 480.0 / width
            cx, cy = cur[0] * sc, cur[1] * sc
            g = CURSOR_GUARD_PX * sc
            ch = an.mid_changes(x - 1, y)
            if any(abs(px - cx) <= g and abs(py - cy) <= g for px, py in ch):
                return False, {"join_mad": round(jm, 3), "why": "change at the cursor"}
        return True, {"join_mad": round(jm, 3)}

    # ------------------------------------------------------------------ one clip
    def plan_clip(self, seg, width):
        an, n = self.an, self.an.n
        a, b = seg["start"], seg["end"]
        keep = [True] * n  # only [a, b) matters
        drops = []

        # 1. blanks, and the transitional frames right after them (Q-53, Q-22)
        for s, e in runs_of(self.blank, a, b):
            _mask_range(keep, s, e, False)
            drops.append({"src_start": s, "src_end": e, "reason": "blank"})
            # the view is settled after the last hard change within 0.3 s of the blank (a sidebar that paints
            # expanded and collapses a frame later, a half-painted layout); frames before it are transitional
            k = e
            for j in range(e + 1, min(b, e + int(TRANSITION_MAX * FPS) + 1)):
                if an.mad[j] >= MOVING and keep[j - 1]:
                    k = j
            if k > e:
                _mask_range(keep, e, k, False)
                drops.append({"src_start": e, "src_end": k, "reason": "transitional after blank"})
        # half-painted new document, when the runner logged the document's ready time (Q-22)
        for nv in self.tl.navs:
            if nv.get("ready") is None:
                continue
            s, e = max(a, int(round(nv["start"] * FPS))), min(b, int(round(nv["ready"] * FPS)))
            if e > s:
                _mask_range(keep, s, e, False)
                drops.append({"src_start": s, "src_end": e, "reason": "half-painted document"})

        # 2. speed-ups: only the step's logged readiness waits (Q-56); never over clicks, holds, typing
        speed = [1.0] * n
        speedups = []
        for w in self.tl.waits:
            ws, we = max(a, int(math.ceil(w["start"] * FPS))), min(b, int(math.floor(w["end"] * FPS)))
            ok = [keep[f] and not self.prot[f] and not self.hold[f] for f in range(n)] if we > ws else None
            if we <= ws:
                continue
            for s, e in runs_of(ok, ws, we):
                s2, e2 = s + 2, e - 2  # the ease reaches ~1.5 raw frames outside the stretch
                if (e2 - s2) / FPS < MIN_SPEEDUP:
                    speedups.append({"src_start": s, "src_end": e, "declined": "shorter than %.1f s" % MIN_SPEEDUP,
                                     "wait": w})
                    continue
                for f in range(s2, e2):
                    speed[f] = self.factor
                speedups.append({"src_start": s2, "src_end": e2, "factor": self.factor, "wait": w})

        # 3. standstill shortening (Q-50)
        still = [False] * n
        for f in range(a, b):
            still[f] = keep[f]
        # a frame continues a still run if it is kept, its predecessor is kept, it changed little and the speed is
        # the same
        runs = []
        s = None
        for f in range(a, b + 1):
            cont = (f < b and keep[f] and s is not None and keep[f - 1] and an.mad[f] <= STILL
                    and speed[f] == speed[f - 1] and not self.hold[f])
            if cont:
                continue
            if s is not None:
                runs.append([s, f, False])
            s = f if (f < b and keep[f] and not self.hold[f]) else None
        runs = [r for r in runs if r[1] - r[0] > 1]
        # merge near-still neighbours (gap < 0.6 s, the gap itself near-still, same speed)
        merged = []
        for r in runs:
            if merged:
                p = merged[-1]
                gap = range(p[1], r[0] + 1)
                if ((r[0] - p[1]) < MERGE_GAP * FPS and all(keep[f] and not self.hold[f] for f in gap)
                        and all(an.mad[f] < MERGE_GAP_MAD for f in gap)
                        and len({speed[f] for f in range(p[0], r[1])}) == 1):
                    p[1], p[2] = r[1], True
                    continue
            merged.append(list(r))
        splices = []
        declined = []
        for rs, re_, near in merged:
            f_speed = speed[rs]
            cap = int(round((CAP_NEAR if near else CAP_STILL) * FPS * f_speed))
            excess = (re_ - rs) - cap
            if excess < MIN_CUT:
                continue
            tail = int(round(TAIL_KEEP * FPS * f_speed))
            removable = runs_of([not self.prot[f] and not self.hold[f] for f in range(n)], rs, re_)
            removable.sort(key=lambda r: r[0] - r[1])  # largest first
            for ra, rb in removable:
                if excess < MIN_CUT:
                    break
                hi = rb - (tail if rb == re_ else 0)
                lo = ra + (1 if ra == rs else 0)
                amount = min(excess, hi - lo)
                if amount < MIN_CUT:
                    continue
                placed = None
                # the removal ends at `hi` by default; slide it earlier if the join would show a change
                for shift in range(0, hi - lo - amount + 1, 3):
                    y = hi - shift
                    x = y - amount
                    ok, info = self.splice_ok(x, y, width)
                    if ok:
                        placed = (x, y, info)
                        break
                if placed is None:
                    declined.append({"src_start": lo, "src_end": hi, "reason": "no invisible join (%s)" % info["why"]})
                    continue
                x, y, info = placed
                _mask_range(keep, x, y, False)
                drops.append({"src_start": x, "src_end": y, "reason": "near-still" if near else "standstill",
                              "run": [rs, re_], "factor": f_speed, "join_mad": info["join_mad"]})
                excess -= amount

        kept = [f for f in range(a, b) if keep[f]]
        return self._timebase(seg, kept, speed, drops, speedups, declined)

    # ------------------------------------------------------------------ output time
    def _timebase(self, seg, kept, speed, drops, speedups, declined):
        """Map delivered frames to raw frames. Each kept raw frame lasts 1/speed delivered frames; speed changes are
        eased linearly over EASE_OUT_FRAMES delivered frames centred on the switch (duration-neutral)."""
        units = [speed[f] for f in kept]  # speed of each kept frame
        # instant-switch output time at the start of each kept frame
        tau = [0.0]
        for u in units:
            tau.append(tau[-1] + 1.0 / u)
        total = tau[-1]
        K = int(round(total))
        # piecewise-constant speed over output time: (tau_start, speed) at each change
        changes = [(0.0, units[0] if units else 1.0)]
        for i in range(1, len(units)):
            if units[i] != units[i - 1]:
                changes.append((tau[i], units[i]))

        def s_inst(t):
            v = changes[0][1]
            for ts, sp in changes:
                if ts <= t:
                    v = sp
                else:
                    break
            return v

        def s_smooth(t):  # box average over EASE_OUT_FRAMES -> linear ramps, preserves the integral
            h = EASE_OUT_FRAMES / 2
            lo, hi = max(0.0, t - h), min(total, t + h)
            if hi <= lo:
                return s_inst(t)
            pts = [lo] + [ts for ts, _ in changes if lo < ts < hi] + [hi]
            acc = 0.0
            for p, q in zip(pts, pts[1:]):
                acc += s_inst((p + q) / 2) * (q - p)
            return acc / (hi - lo)

        fmap, fspeed, badge = [], [], []
        v = 0.0
        sub = 8
        for k in range(K):
            idx = min(len(kept) - 1, int(math.floor(v + 1e-6)))
            fmap.append(kept[idx])
            sp = s_smooth(k + 0.5)
            fspeed.append(round(sp, 3))
            badge.append(round(BADGE_OPACITY * max(0.0, min(1.0, (sp - 1) / (self.factor - 1))), 3)
                         if self.factor > 1 else 0.0)
            for j in range(sub):
                v += s_smooth(k + (j + 0.5) / sub) / sub
        return {"seg": seg, "kept": kept, "fmap": fmap, "speed": fspeed, "badge": badge, "drops": drops,
                "speedups": speedups, "declined": declined}
