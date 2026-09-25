"""Structure checks: every step delivered (Q-10), expected state reached (Q-11), length (Q-13), no collapsed clip
(Q-14), holds kept (Q-51, Q-62) and the still beat after camera moves (Q-37).

Per-clip parts run in the clip worker (run_clip); take-level parts run once (run_take).
"""
import numpy as np

from ..model import STILL_MAD, Violation

HOLD_TOL = 0.25          # Q-51: +-0.25 s per hold
FINAL_HOLD = 12.0        # Q-62: final hold 12.0 s
STILL_BEAT = 0.9         # Q-37: >= 0.9 s of stillness after a pan before the next action
STANDSTILL_MAXDIFF = 12  # Q-14: a clip is all standstill when no frame changes any low-res pixel by more than this


def run_clip(ctx):
    _q14(ctx)
    _holds(ctx)
    _q37(ctx)


def _q14(ctx):
    rec_flag = bool(ctx.record.get("all_standstill"))
    a = ctx.low
    still = ctx.n < 2 or float(ctx.mad[1:].max()) <= STILL_MAD
    if still and ctx.n >= 2:
        # also no local change at all (a glide or a ripple would change a few pixels a lot)
        mx = 0
        for i in range(1, ctx.n, 64):
            blk = a[max(0, i - 1):i + 64].astype(np.int16)
            if len(blk) > 1:
                mx = max(mx, int(np.abs(np.diff(blk, axis=0)).max()))
        still = mx <= STANDSTILL_MAXDIFF
    if still or rec_flag:
        what = []
        if ctx.ev is not None and ctx.ev.clicks:
            what.append("its logged click at " + ", ".join(f"{c['t']:.2f} s" for c in ctx.ev.clicks)
                        + " is not visible")
        ctx.add("Q-14", 0.0, ctx.duration,
                "clip is all standstill (no frame changes" + ("; flagged by the cut record" if rec_flag else "")
                + ")" + ("; " + "; ".join(what) if what else "") + ": it looks like a missing or truncated step")
        return
    if ctx.ev is None:
        return
    # every logged action of the step falls inside its own clip
    tol = 1.0 / ctx.fps
    for c in ctx.ev.clicks:
        if not (-tol <= c["t"] <= ctx.duration + tol):
            ctx.add("Q-14", c["t"], None, f"logged click{_label(c)} at {c['t']:.2f} s lies outside the clip "
                                          f"(0-{ctx.duration:.2f} s): the step's action is not in its own clip")
    for e in ctx.ev.dropped:
        ctx.add("Q-14", e.get("clip_t"), None, f"logged {e.get('type', 'action')}{_label(e)} (raw "
                f"{e.get('src_t', 0) or 0:.2f} s) was removed by the cut: the step's action is not in its clip")
    for a_ in ctx.ev.actions:
        t = a_.get("t")
        if t is not None and not (-tol <= t <= ctx.duration + tol):
            ctx.add("Q-14", t, None, f"logged {a_.get('type', 'action')} at {t:.2f} s lies outside the clip")


def _label(c):
    return f" \"{c['label']}\"" if c.get("label") else ""


