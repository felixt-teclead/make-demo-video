"""Read a run's timeline (manifest + event log) into frame-based spans the planner uses.

Input contract: M1 docs/interfaces.md section 5 (manifest.json, events.jsonl) plus the readiness waits of the step
runner (events posted to the recorder, or `readiness_wait` records in take.log.jsonl). The accepted event shapes are
listed in docs/cut.md.
"""
import json
import math
import os
from urllib.parse import urlparse

from .tools import FPS

CLICK_TYPES = {"click"}
TYPING_TYPES = {"type", "typing", "paste", "key", "keys"}
WAIT_TYPES = {"readiness_wait", "wait", "late_content"}
NAV_TYPES = {"navigation", "navigate", "load", "document"}
SPAN_TYPES = {"pan", "scroll", "camera", "glide", "reveal", "move"}


def host_of(url):
    if not url:
        return None
    try:
        h = urlparse(url if "//" in url else "//" + url).hostname
    except ValueError:
        return None
    return h.lower() if h else None


def _vt(ev, t0, key="t"):
    """Video time (s) of the event's `key` timestamp."""
    if key == "t" and ev.get("video_t") is not None:
        return float(ev["video_t"])
    v = ev.get(key)
    if v is None:
        return None
    v = float(v)
    # epoch stamps are large; small values are already on the video clock
    return v - t0 if v > 1e8 else v


def _end_vt(ev, t0):
    for k in ("video_end_t", "end_video_t"):
        if ev.get(k) is not None:
            return float(ev[k])
    for k in ("end", "end_t", "t_end", "ready", "ready_t"):
        if ev.get(k) is not None:
            return _vt(ev, t0, k)
    start = _vt(ev, t0)
    for k in ("duration", "seconds"):
        if ev.get(k) is not None and start is not None:
            return start + float(ev[k])
    return None


def fr(t):
    """Raw frame of a video time: the first frame that shows it, ceil(t * fps). This is the recorder's own stamp
    (env/recorder/recorder.py `frame`, and every segment's start_frame), so an event logged after a mark is never
    assigned to a frame before that mark's segment (review Major 3: round() put a hold posted a few ms after a mark
    in the first half of a frame into no clip)."""
    return max(0, int(math.ceil(t * FPS - 1e-6)))


class Timeline:
    def __init__(self, run_dir, n_frames):
        self.run_dir = run_dir
        with open(os.path.join(run_dir, "manifest.json")) as f:
            self.manifest = m = json.load(f)
        self.t0 = float(m.get("t0") or 0)
        self.n = n_frames
        events = list(m.get("events") or [])
        if not events and os.path.exists(os.path.join(run_dir, "events.jsonl")):
            with open(os.path.join(run_dir, "events.jsonl")) as f:
                events = [json.loads(line) for line in f if line.strip()]
        self.events = events
        self.marks = sorted(m.get("marks") or [e for e in events if e.get("type") == "mark"],
                            key=lambda e: _vt(e, self.t0))
        self.holds = []
        for h in m.get("holds") or [e for e in events if e.get("type") == "hold"]:
            s = _vt(h, self.t0)
            self.holds.append({"kind": h.get("kind"), "seconds": float(h.get("seconds", 0)), "step": h.get("step"),
                               "start": fr(s), "end": fr(s + float(h.get("seconds", 0))), "src_t": s})
        self.clicks, self.typing, self.spans, self.waits, self.navs, self.urls = [], [], [], [], [], []
        for e in events:
            typ = e.get("type")
            s = _vt(e, self.t0)
            if s is None:
                continue
            url = e.get("url") or e.get("href")
            if url:
                self.urls.append((fr(s), host_of(url)))
            if typ in CLICK_TYPES:
                g = _vt(e, self.t0, "glide_t") if e.get("glide_t") is not None else None
                self.clicks.append({"frame": fr(s), "t": s, "glide": g, "x": e.get("x"), "y": e.get("y"),
                                    "navigates": bool(e.get("navigates")), "step": e.get("step"),
                                    "label": e.get("label")})
            elif typ in TYPING_TYPES:
                end = _end_vt(e, self.t0) or s
                self.typing.append({"start": s, "end": end, "step": e.get("step"), "type": typ})
            elif typ in WAIT_TYPES:
                end = _end_vt(e, self.t0)
                if end is not None and end > s:
                    self.waits.append({"start": s, "end": end, "kind": e.get("kind") or typ, "step": e.get("step"),
                                       "source": "events"})
            elif typ in NAV_TYPES:
                end = _end_vt(e, self.t0)
                self.navs.append({"start": s, "ready": end, "url": url, "step": e.get("step")})
            elif typ in SPAN_TYPES:
                end = _end_vt(e, self.t0)
                if end is not None:
                    self.spans.append({"start": s, "end": end, "type": typ, "step": e.get("step")})
        # wait_start / wait_end pairs
        open_w = {}
        for e in events:
            if e.get("type") == "wait_start":
                open_w[e.get("id", e.get("step"))] = e
            elif e.get("type") == "wait_end":
                st = open_w.pop(e.get("id", e.get("step")), None)
                if st:
                    self.waits.append({"start": _vt(st, self.t0), "end": _vt(e, self.t0),
                                       "kind": st.get("kind", "wait"), "step": st.get("step"), "source": "events"})
        # readiness waits from the step runner's log (M2: `readiness_wait` with start/end epochs)
        tl = os.path.join(run_dir, "take.log.jsonl")
        if os.path.exists(tl):
            with open(tl) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("type") == "readiness_wait" and r.get("start") and r.get("end"):
                        s, e_ = float(r["start"]) - self.t0, float(r["end"]) - self.t0
                        if e_ > s and not any(abs(w["start"] - s) < 0.05 for w in self.waits):
                            self.waits.append({"start": s, "end": e_, "kind": "readiness_wait",
                                               "step": r.get("step"), "source": "take.log"})
        self.clicks.sort(key=lambda c: c["frame"])
        self.urls.sort()

    @staticmethod
    def frame(t):
        return fr(t)

    def segments(self):
        """Per-step raw spans [start_frame, end_frame) from each mark to the next (last one to the end)."""
        segs = self.manifest.get("segments")
        out = []
        if segs:
            for s in segs:
                a = int(s.get("start_frame", fr(float(s["start_t"]))))
                b = s.get("end_frame")
                b = int(b) if b is not None else fr(float(s["end_t"]))
                out.append({"index": s.get("index", len(out)), "step": s.get("step"), "name": s.get("name"),
                            "start": max(0, a), "end": min(self.n, b), "site": s.get("site") or host_of(s.get("url"))})
        else:
            for i, mk in enumerate(self.marks):
                a = fr(_vt(mk, self.t0))
                b = fr(_vt(self.marks[i + 1], self.t0)) if i + 1 < len(self.marks) else self.n
                out.append({"index": i, "step": mk.get("step", i + 1), "name": mk.get("name") or f"step-{i + 1}",
                            "start": a, "end": min(self.n, b), "site": None})
        # sites: the mark's own url/site, else the last url known at the segment start
        for s in out:
            mk = next((m for m in self.marks if m.get("step") == s["step"] or m.get("name") == s["name"]), None)
            if not s["site"] and mk:
                s["site"] = mk.get("site") or host_of(mk.get("url"))
            known = [h for f, h in self.urls if f <= s["start"] + 1 and h]
            s["start_site"] = s["site"] or (known[-1] if known else None)
            inside = [h for f, h in self.urls if f < s["end"] and h]
            s["end_site"] = inside[-1] if inside else s["start_site"]
        return out
