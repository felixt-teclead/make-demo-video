"""Q-03 check both layers: raw and cut (S2, diagnostic).

`vc-gate RUN_DIR --layers` runs the gate on the delivered clips (cut mode, the verdict) and then every pixel check on
the run's raw capture (raw mode). Each defect is then placed in the layer that made it:

- **recording**: found in the clips and, at the same raw time, in the raw capture (fix the step or the page handling);
- **cutter**: found only in the clips (made by the cut: a splice, a boundary, a speed-up);
- **raw only**: found only in the raw capture. "removed by the cutter" when none of its raw frames reached a clip
  (e.g. a white-out that the cutter dropped); otherwise "not seen in the clips" (shortened or covered).

Clip time maps to raw time through the cut record: `frame_map` (the raw frame of every delivered frame), else
`kept` pieces (`clip_start, clip_end, src_start, src_end, factor`), else `map` (`clip_start, src_start, src_end,
speed`). Structure checks (clips, results, length, holds) have no raw counterpart and are listed as such.
The raw run is a diagnostic: it never changes the verdict and its time is not part of the C-27 budget.
"""

PIXEL_CHECKS = {"Q-20", "Q-21", "Q-23", "Q-30", "Q-31", "Q-34", "Q-40", "Q-42", "Q-43", "Q-44", "Q-45"}
TOL_S = 0.25   # a raw defect matches a clip defect of the same check when their raw times are within 0.25 s


class ClipMap:
    """Clip seconds <-> raw seconds for one delivered clip."""

    def __init__(self, rec, fps):
        self.fps = float(fps or 30.0)
        self.frame_map = rec.get("frame_map") or []
        self.pieces = []
        for k in rec.get("kept") or []:
            self.pieces.append((float(k["clip_start"]), float(k["clip_end"]), float(k["src_start"]),
                                float(k["src_end"]), float(k.get("factor") or 1.0)))
        if not self.pieces:
            for m in rec.get("map") or []:
                sp = float(m.get("speed") or m.get("factor") or 1.0)
                a, b = float(m["src_start"]), float(m["src_end"])
                c0 = float(m.get("clip_start", 0.0))
                self.pieces.append((c0, c0 + (b - a) / sp, a, b, sp))
        src = rec.get("source") or {}
        self.offset = src.get("start_t")

    @property
    def usable(self):
        return bool(self.frame_map or self.pieces or self.offset is not None)

    def to_raw(self, t):
        if self.frame_map:
            i = min(max(int(round(t * self.fps)), 0), len(self.frame_map) - 1)
            return self.frame_map[i] / self.fps
        for c0, c1, s0, s1, f in self.pieces:      # pieces are [clip_start, clip_end): a splice time is the next piece
            if c0 - 1e-6 <= t < c1 - 1e-6:
                return s0 + (t - c0) * f
        for c0, c1, s0, s1, f in self.pieces:
            if c0 - 1e-6 <= t <= c1 + 1e-6:
                return s0 + (t - c0) * f
        if self.pieces:   # outside every piece: the nearest piece end
            c0, c1, s0, s1, f = min(self.pieces, key=lambda p: min(abs(t - p[0]), abs(t - p[1])))
            return s0 if abs(t - c0) < abs(t - c1) else s1
        return float(self.offset) + t

    def raw_span(self, t0, t1):
        """Raw [lo, hi] covered by clip seconds [t0, t1]."""
        if t1 is None or t1 < t0:
            t1 = t0
        if self.frame_map:
            a = min(max(int(round(t0 * self.fps)), 0), len(self.frame_map) - 1)
            b = min(max(int(round(t1 * self.fps)), a), len(self.frame_map) - 1)
            fr = self.frame_map[a:b + 1]
            return min(fr) / self.fps, max(fr) / self.fps
        return self.to_raw(t0), self.to_raw(t1)

    def kept_frames(self, lo, hi):
        """How many raw frames of the defect [lo, hi) reached this clip (the gate's t1 is exclusive; lo == hi is
        one frame)."""
        if self.frame_map:
            a, b = int(round(lo * self.fps)), int(round(hi * self.fps))
            b = max(b, a + 1)
            return sum(1 for f in self.frame_map if a <= f < b)
        n = 0
        for c0, c1, s0, s1, f in self.pieces:
            ov = min(hi, s1) - max(lo, s0)
            if ov >= 0:
                n += max(1, int(round(ov * self.fps / max(f, 1.0))))
        return n


def _span(v):
    t0 = v.get("t0")
    t1 = v.get("t1")
    if t0 is None:
        return None
    return float(t0), float(t1 if t1 is not None else t0)