def _holds(ctx):
    """Q-51 and Q-62. Holds come from the event log (logged during filming, with the spec's length) and from the
    clip index (what the cutter kept). Without either there is nothing to measure."""
    holds = []
    if ctx.ev is not None:
        holds = list(ctx.ev.holds)
    idx_holds = [h for h in (getattr(ctx, "index_holds", None) or []) if "clip_t" in h]
    if not holds and idx_holds:
        holds = [{"kind": h.get("kind"), "seconds": h.get("seconds"), "t": h["clip_t"],
                  "spec_seconds": h.get("spec_seconds")} for h in idx_holds]
    if ctx.ev is not None and ctx.ev.spec_holds is not None:
        holds = _against_spec(ctx, holds, ctx.ev.spec_holds)
    splices = ctx.splices
    for h in holds:
        kind = h.get("kind") or "hold"
        if h.get("t") is None and h.get("clip_t") is None:
            ctx.add("Q-51", None, None, f"{kind} hold of {h.get('seconds')} s was removed by the cut")
            continue
        t = float(h["t"] if h.get("t") is not None else h["clip_t"])
        kept = h.get("kept_seconds")
        if kept is not None and h.get("seconds") is not None and abs(float(kept) - float(h["seconds"])) > HOLD_TOL:
            ctx.add("Q-51", t, t + float(kept), f"{kind} hold at {t:.2f} s kept {float(kept):.2f} s of its logged "
                                                f"{float(h['seconds']):.2f} s (tolerance {HOLD_TOL} s)")
        sec = h.get("seconds")
        spec = h.get("spec_seconds")
        if kind == "final" and spec is None:
            spec = FINAL_HOLD
        if sec is None:
            ctx.add("Q-51", t, None, f"{kind} hold at {t:.2f} s has no logged length")
            continue
        sec = float(sec)
        if spec is not None and abs(sec - float(spec)) > HOLD_TOL:
            ctx.add("Q-51", t, t + sec, f"{kind} hold is {sec:.2f} s, the spec asks for {float(spec):.2f} s "
                                        f"(tolerance {HOLD_TOL} s)")
        end = t + sec
        if end > ctx.duration + HOLD_TOL:
            ctx.add("Q-51", t, ctx.duration, f"{kind} hold of {sec:.2f} s from {t:.2f} s is cut short: the clip ends "
                                             f"at {ctx.duration:.2f} s")
        cut = [s for s in splices if t + 1e-3 < s < end - 1e-3]
        if cut:
            ctx.add("Q-51", t, end, f"{kind} hold {t:.2f}-{end:.2f} s is trimmed: cutter splice at "
                                    + ", ".join(f"{s:.2f} s" for s in cut))
        if kind == "final":
            _final(ctx, float(spec or FINAL_HOLD))


def _against_spec(ctx, holds, spec):
    """Q-51 Verify: the logged holds match the spec's holds. A spec hold with no logged hold is missing (FAIL); the
    others get their spec length, so one missing hold never switches off the length check of the rest."""
    from ..holds import align
    pairs, missing, _extra = align(spec, holds)
    out = [dict(h) for h in holds]
    for i, j in pairs:
        if spec[i].get("seconds") is not None:
            out[j]["spec_seconds"] = spec[i]["seconds"]
    for i in missing:
        kind = spec[i].get("kind") or "step"
        ctx.add("Q-51", None, None, f"{kind} hold of {spec[i].get('seconds')} s (spec hold {i + 1} of {len(spec)}) is "
                                    f"missing: no such hold was logged in this clip")
    return out


def _final(ctx, need):
    """Q-62: the last clip is at least the final hold long and ends with that much stillness."""
    if ctx.duration + HOLD_TOL < need:
        ctx.add("Q-62", 0.0, ctx.duration, f"final clip is {ctx.duration:.2f} s, shorter than the {need:.1f} s "
                                           f"final hold")
        return
    k = int(round((need - HOLD_TOL) * ctx.fps))
    tail = ctx.mad[max(1, ctx.n - k):]
    bad = np.where(tail > STILL_MAD)[0]
    if len(bad):
        t_bad = (ctx.n - len(tail) + bad[-1]) / ctx.fps
        ctx.add("Q-62", t_bad, ctx.duration, f"final hold is not still: the picture changes at {t_bad:.2f} s, "
                                             f"{ctx.duration - t_bad:.2f} s before the end (needs {need:.1f} s still)")


def _q37(ctx):
    if ctx.ev is None:
        return
    acts = sorted([c.get("glide_t", c["t"]) for c in ctx.ev.clicks]
                  + [a.get("t") for a in ctx.ev.actions if a.get("type") in ("type", "typing", "click")])
    for e in ctx.episodes:
        if e.get("kind") != "pan":
            continue
        # the end of the movement: the last frame that is not still (MAD > 0.5), ease tail included
        seg = ctx.mad[e["start"]:e["end"] + 1 + int(round(0.25 * ctx.fps))]
        mv = np.where(seg > STILL_MAD)[0]
        if not len(mv):
            continue
        t_end = (e["start"] + mv[-1]) / ctx.fps
        nxt = [t for t in acts if t is not None and t > t_end - 1e-6]
        if nxt and nxt[0] - t_end < STILL_BEAT:
            ctx.add("Q-37", t_end, nxt[0], f"only {nxt[0] - t_end:.2f} s of stillness after the pan ending at "
                                           f"{t_end:.2f} s before the next action (needs {STILL_BEAT} s)")


