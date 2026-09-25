"""Build the gate's clip-time event log (M6a docs/gate.md "Event log in clip time") from the cut record (M4), the
spec and the runner's per-step results. The loop owns this file; the gate only reads it."""
import json
import os


def spec_hold_list(spec_resolved, step, first):
    """The holds the runner logs for one step, in its order and with its defaults (take_runner.py): the landing hold
    (first step), per action its `hold` (kind `hold_kind`, default step) or a `hold` op (kind default reading), then
    the step's own hold. The gate aligns the logged holds with this list (Q-51: a missing hold FAILs)."""
    if not step:
        return []
    out = []
    if first:
        out.append({"kind": "landing", "seconds": float((spec_resolved.get("start") or {}).get("hold", 1.0))})
    for a in step.get("actions", []):
        if a.get("hold") is not None:
            out.append({"kind": a.get("hold_kind", "step"), "seconds": float(a["hold"])})
        elif a.get("op") == "hold":
            out.append({"kind": a.get("kind", "reading"), "seconds": float(a["seconds"])})
    out.append({"kind": step.get("hold_kind", "step"), "seconds": float(step.get("hold", 1.0))})
    return out


def build(run_dir, spec_resolved, result, *, dry_run=False):
    rec_path = os.path.join(run_dir, "cut", "record.json")
    with open(rec_path) as f:
        record = json.load(f)
    steps = [s["name"] for s in spec_resolved["steps"]]
    spec_holds = {s["name"]: s for s in spec_resolved["steps"]}
    by_step = {r["name"]: r for r in (result or {}).get("steps", [])}
    clips, dropped = {}, []
    for c in record.get("clips", []):
        name = c.get("name")
        clicks, actions = [], []
        for e in c.get("events", []):
            t = e.get("clip_t")
            if e.get("dropped") or t is None:
                dropped.append({"type": e.get("type"), "video_t": e.get("src_t"), "step": name})
                continue
            if e.get("type") == "click":
                d = {"t": t, "x": e.get("x"), "y": e.get("y"), "label": e.get("label")}
                if e.get("glide_clip_t") is not None:
                    d["glide_t"] = e["glide_clip_t"]
                clicks.append(d)
            else:
                actions.append({"type": e.get("type"), "t": t, "end": e.get("clip_end")})
        s = spec_holds.get(name) or {}
        holds = [{"kind": h.get("kind"), "t": h.get("clip_t"), "seconds": h.get("seconds"),
                  "kept_seconds": h.get("kept_seconds")} for h in c.get("holds", [])]
        r = by_step.get(name)
        clips[name] = {"clicks": clicks, "actions": actions, "holds": holds, "marks": [0.0],
                       "spec_holds": spec_hold_list(spec_resolved, s, first=bool(steps) and name == steps[0]),
                       "result": {"verified": bool(r and r.get("verified")),
                                  "error": None if (r and r.get("ok")) else ((r or {}).get("reason") or "no result")}}
    ln = spec_resolved.get("length") or [30, 60]
    ev = {"steps": steps, "length": {"min": ln[0], "max": ln[1]}, "dry_run": bool(dry_run),
          "final_hold": spec_resolved["steps"][-1].get("hold_kind") == "final", "dropped": dropped, "clips": clips}
    out = os.path.join(run_dir, "clips", "events.json")
    with open(out, "w") as f:
        json.dump(ev, f, indent=1, ensure_ascii=False)
    return out