def diagnose(cut_res, raw_res, record, fps_by_clip):
    """Returns {"items": [...], "counts": {...}, "notes": [...]}. Each item: check, severity, layer, where, reason,
    clip, t0, t1, raw_t0, raw_t1."""
    if "abort" in raw_res:   # the raw run could not measure: no layer for any defect (Q-02), the verdict stands
        return {"items": [], "counts": {}, "abort": raw_res["abort"],
                "notes": ["raw capture could not be measured: no defect is placed in a layer"]}
    maps = {name: ClipMap(rec, fps_by_clip.get(name, 30.0)) for name, rec in record.items()}
    notes = []
    raw_all = [v for v in raw_res.get("violations", []) + raw_res.get("warnings", []) if v["check"] in PIXEL_CHECKS]
    used = set()
    items = []
    for v in cut_res.get("violations", []) + cut_res.get("warnings", []):
        it = {"check": v["check"], "severity": v["severity"], "clip": v.get("clip"), "t0": v.get("t0"),
              "t1": v.get("t1"), "reason": v["reason"], "raw_t0": None, "raw_t1": None}
        if v["check"] not in PIXEL_CHECKS:
            it["layer"] = "structure"
            it["where"] = "a take-structure check: no raw counterpart"
            items.append(it)
            continue
        m = maps.get(v.get("clip"))
        sp = _span(v)
        if m is None or not m.usable or sp is None:
            it["layer"] = "cutter"
            it["where"] = "found in the clips; no time map to the raw capture (cut record missing)"
            items.append(it)
            continue
        lo, hi = m.raw_span(*sp)
        it["raw_t0"], it["raw_t1"] = round(lo, 3), round(hi, 3)
        hit = None
        for i, r in enumerate(raw_all):
            rs = _span(r)
            if r["check"] != v["check"] or rs is None:
                continue
            if rs[0] <= hi + TOL_S and rs[1] >= lo - TOL_S:
                hit = i
                if i not in used:
                    break
        if hit is not None:
            used.add(hit)
            r = raw_all[hit]
            it["layer"] = "recording"
            it["where"] = f"in the clips and in the raw capture (raw {_fmt(_span(r))}): a recording problem"
        else:
            it["layer"] = "cutter"
            it["where"] = f"only in the clips (raw {lo:.2f} s shows none): made by the cutter"
        items.append(it)
    for i, r in enumerate(raw_all):
        if i in used:
            continue
        rs = _span(r)
        kept = sum(m.kept_frames(rs[0], rs[1]) for m in maps.values()) if rs else 0
        it = {"check": r["check"], "severity": r["severity"], "clip": None, "t0": None, "t1": None,
              "reason": r["reason"], "raw_t0": rs[0] if rs else None, "raw_t1": rs[1] if rs else None,
              "layer": "raw only"}
        if not maps:
            it["where"] = "only in the raw capture; no cut record to say whether the cutter removed it"
        elif kept == 0:
            it["where"] = "only in the raw capture: removed by the cutter (no frame of it reached a clip)"
        else:
            it["where"] = f"only in the raw capture: not seen in the clips ({kept} of its frames were kept)"
        items.append(it)
    counts = {}
    for it in items:
        counts[it["layer"]] = counts.get(it["layer"], 0) + 1
    if not record:
        notes.append("no cut record: clip defects cannot be placed on the raw clock")
    return {"items": items, "counts": counts, "notes": notes}


def _fmt(sp):
    if sp is None:
        return "?"
    return f"{sp[0]:.2f} s" if abs(sp[1] - sp[0]) < 1e-6 else f"{sp[0]:.2f}-{sp[1]:.2f} s"


def render(diag, raw_res):
    lines = ["", "layers (Q-03, diagnostic: raw capture vs delivered clips; does not change the verdict):"]
    if "abort" in raw_res:
        lines.append("  raw capture could not be measured: " + "; ".join(a["reason"] for a in raw_res["abort"]))
        return "\n".join(lines) + "\n"
    order = {"recording": 0, "cutter": 1, "raw only": 2, "structure": 3}
    for it in sorted(diag["items"], key=lambda x: (order.get(x["layer"], 9), x["raw_t0"] or 0)):
        where = (f"{it['clip']} {_fmt(_span(it))}" if it["clip"] else f"raw {_fmt((it['raw_t0'], it['raw_t1']))}"
                 if it["raw_t0"] is not None else "(take)")
        lines.append(f"  {it['layer'].upper():9} {it['severity']} {it['check']} {where}: {it['where']}")
        lines.append(f"            {it['reason']}")
    for n in diag["notes"]:
        lines.append(f"  note: {n}")
    c = diag["counts"]
    lines.append("  layer totals: " + ", ".join(f"{k} {c.get(k, 0)}" for k in ("recording", "cutter", "raw only",
                                                                             "structure"))
                 + f"; raw run {raw_res.get('elapsed', 0):.1f} s (not counted toward the gate budget, C-27)")
    return "\n".join(lines) + "\n"
