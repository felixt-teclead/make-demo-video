"""The cutter: one raw take -> one clip per step + the cut record + the joined full video.

    python3 -m vc.cut cut  RUN_DIR [--out NEW_DIR] [--speed 4] [--crossfade 0.2] [--keep-work]
    python3 -m vc.cut join OUT_DIR                        (re-join existing clips; hard cut / fade per site)

A first cut writes clips/, cut/ and full.mp4 into the take's own run directory (they must not exist yet). Any later
cut of the same take is a recut: it always writes to a new run directory and never touches the source (Q-58).
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import time

from . import analyze as an_mod
from . import cover as cover_mod
from .join import join
from .plan import Planner
from .render import CROSSFADE_DEFAULT_S, fade_frames, render
from .timeline import Timeline, fr
from .tools import FPS, probe

RECORD_VERSION = 1


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:40] or "step"


def _t(frames):
    return round(frames / FPS, 4)


def resolve_out(run_dir, out):
    run_dir = os.path.abspath(run_dir)
    already_cut = any(os.path.exists(os.path.join(run_dir, p)) for p in ("clips", "cut", "full.mp4"))
    if out is None and not already_cut:
        return run_dir, False
    if out is None:
        rid = os.path.basename(run_dir.rstrip("/"))
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = os.path.join(os.path.dirname(run_dir), f"{stamp}-recut-{rid}")
    out = os.path.abspath(out)
    if out == run_dir or out.startswith(run_dir + os.sep):
        raise SystemExit("a recut must write to a new run directory outside the source (Q-58)")
    if os.path.exists(out):
        raise SystemExit(f"output directory exists: {out} (a recut always writes to a new run directory, Q-58)")
    os.makedirs(out)
    return out, True


def cut(run_dir, out=None, factor=4.0, keep_work=False, preset="veryfast", crossfade_s=CROSSFADE_DEFAULT_S):
    """Cut into a staging directory, then publish: a failed cut leaves no partial clips behind."""
    run_dir = os.path.abspath(run_dir)
    final_dir, is_recut = resolve_out(run_dir, out)
    stage = os.path.join(final_dir, ".cut-staging-%d" % os.getpid())
    os.makedirs(stage)
    try:
        rec = _cut(run_dir, stage, final_dir, is_recut, factor, keep_work, preset, crossfade_s)
        for name in ("clips", "cut", "full.mp4", "source.json"):
            if os.path.exists(os.path.join(stage, name)):
                os.replace(os.path.join(stage, name), os.path.join(final_dir, name))
    except BaseException:
        if is_recut:
            shutil.rmtree(final_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return final_dir, rec


def _cut(run_dir, out_dir, final_dir, is_recut, factor, keep_work, preset, crossfade_s=CROSSFADE_DEFAULT_S):
    t_start = time.time()
    fade_n = fade_frames(crossfade_s)
    with open(os.path.join(run_dir, "manifest.json")) as f:
        manifest = json.load(f)
    raw = os.path.join(run_dir, manifest.get("raw", "raw.mp4"))
    info = probe(raw)
    W, H = info["width"], info["height"]
    work = os.path.join(out_dir, "cut", "work")
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "clips"), exist_ok=True)
    timing = {}

    t = time.time()
    an = an_mod.analyze(raw, work)
    timing["analyze_s"] = round(time.time() - t, 2)

    t = time.time()
    tl = Timeline(run_dir, an.n)
    segs = tl.segments()
    if not segs:
        raise SystemExit("no step marks in the manifest: nothing to cut (Q-10)")
    for s in segs:
        # an empty segment (two marks in the same frame, or a mark at the very end): deliver the step as one frame,
        # flagged collapsed / all standstill, so the gate fails it on Q-14 instead of the render crashing
        if s["end"] <= s["start"]:
            s["collapsed"] = True
            s["start"] = max(0, min(s["start"], an.n - 1))
            s["end"] = s["start"] + 1
    pl = Planner(an, tl, factor)
    plans = [pl.plan_clip(s, W) for s in segs]
    timing["plan_s"] = round(time.time() - t, 2)

    clips = []
    for p in plans:
        s = p["seg"]
        name = s["name"] or f"step-{s['step']}"
        p["file"] = f"clips/{s['index'] + 1:02d}-{_slug(name)}.mp4"
        p["path"] = os.path.join(out_dir, p["file"])

    # Q-23 skeleton cover (S2): decided on the delivered frame sequence, drawn in the render
    t = time.time()
    cover_mod.plan_covers(raw, an, plans, [_cover_clicks(p, tl) for p in plans], W, H, work)
    timing["cover_s"] = round(time.time() - t, 2)
    for p in plans:
        clips.append({"fmap": p["fmap"], "badge": p["badge"], "path": p["path"], "covers": p["cover_render"]})

    t = time.time()
    render(raw, clips, W, H, work, factor, preset, fade_n)
    timing["render_s"] = round(time.time() - t, 2)

    record_clips = [_clip_record(p, pl, tl, an) for p in plans]

    t = time.time()
    jr = join([{"path": p["path"], "frames": len(p["fmap"]), "site_start": p["seg"]["start_site"],
                "site_end": p["seg"]["end_site"], "index": p["seg"]["index"]} for p in plans],
              os.path.join(out_dir, "full.mp4"), os.path.join(work, "join"), preset, fade_n)
    timing["join_s"] = round(time.time() - t, 2)
    timing["total_s"] = round(time.time() - t_start, 2)
    timing["raw_duration_s"] = round(an.n / FPS, 2)

    source_id = manifest.get("run_id") or os.path.basename(run_dir)
    run_id = os.path.basename(final_dir) if is_recut else source_id
    record = {
        "version": RECORD_VERSION, "run_id": run_id, "source_run": source_id, "source_dir": run_dir,
        "recut": is_recut, "raw": raw, "width": W, "height": H, "fps": FPS, "raw_frames": an.n,
        "params": {"speed_factor": factor, "fade_s": round(fade_n / FPS, 4), "fade_frames": fade_n,
                   "encoder": "libx264 high yuv420p crf18 " + preset,
                   "skeleton_cover": {"max_s": cover_mod.COVER_MAX_S, "seam_max": cover_mod.SEAM_MAX,
                                      "ripple": "drawn", "ripple_s": cover_mod.RIPPLE_S,
                                      "ripple_d": cover_mod.RIPPLE_D}},
        "clips": record_clips, "joins": jr["joins"], "full": "full.mp4", "full_duration": jr["full_duration"],
        "full_frames": jr["full_frames"], "full_expected_frames": jr["expected_frames"], "timing": timing,
    }
    with open(os.path.join(out_dir, "cut", "record.json"), "w") as f:
        json.dump(record, f, indent=1)
    index = {
        "run_id": run_id, "source_run": source_id, "record": "cut/record.json",
        "clips": [{"index": c["index"], "step": c["step"], "name": c["name"], "file": c["file"],
                   "duration": c["duration"], "frames": c["frames"], "site": c["site_start"],
                   "holds": [{"kind": h["kind"], "seconds": h["seconds"], "clip_t": h["clip_t"]}
                             for h in c["holds"]],
                   "source": {"start_t": c["source"]["start_t"], "end_t": c["source"]["end_t"]}}
                  for c in record_clips],
        "full": "full.mp4", "full_duration": jr["full_duration"],
    }
    with open(os.path.join(out_dir, "clips", "index.json"), "w") as f:
        json.dump(index, f, indent=1)
    if is_recut:
        with open(os.path.join(out_dir, "source.json"), "w") as f:
            json.dump({"source_run": source_id, "source_dir": run_dir, "kind": "recut",
                       "created": datetime.datetime.now().isoformat(timespec="seconds")}, f, indent=1)
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return record


def _cover_clicks(p, tl):
    """The clip's clicks and stops in delivered frames, as the gate sees them in the clip-time event log (clicks
    whose frame was dropped are not logged; stops: every click, every click's glide start, glide/move spans)."""
    s, fmap = p["seg"], p["fmap"]
    a, b = s["start"], s["end"]
    clicks, stops = [], []
    for c in tl.clicks:
        if not a <= c["frame"] < b:
            continue
        k, moved = _map_frame(fmap, c["frame"])
        if k is None or (moved and fmap[k] != c["frame"]):
            continue
        clicks.append({"k": k, "x": c.get("x"), "y": c.get("y"), "t": round(c["t"], 4), "label": c.get("label")})
        stops.append(k)
        if c.get("glide") is not None:
            stops.append(_map_frame(fmap, fr(c["glide"]))[0] or 0)
    for spn in tl.spans:
        f0 = fr(spn["start"])
        if spn["type"] in ("glide", "move") and a <= f0 < b:
            k, _ = _map_frame(fmap, f0)
            if k is not None:
                stops.append(k)
    return {"clicks": clicks, "stops": stops}


