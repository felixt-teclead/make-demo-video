"""Readable report (Q-07): grouped per clip, clip seconds, plain words; totals last; last line PASS or the count."""


def _when(v):
    if v.get("t0") is None:
        return ""
    if v.get("t1") is None or abs(v["t1"] - v["t0"]) < 1e-6:
        return f"{v['t0']:.2f} s"
    return f"{v['t0']:.2f}-{v['t1']:.2f} s"


def _line(v, clip):
    head = " ".join(x for x in (v["severity"], v["check"], clip, _when(v)) if x)
    return f"  {head}: {v['reason']}"


def render(res):
    lines = []
    if "abort" in res:
        lines.append(f"QA gate ({res.get('mode', 'cut')} mode): ABORT, could not measure")
        for a in res["abort"]:
            lines.append(f"  clip {a['clip']}: {a['reason']}")
        lines.append("ABORT: no verdict (the gate could not measure; this says nothing about the footage)")
        return "\n".join(lines) + "\n"

    lines.append(f"QA gate ({res['mode']} mode): {res['root']}")
    if not res["events"]:
        lines.append("  note: no event log given; checks that need logged clicks, marks, holds or step results "
                     "were not run (Q-11, Q-13, Q-44 matching, Q-51, Q-62, the click/mark explanations of Q-30)")
    for f in res["ignored"]:
        lines.append(f"  ignored (not in the clip index): {f}")

    by_clip = {}
    for v in res["violations"] + res["warnings"]:
        by_clip.setdefault(v["clip"] or "(take)", []).append(v)
    order = [c["name"] for c in res["clips"]] + ["(take)"]
    for c in res["clips"]:
        vs = by_clip.get(c["name"], [])
        nf = sum(1 for v in vs if v["severity"] == "FAIL")
        nw = len(vs) - nf
        lines.append("")
        lines.append(f"clip {c['name']} ({c['duration']:.2f} s, {c['frames']} frames): "
                     + ("ok" if not vs else f"{nf} FAIL, {nw} WARN"))
        for v in sorted(vs, key=lambda v: (v["t0"] is None, v["t0"] or 0)):
            lines.append(_line(v, c["name"]))
        for n in c["notes"]:
            lines.append(f"  info {n['check']}: {n['text']}")
    tv = by_clip.get("(take)", [])
    if tv or res.get("take_notes"):
        lines.append("")
        lines.append("take:")
        for v in tv:
            lines.append(_line(v, None))
        for n in res.get("take_notes", []):
            lines.append(f"  info {n['check']}: {n['text']}")
    for k in by_clip:
        if k not in order:
            for v in by_clip[k]:
                lines.append(_line(v, k))

    nf, nw = len(res["violations"]), len(res["warnings"])
    total = sum(c["duration"] for c in res["clips"])
    lines.append("")
    lines.append(f"totals: {len(res['clips'])} clips, {total:.2f} s, {nf} violations, {nw} warnings, "
                 f"gate time {res['elapsed']:.1f} s")
    lines.append("PASS" if nf == 0 else f"FAIL: {nf} violation{'s' if nf != 1 else ''}")
    return "\n".join(lines) + "\n"
