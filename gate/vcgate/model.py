"""Shared data model of the gate: per-clip context, violations, notes."""
import numpy as np

from . import video

STILL_MAD = 0.5   # section Q conventions: MAD <= 0.5 is still
MOVING_MAD = 1.5  # MAD >= 1.5 is moving


class Violation:
    """One defect = one violation (Q-06). severity is FAIL or WARN."""

    def __init__(self, check, clip, t0, t1, reason, severity="FAIL", **extra):
        self.check = check
        self.clip = clip
        self.t0 = None if t0 is None else round(float(t0), 3)
        self.t1 = None if t1 is None else round(float(t1), 3)
        self.reason = reason
        self.severity = severity
        self.extra = extra

    def to_dict(self):
        d = {"check": self.check, "severity": self.severity, "clip": self.clip,
             "t0": self.t0, "t1": self.t1, "reason": self.reason}
        if self.extra:
            d["detail"] = _jsonable(self.extra)
        return d


def _jsonable(o):
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return round(float(o), 4)
    if isinstance(o, float):
        return round(o, 4)
    return o


class ClipEvents:
    """Logged context of one clip, in delivered clip seconds. None fields mean 'not logged'."""

    def __init__(self, d=None):
        d = d or {}
        self.clicks = sorted(d.get("clicks", []), key=lambda c: c["t"])      # {t, x, y, label, glide_t?}
        self.marks = sorted(float(m["t"] if isinstance(m, dict) else m) for m in d.get("marks", [0.0]))
        self.holds = d.get("holds", [])                                        # {kind, seconds, t, spec_seconds?}
        self.spec_holds = d.get("spec_holds")                                  # the spec's holds {kind, seconds} or None
        self.actions = d.get("actions", [])                                    # {type, t, end?}: typing, camera, waits
        self.result = d.get("result")                                          # {verified, error}
        self.waits = d.get("waits", [])                                        # {start, end}
        self.dropped = d.get("dropped", [])                                    # logged events the cut removed


class ClipCtx:
    """Everything a check needs about one clip. Built inside the clip worker process."""

    def __init__(self, name, index, path, info, events, record, mode="cut"):
        self.name = name
        self.index = index
        self.path = path
        self.width = info["width"]
        self.height = info["height"]
        self.fps = info["fps"] or 30.0
        self.duration = info["duration"]
        self.ev = events            # ClipEvents or None (no event log given)
        self.record = record or {}  # this clip's cut-record entry ({} if none)
        self.mode = mode
        self.scale = self.width / 1920.0   # pixel thresholds scale by width/1920
        self.low = None
        self.n = 0
        self.mad = None
        self.violations = []
        self.notes = []             # plain-words measurements for the report
        self.episodes = []          # motion episodes (filled by the motion check)
        self.ripples = []           # detected ripples (filled by the ripple check)
        self.tips_head = []         # cursor tip in the first frames (Q-43)
        self.tips_tail = []         # cursor tip in the last frames (Q-43)
        self.timing = {}

    # --- decoding -------------------------------------------------------
    def load_low(self):
        self.low = video.decode_low(self.path, self.name)
        self.n = len(self.low)
        a = self.low.astype(np.int16)
        self.mad = np.zeros(self.n)
        if self.n > 1:
            self.mad[1:] = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
        # clip duration from the decoded frame count (the probe's duration can be off by a frame)
        self.duration = self.n / self.fps

    def full(self, start, count, pix_fmt="rgb24"):
        start = max(0, int(start))
        count = max(0, min(int(count), self.n - start))
        if count == 0:
            return np.zeros((0, self.height, self.width, 3) if pix_fmt == "rgb24" else (0, self.height, self.width),
                            np.uint8)
        return video.decode_full(self.path, self.name, self.width, self.height, start, count, self.fps, pix_fmt)

    def iter_full(self, pix_fmt="rgb24", chunk=32):
        return video.iter_full(self.path, self.name, self.width, self.height, pix_fmt, chunk)

    # --- helpers --------------------------------------------------------
    def t(self, frame):
        return frame / self.fps

    def f(self, t):
        return int(round(t * self.fps))

    def px(self, v):
        """A threshold given in px of the 1920-wide delivered video, scaled to this clip."""
        return v * self.scale

    @property
    def splices(self):
        out = []
        for s in self.record.get("splices", []):
            if isinstance(s, dict):
                # M4 cut record: {"clip_t", "frame"} where frame is the first delivered frame after the splice
                t = s.get("t", s.get("clip_t"))
                if t is None and s.get("frame") is not None:
                    t = s["frame"] / self.fps
                out.append(float(t))
            else:
                out.append(float(s))
        return sorted(out)

    @property
    def speedups(self):
        """[{start, end, factor}] in clip seconds (accepts the M4 keys clip_start/clip_end)."""
        out = []
        for s in self.record.get("speedups", []):
            a = s.get("start", s.get("clip_start"))
            b = s.get("end", s.get("clip_end"))
            if a is not None and b is not None:
                out.append({"start": float(a), "end": float(b), "factor": float(s.get("factor") or 1.0)})
        return out

    def speed_at(self, t):
        for s in self.speedups:
            if s["start"] <= t <= s["end"]:
                return s["factor"]
        return 1.0

    def add(self, check, t0, t1, reason, severity="FAIL", **extra):
        self.violations.append(Violation(check, self.name, t0, t1, reason, severity, **extra))

    def note(self, check, text):
        self.notes.append({"check": check, "text": text})
