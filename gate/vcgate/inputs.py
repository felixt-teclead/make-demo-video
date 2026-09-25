"""Gate inputs: the clip index, the cut record and the optional event log (all in delivered clip time).

See docs/gate.md for the formats. The gate reads ONLY the clips that the index lists (Q-10).
"""
import glob
import json
import os

from .model import ClipEvents


class InputError(Exception):
    pass


def _load_json(path):
    with open(path) as fh:
        return json.load(fh)


class Take:
    def __init__(self):
        self.root = None
        self.mode = "cut"
        self.clips = []          # [{name, index, step, file(abs), holds, ...}] in play order
        self.ignored = []        # media files present but not listed (Q-10)
        self.record = {}         # clip name -> cut-record entry
        self.events = None       # parsed event log (dict) or None when not given
        self.index = {}
        self.events_missing = None   # set when the event log is absent without --no-events (gate aborts)

    def clip_events(self, name):
        if self.events is None:
            return None
        return ClipEvents((self.events.get("clips") or {}).get(name, {}))


def load_take(root, events_path=None, record_path=None, no_events=False, mode="cut"):
    """Load a take directory (cut mode) or a run directory's raw capture (raw mode)."""
    take = Take()
    take.root = os.path.abspath(root)
    take.mode = mode
    if not os.path.isdir(take.root):
        raise InputError(f"take directory not found: {root}")

    if mode == "raw":
        _load_raw(take)
    else:
        idx_path = os.path.join(take.root, "clips", "index.json")
        if os.path.isfile(idx_path):
            take.index = _load_json(idx_path)
        else:
            take.index = {"clips": []}   # no index: 0 delivered clips (Q-10 fails, never "OK")
        for i, c in enumerate(take.index.get("clips", [])):
            f = c["file"]
            f = f if os.path.isabs(f) else os.path.join(take.root, f)
            take.clips.append({"name": c.get("name") or os.path.splitext(os.path.basename(f))[0],
                               "index": c.get("index", i), "step": c.get("step"), "file": f,
                               "holds": c.get("holds", []), "site": c.get("site")})
        listed = {os.path.abspath(c["file"]) for c in take.clips}
        for f in sorted(glob.glob(os.path.join(take.root, "**", "*.mp4"), recursive=True)):
            if os.path.abspath(f) not in listed:
                take.ignored.append(os.path.relpath(f, take.root))

        rec_path = record_path or os.path.join(take.root, "cut", "record.json")
        if os.path.isfile(rec_path):
            rec = _load_json(rec_path)
            for c in rec.get("clips", []):
                take.record[c.get("name")] = c

    ev_path = events_path
    if ev_path is None and not no_events:
        cand = os.path.join(take.root, "clips", "events.json")
        if os.path.isfile(cand):
            ev_path = cand
    if ev_path and not no_events:
        take.events = _load_json(ev_path)
    elif mode == "raw" and not no_events and os.path.isfile(os.path.join(take.root, "events.jsonl")):
        take.events = raw_events(take.root)
    elif mode == "cut" and not no_events:
        # no event log: Q-10, Q-11, Q-13, Q-44, Q-51, Q-62 cannot be measured, and the cut record is no substitute
        # (it has no step list, length range or results). The gate aborts (Q-02); --no-events is the explicit
        # pixel-only diagnostic.
        take.events_missing = os.path.join("clips", "events.json")
    return take


def events_from_record(record, order):
    """Clip-time event log from the cutter's record (M4: clips[].events, clips[].holds). The record has no spec
    data (step list, length range, results); those checks then say 'not measured'."""
    clips = {}
    for name in order:
        c = record.get(name) or {}
        clicks, actions, dropped = [], [], []
        for e in c.get("events", []):
            t = e.get("clip_t", e.get("t"))
            if e.get("type") == "click":
                if t is None or e.get("dropped"):
                    dropped.append(e)
                if t is not None:
                    d = {"t": t, "x": e.get("x"), "y": e.get("y"), "label": e.get("label")}
                    if e.get("glide_clip_t") is not None:
                        d["glide_t"] = e["glide_clip_t"]
                    clicks.append(d)
            elif t is not None:
                actions.append({"type": e.get("type"), "t": t, "end": e.get("clip_end")})
            else:
                dropped.append(e)
        holds = [{"kind": h.get("kind"), "t": h.get("clip_t"), "seconds": h.get("seconds"),
                  "kept_seconds": h.get("kept_seconds")} for h in c.get("holds", []) if h.get("clip_t") is not None]
        holds += [{"kind": h.get("kind"), "t": None, "seconds": h.get("seconds"), "kept_seconds": 0.0}
                  for h in c.get("holds", []) if h.get("clip_t") is None]
        clips[name] = {"clicks": clicks, "actions": actions, "holds": holds, "marks": [0.0],
                       "dropped": dropped}
    return {"clips": clips, "source": "cut record"}


def _load_raw(take):
    """Raw mode (diagnostic, Q-03): the raw capture, one clip per recorded step segment."""
    man_path = os.path.join(take.root, "manifest.json")
    raw = os.path.join(take.root, "raw.mp4")
    if not os.path.isfile(raw):
        raise InputError("raw mode needs raw.mp4 in the run directory")
    man = _load_json(man_path) if os.path.isfile(man_path) else {}
    take.index = {"clips": [], "raw": True}
    take.clips.append({"name": "raw", "index": 0, "step": None, "file": raw, "holds": man.get("holds", [])})


def raw_events(run_dir):
    """Event log on the raw clock (events.jsonl from the recorder, M1 interfaces section 5) as a one-clip log."""
    clicks, holds, marks, actions = [], [], [], []
    with open(os.path.join(run_dir, "events.jsonl")) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            t = e.get("video_t")
            if t is None:
                continue
            typ = e.get("type")
            if typ == "click":
                c = {"t": t, "x": e.get("x"), "y": e.get("y"), "label": e.get("label")}
                if e.get("glide_t") is not None and e.get("t") is not None:     # epoch -> raw clock
                    c["glide_t"] = round(t - (float(e["t"]) - float(e["glide_t"])), 4)
                clicks.append(c)
            elif typ == "hold":
                holds.append({"kind": e.get("kind"), "seconds": e.get("seconds"), "t": t})
            elif typ == "mark":
                marks.append(t)
            elif typ not in ("start", "stop"):
                end = e.get("video_end_t")
                if end is None and e.get("end") is not None and e.get("t") is not None:
                    end = round(t + float(e["end"]) - float(e["t"]), 4)
                actions.append({"type": typ, "t": t, "end": end})
    return {"clips": {"raw": {"clicks": clicks, "holds": holds, "marks": marks or [0.0], "actions": actions}}}


def map_raw_events(manifest, record, raw_events_list):
    """Map raw-clock events to delivered clip time through the cut record's per-clip time map.

    Each cut-record clip carries "map": [{"src_start", "src_end", "clip_start", "speed"}] (raw seconds -> clip
    seconds). An event maps to the clip whose map contains its raw time. Events in removed stretches are dropped and
    returned separately so the report can name them (a click that the cutter removed is a Q-14 failure).
    """
    out, dropped = {}, []
    for e in raw_events_list:
        t = e.get("video_t")
        hit = False
        for c in record.get("clips", []):
            for seg in c.get("map", []):
                if seg["src_start"] <= t < seg["src_end"]:
                    ct = seg["clip_start"] + (t - seg["src_start"]) / float(seg.get("speed", 1.0))
                    out.setdefault(c["name"], []).append(dict(e, t=round(ct, 4)))
                    hit = True
                    break
            if hit:
                break
        if not hit:
            dropped.append(e)
    return out, dropped
