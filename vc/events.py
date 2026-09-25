"""The one event stream of a take (docs/events.md): the recorder's `events.jsonl` (and `manifest.json` "events").

Written by the recorder (start, mark, hold) and by what the take runner posts to it (the jev wrapper's readiness
waits, the overlay's clicks, typing, pastes and orphan ripples, the camera's pans and reveals). Read by the cutter
(vc/cut/timeline.py) and the gate (gate/vcgate/inputs.py raw mode, and through the cut record in cut mode).

All times are epoch seconds on the one host clock; the recorder adds `video_t` (and `video_end_t` for `end`).
Standard library only.
"""

# type -> (required fields, optional fields)
SCHEMA = {
    "start": (("t",), ("label", "run_id")),
    "mark": (("t", "name", "step"), ("url",)),
    "hold": (("t", "kind", "seconds", "step"), ()),
    "click": (("t", "glide_t", "click_t", "x", "y", "step"), ("label", "kind", "navigates")),
    "typing": (("t", "end", "step"), ("chars", "submit", "value_set_whole", "label", "x", "y")),
    "paste": (("t", "end", "step"), ("chars", "label")),
    "readiness_wait": (("t", "end", "step"), ("ok", "jev_step")),
    "pan": (("t", "end", "step"), ("distance", "axis", "frames", "ms", "scroller")),
    "reveal": (("t", "end", "step"), ("distance", "axis", "frames", "ms", "scroller")),
    "orphan_ripple": (("t", "x", "y", "step"), ("reason", "label")),
}
STAMPED = ("video_t", "frame", "video_end_t")


def problems(ev):
    """Reasons why one event breaks the contract ([] if it is fine)."""
    typ = ev.get("type")
    if typ not in SCHEMA:
        return [f"unknown event type {typ!r}"]
    req, _opt = SCHEMA[typ]
    out = [f"{typ}: missing {k}" for k in req if ev.get(k) is None]
    for k in ("t", "end", "glide_t", "click_t"):
        v = ev.get(k)
        if v is not None and not (isinstance(v, (int, float)) and v > 1e8):
            out.append(f"{typ}: {k} must be an epoch time in seconds, got {v!r}")
    if typ == "click" and not out:
        if not ev["glide_t"] <= ev["t"] <= ev["click_t"]:
            out.append("click: expected glide_t <= t (press) <= click_t (real input)")
    if ev.get("end") is not None and ev.get("t") is not None and ev["end"] < ev["t"]:
        out.append(f"{typ}: end before t")
    return out


def check_stream(events):
    """All problems of a whole stream, each prefixed with the event's index."""
    out = []
    for i, ev in enumerate(events):
        out += [f"#{i}: {p}" for p in problems(ev)]
    return out
