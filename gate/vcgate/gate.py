"""Gate orchestration: per-clip analysis in parallel worker processes, then take-level checks and the report."""
import os
import time
from concurrent.futures import ProcessPoolExecutor

from . import video
from .model import ClipCtx
from .checks import frames, motion, ripple, cursor, jumps, skeleton, tooltip, take as takechecks

EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_ABORT = 0, 1, 2, 3

# per-clip checks, in dependency order (jumps needs motion episodes and ripples; take checks need all)
CLIP_STAGES = [
    ("frames", frames.run_clip),     # Q-20, Q-21
    ("motion", motion.run_clip),     # Q-31, Q-34 (motion episodes)
    ("ripple", ripple.run_clip),     # Q-45 and ripple detection for Q-44
    ("cursor", cursor.run_clip),     # Q-40, Q-42, tips for Q-43
    ("jumps", jumps.run_clip),       # Q-30
    ("skeleton", skeleton.run_clip), # Q-23 (S2; needs the ripples for click positions)
    ("tooltip", tooltip.run_clip),   # Q-71 stray tooltip-like box (S2)
    ("clip", takechecks.run_clip),   # Q-14, Q-44 matching, holds (Q-37, Q-51, Q-62)
]


def analyse_clip(job):
    """Runs in a worker process. Returns a plain dict (picklable)."""
    name, index, path, events, record, mode, is_last, holds = job
    t0 = time.time()
    try:
        info = video.probe(path, name)
        ctx = ClipCtx(name, index, path, info, events, record, mode)
        ctx.index_holds = holds
        ctx.is_last = is_last
        ctx.load_low()
        ctx.timing["decode_low"] = round(time.time() - t0, 2)
        for stage, fn in CLIP_STAGES:
            ts = time.time()
            fn(ctx)
            ctx.timing[stage] = round(time.time() - ts, 2)
    except video.Unmeasurable as e:
        return {"name": name, "abort": e.reason}
    except Exception as e:   # noqa: BLE001  a gate or tool error is never a statement about the footage (C3, Q-02)
        return {"name": name, "abort": _error_reason(e)}
    return {
        "name": name, "index": index, "frames": ctx.n, "duration": ctx.duration,
        "width": ctx.width, "height": ctx.height,
        "violations": [v.to_dict() for v in ctx.violations], "notes": ctx.notes,
        "tips_head": ctx.tips_head, "tips_tail": ctx.tips_tail,
        "episodes": [{k: v for k, v in e.items() if k in ("start", "end", "kind", "ok")} for e in ctx.episodes],
        "ripples": ctx.ripples, "timing": dict(ctx.timing, total=round(time.time() - t0, 2)),
        "scale": ctx.scale,
    }


def run(take, workers=None):
    """Returns (result dict, exit status)."""
    t_start = time.time()
    if getattr(take, "events_missing", None):
        return abort_result(take, "(take)", f"no event log ({take.events_missing}): the event checks Q-10, Q-11, "
                            "Q-13, Q-44, Q-51 and Q-62 cannot be measured; pass --events FILE, or --no-events for a "
                            "pixel-only diagnostic", t_start), EXIT_ABORT
    jobs = []
    n = len(take.clips)
    for i, c in enumerate(take.clips):
        jobs.append((c["name"], c["index"], c["file"], take.clip_events(c["name"]),
                     take.record.get(c["name"], {}), take.mode, i == n - 1, c.get("holds", [])))
    # longest clips first so the pool stays busy
    order = sorted(range(len(jobs)), key=lambda i: -_size(jobs[i][2]))
    results = [None] * len(jobs)
    workers = workers or min(8, os.cpu_count() or 1)
    if jobs:
        if workers > 1 and len(jobs) > 1:
            with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as ex:
                futs = {i: ex.submit(analyse_clip, jobs[i]) for i in order}
                for i, fu in futs.items():
                    try:
                        results[i] = fu.result()
                    except Exception as e:   # noqa: BLE001  a killed worker (out of memory) or a pickling error
                        results[i] = {"name": jobs[i][0], "abort": _error_reason(e)}
        else:
            for i in order:
                results[i] = analyse_clip(jobs[i])

    aborts = [r for r in results if r and "abort" in r]
    if aborts:
        return {"abort": [{"clip": r["name"], "reason": r["abort"]} for r in aborts], "mode": take.mode,
                "elapsed": round(time.time() - t_start, 2)}, EXIT_ABORT

    violations = [v for r in results for v in r["violations"]]
    try:
        take_viol, take_notes = takechecks.run_take(take, results)
    except Exception as e:   # noqa: BLE001
        return abort_result(take, "(take)", _error_reason(e), t_start), EXIT_ABORT
    violations += take_viol
    fails = [v for v in violations if v["severity"] == "FAIL"]
    warns = [v for v in violations if v["severity"] == "WARN"]
    res = {
        "mode": take.mode, "root": take.root,
        "clips": [{"name": r["name"], "frames": r["frames"], "duration": round(r["duration"], 3),
                   "notes": r["notes"], "timing": r["timing"]} for r in results],
        "ignored": take.ignored, "events": take.events is not None,
        "violations": fails, "warnings": warns, "take_notes": take_notes,
        "verdict": "PASS" if not fails else "FAIL",
        "elapsed": round(time.time() - t_start, 2),
    }
    return res, (EXIT_PASS if not fails else EXIT_FAIL)


def abort_result(take, clip, reason, t_start=None):
    """An ABORT result (Q-02): no verdict, no count; names what could not be measured."""
    return {"abort": [{"clip": clip, "reason": reason}], "mode": getattr(take, "mode", "cut"),
            "elapsed": round(time.time() - t_start, 2) if t_start else 0.0}


def _error_reason(e):
    return f"gate error, could not measure ({type(e).__name__}: {e})"


def _size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0