# --------------------------------------------------------------------------- take level

def run_take(take, results):
    out, notes = [], []
    ev = take.events

    # Q-10: every step delivered, in spec order; zero clips is always a FAIL
    names = [r["name"] for r in results]
    steps = (ev or {}).get("steps")
    if not results:
        miss = f"; missing steps: {', '.join(steps)}" if steps else ""
        out.append(_v("Q-10", "take has 0 clips" + miss))
    elif steps:
        missing = [s for s in steps if s not in names]
        extra = [n for n in names if n not in steps]
        if missing:
            out.append(_v("Q-10", f"{len(names)} clips for {len(steps)} steps; missing steps: {', '.join(missing)}"))
        if extra:
            out.append(_v("Q-10", f"clips that are not spec steps: {', '.join(extra)}"))
        common = [n for n in names if n in steps]
        if not missing and common != [s for s in steps if s in common]:
            out.append(_v("Q-10", "clips are not in spec order: " + " -> ".join(names)))
    else:
        notes.append({"check": "Q-10", "text": f"{len(names)} clips; no step list given, clip count not compared"})

    # Q-11: every step reached its Expected state; not a dry run
    if ev is not None:
        if ev.get("dry_run"):
            out.append(_v("Q-11", "the run is a dry run; a dry run is never a take"))
        per_clip = {n: (ev.get("clips") or {}).get(n, {}).get("result") for n in names}
        for r in ev.get("step_results") or []:
            if r.get("name") in per_clip:
                per_clip[r["name"]] = r
        if all(v is None for v in per_clip.values()):
            notes.append({"check": "Q-11", "text": "no per-step results in the event log; not measured"})
        else:
            for n, r in per_clip.items():
                if r is None:
                    out.append(_v("Q-11", f"step {n}: no result logged", clip=n))
                elif not r.get("verified") or r.get("error"):
                    out.append(_v("Q-11", f"step {n}: Expected state not verified"
                                  + (f" ({r['error']})" if r.get("error") else ""), clip=n))

    # Q-13: length within the spec's range, 1 s slack at the top
    total = sum(r["duration"] for r in results)
    rng = (ev or {}).get("length")
    if rng and results:
        lo, hi = float(rng.get("min", 30)), float(rng.get("max", 60))
        if total < lo or total > hi + 1.0:
            out.append(_v("Q-13", f"video length {total:.2f} s is outside the spec's range {lo:g}-{hi:g} s "
                                  f"(+1 s slack)"))
        else:
            notes.append({"check": "Q-13", "text": f"length {total:.2f} s within {lo:g}-{hi:g} s"})
    elif results:
        notes.append({"check": "Q-13", "text": f"length {total:.2f} s; no length range given, not compared"})

    # Q-62: the spec's final hold must exist on the last clip
    if ev is not None and ev.get("final_hold") and results:
        last = results[-1]["name"]
        holds = ((ev.get("clips") or {}).get(last) or {}).get("holds", [])
        if not any(h.get("kind") == "final" for h in holds):
            out.append(_v("Q-62", f"the last clip {last} has no final hold", clip=last))

    # Q-14: events that the cutter removed (mapped out of every clip)
    for e in (ev or {}).get("dropped", []):
        out.append(_v("Q-14", f"logged {e.get('type', 'action')} at raw {e.get('video_t', 0):.2f} s is not in any "
                              f"delivered clip"))

    # Q-43: cursor continuity across clip boundaries
    from . import cursor
    if hasattr(cursor, "run_take"):
        out += cursor.run_take(take, results)
    return out, notes


def _v(check, reason, clip=None, severity="FAIL"):
    return Violation(check, clip, None, None, reason, severity).to_dict()
