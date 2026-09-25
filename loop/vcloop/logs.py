"""Timing and cost log (F-28), take log (F-30), best take (F-32).

Every number comes from a timestamp or a tool's output: seconds = end - start; jev numbers are summed from the
runner's per-decision records (M2 `take.log.jsonl`, type "decision": latency_ms, cost_usd, cost_source).
"""
import json
import os
import time


def append_jsonl(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def jev_stats(run_dir):
    """Sum the jev decisions of one run folder from the runner's decision log (M2 format)."""
    recs = [r for r in read_jsonl(os.path.join(run_dir, "take.log.jsonl")) if r.get("type") == "decision"]
    n = len(recs)
    ms = sum(float(r.get("latency_ms") or 0) for r in recs)
    cost = sum(float(r.get("cost_usd") or 0) for r in recs)
    sources = {}
    for r in recs:
        s = r.get("cost_source") or "unknown"
        sources[s] = sources.get(s, 0) + 1
    texts = [r for r in read_jsonl(os.path.join(run_dir, "take.log.jsonl")) if r.get("type") == "text_call"]
    return {"jev_decisions": n, "jev_s": round(ms / 1000.0, 3),
            "ms_per_decision": round(ms / n, 1) if n else None, "jev_cost_usd": round(cost, 6),
            "cost_source": sources, "text_calls": len(texts)}


class Timing:
    """Append-only per-phase records (F-28)."""

    def __init__(self, path, job):
        self.path, self.job = path, job

    def record(self, phase, start, end, *, run_id=None, take=None, jev=None, agent_cost_usd=None,
               parallel_with=(), result=None, **extra):
        jv = jev or {"jev_decisions": 0, "jev_s": 0.0, "ms_per_decision": None, "jev_cost_usd": 0.0}
        rec = {"job": self.job, "phase": phase, "start": round(start, 3), "end": round(end, 3),
               "seconds": round(end - start, 3), "run_id": run_id, "take": take,
               "jev_decisions": jv["jev_decisions"], "jev_s": jv["jev_s"], "ms_per_decision": jv["ms_per_decision"],
               "jev_cost_usd": jv["jev_cost_usd"], "cost_source": jv.get("cost_source", {}),
               "agent_cost_usd": agent_cost_usd, "parallel_with": list(parallel_with), "result": result}
        rec.update(extra)
        append_jsonl(self.path, rec)
        return rec

    def records(self):
        return read_jsonl(self.path)


def is_human_wait(r):
    return bool(r.get("human")) or "(human)" in str(r.get("phase", ""))


def summarize(records):
    """Per-phase summary and the wall-clock total (first start to last end; parallel phases overlap).
    Human waits are reported apart and left out of both totals: the approval record spans the owner's last view of
    the spec to the approval, which can be hours before the job started (c2 074309: 5403 s wall for a 2 min job)."""
    phases = {}
    for r in records:
        p = phases.setdefault(r["phase"], {"records": 0, "seconds": 0.0, "jev_decisions": 0, "jev_s": 0.0,
                                           "jev_cost_usd": 0.0})
        p["records"] += 1
        p["seconds"] += r["seconds"]
        p["jev_decisions"] += r.get("jev_decisions") or 0
        p["jev_s"] += r.get("jev_s") or 0
        p["jev_cost_usd"] += r.get("jev_cost_usd") or 0
    for p in phases.values():
        p["ms_per_decision"] = round(p["jev_s"] * 1000 / p["jev_decisions"], 1) if p["jev_decisions"] else None
        p["seconds"] = round(p["seconds"], 3)
        p["jev_s"] = round(p["jev_s"], 3)
        p["jev_cost_usd"] = round(p["jev_cost_usd"], 6)
    auto = [r for r in records if not is_human_wait(r)]
    wall = max(r["end"] for r in auto) - min(r["start"] for r in auto) if auto else 0.0
    tot_dec = sum(p["jev_decisions"] for p in phases.values())
    tot_s = sum(p["jev_s"] for p in phases.values())
    return {"phases": phases, "wall_s": round(wall, 3), "sum_s": round(sum(r["seconds"] for r in auto), 3),
            "human_s": round(sum(r["seconds"] for r in records if is_human_wait(r)), 3),
            "jev_decisions": tot_dec, "ms_per_decision": round(tot_s * 1000 / tot_dec, 1) if tot_dec else None,
            "jev_cost_usd": round(sum(p["jev_cost_usd"] for p in phases.values()), 6)}


def summary_lines(s):
    lines = [f"  {'phase':14} {'n':>3} {'seconds':>9} {'jev dec':>8} {'ms/dec':>8} {'jev $':>10}"]
    for name, p in s["phases"].items():
        ms = "-" if p["ms_per_decision"] is None else f"{p['ms_per_decision']:.0f}"
        lines.append(f"  {name:14} {p['records']:>3} {p['seconds']:>9.1f} {p['jev_decisions']:>8} {ms:>8} "
                     f"{p['jev_cost_usd']:>10.5f}")
    hw = f"; human waits {s['human_s']:.1f} s, not included" if s.get("human_s") else ""
    lines.append(f"  wall-clock total {s['wall_s']:.1f} s (sum of phases {s['sum_s']:.1f} s; parallel phases overlap{hw}); "
                 f"jev {s['jev_decisions']} decisions, mean {s['ms_per_decision'] or 0:.0f} ms, ${s['jev_cost_usd']:.5f}")
    return lines


# ------------------------------------------------------------------------------------------------ best take

def is_hit(row):
    return bool(row.get("hit"))


def best_take(rows, length_range):
    """F-32, deterministic. rows: take-log rows (completed takes with a verdict are candidates)."""
    cand = [r for r in rows if r.get("completed") and r.get("verdict") in ("PASS", "FAIL", "ABORT") and not r.get("recut")]
    if not cand:
        return None, False
    hits = [r for r in cand if is_hit(r)]
    if hits:
        mid = (length_range[0] + length_range[1]) / 2.0
        hits.sort(key=lambda r: (r.get("warnings", 0), abs((r.get("length") or 0) - mid), r["take"]))
        return hits[0], True
    cand.sort(key=lambda r: (0 if r.get("expected_state_ok") else 1, r.get("violations", 10 ** 6), -r["take"]))
    return cand[0], False


def now():
    return time.time()