def _map_frame(fmap, f):
    """Delivered frame index showing raw frame f (or the first kept frame after it). None if after the clip."""
    for k, g in enumerate(fmap):
        if g >= f:
            return k, g != f
    return None, True


def _clip_record(p, pl, tl, an):
    s, fmap = p["seg"], p["fmap"]
    K = len(fmap)
    a, b = s["start"], s["end"]
    # splices: every gap in the kept raw frames (also inside a sped-up stretch), at the delivered frame that shows
    # the first raw frame after the gap
    kept = p["kept"]
    splices = []
    for i in range(1, len(kept)):
        if kept[i] - kept[i - 1] > 1:
            k, _ = _map_frame(fmap, kept[i])
            if k is None:
                continue
            gap = (kept[i - 1] + 1, kept[i])
            ds = [x for x in p["drops"] if x["src_start"] < gap[1] and x["src_end"] > gap[0]]
            jm = [x["join_mad"] for x in ds if "join_mad" in x]
            splices.append({"clip_t": _t(k), "frame": k, "src_from": kept[i - 1], "src_to": kept[i],
                            "removed_frames": gap[1] - gap[0], "reason": ", ".join(sorted({x["reason"] for x in ds}))
                            or "cut", "in_speedup": p["speed"][k] > 1.0001, "join_mad": jm[0] if jm else None})
    # speed-ups in delivered time
    sp = []
    k = 0
    while k < K:
        if p["speed"][k] > 1.0001:
            j = k
            while j + 1 < K and p["speed"][j + 1] > 1.0001:
                j += 1
            w = next((u for u in p["speedups"] if u.get("factor") and u["src_start"] <= fmap[min(K - 1, (k + j) // 2)]
                      < u["src_end"]), None)
            sp.append({"clip_start": _t(k), "clip_end": _t(j + 1), "frames": [k, j + 1],
                       "src_start": _t(fmap[k]), "src_end": _t(fmap[j] + 1), "factor": pl.factor,
                       "ease_frames": 3, "badge": True,
                       "wait": ({"kind": w["wait"]["kind"], "start_t": round(w["wait"]["start"], 3),
                                 "end_t": round(w["wait"]["end"], 3), "step": w["wait"].get("step"),
                                 "source": w["wait"].get("source")} if w else None)})
            k = j + 1
        else:
            k += 1
    holds = []
    for h in tl.holds:
        if a <= h["start"] < b:
            ks, _ = _map_frame(fmap, h["start"])
            ke, _ = _map_frame(fmap, h["end"])
            ke = K if ke is None else ke
            holds.append({"kind": h["kind"], "seconds": h["seconds"], "clip_t": _t(ks) if ks is not None else None,
                          "clip_end": _t(ke), "kept_seconds": _t(ke - ks) if ks is not None else 0.0,
                          "src_t": round(h["src_t"], 4)})
    events = []
    for c in tl.clicks:
        if a <= c["frame"] < b:
            k, moved = _map_frame(fmap, c["frame"])
            events.append({"type": "click", "clip_t": _t(k) if k is not None else None, "src_t": round(c["t"], 4),
                           "x": c.get("x"), "y": c.get("y"), "label": c.get("label"), "navigates": c["navigates"],
                           "glide_clip_t": (_t(_map_frame(fmap, fr(c["glide"]))[0] or 0)
                                            if c.get("glide") is not None else None),
                           "dropped": moved and k is not None and fmap[k] != c["frame"]})
    for ty in tl.typing:
        f0 = fr(ty["start"])
        if a <= f0 < b:
            k0, _ = _map_frame(fmap, f0)
            k1, _ = _map_frame(fmap, fr(ty["end"]))
            events.append({"type": ty["type"], "clip_t": _t(k0) if k0 is not None else None,
                           "clip_end": _t(k1 if k1 is not None else K)})
    for spn in tl.spans:
        f0 = fr(spn["start"])
        if a <= f0 < b:
            k0, _ = _map_frame(fmap, f0)
            k1, _ = _map_frame(fmap, fr(spn["end"]))
            events.append({"type": spn["type"], "clip_t": _t(k0) if k0 is not None else None,
                           "clip_end": _t(k1 if k1 is not None else K)})
    seg_mad = [an.mad[f] for f in range(a + 1, b)]
    all_still = (bool(seg_mad) and max(seg_mad) <= 0.5) or bool(s.get("collapsed"))
    kept_mad = [an.mad[fmap[k]] for k in range(1, K) if fmap[k] == fmap[k - 1] + 1]
    return {
        "index": s["index"], "step": s["step"], "name": s["name"], "file": p["file"],
        "site_start": s["start_site"], "site_end": s["end_site"],
        "duration": _t(K), "frames": K,
        "source": {"start_t": _t(a), "end_t": _t(b), "start_frame": a, "end_frame": b},
        "kept": _pieces(fmap, p["speed"]),
        "drops": [{"src_start": _t(d["src_start"]), "src_end": _t(d["src_end"]),
                   "frames": d["src_end"] - d["src_start"], "reason": d["reason"],
                   **({"join_mad": d["join_mad"]} if "join_mad" in d else {})} for d in p["drops"]],
        "declined": [{"src_start": _t(d["src_start"]), "src_end": _t(d["src_end"]), "reason": d["reason"]}
                     for d in p["declined"]]
                    + [{"src_start": _t(u["src_start"]), "src_end": _t(u["src_end"]),
                        "reason": "speed-up declined: " + u["declined"]} for u in p["speedups"] if u.get("declined")],
        "splices": splices,
        "speedups": sp,
        "badge": [{"clip_start": x["clip_start"], "clip_end": x["clip_end"], "opacity": 0.8} for x in sp],
        "freezes": [],
        "bridges": [],
        "covers": p.get("covers", []),
        "holds": holds,
        "events": events,
        "all_standstill": all_still,
        "collapsed": bool(s.get("collapsed")),
        "raw_all_still_mad_max": round(max(seg_mad), 3) if seg_mad else 0.0,
        "kept_motion_frames": sum(1 for m in kept_mad if m >= 1.5),
        "frame_map": fmap,
    }


def _pieces(fmap, speed):
    """Kept raw stretches in delivered order: {clip_start, clip_end, src_start, src_end, factor}. A new piece starts
    at every splice and wherever the speed class (1x / sped up) changes."""
    out = []
    k, K = 0, len(fmap)
    while k < K:
        sped = speed[k] > 1.0001
        j = k
        while j + 1 < K and (speed[j + 1] > 1.0001) == sped and (sped or fmap[j + 1] == fmap[j] + 1):
            j += 1
        out.append({"clip_start": _t(k), "clip_end": _t(j + 1), "src_start": _t(fmap[k]), "src_end": _t(fmap[j] + 1),
                    "factor": round(sum(speed[k:j + 1]) / (j + 1 - k), 3)})
        k = j + 1
    return out


def dir_digest(path):
    """sha256 over every file (relative path + content) below `path`; used to prove a recut left the source intact."""
    h = hashlib.sha256()
    for root, dirs, files in sorted(os.walk(path)):
        dirs.sort()
        for fn in sorted(files):
            p = os.path.join(root, fn)
            h.update(os.path.relpath(p, path).encode())
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
    return h.hexdigest()
