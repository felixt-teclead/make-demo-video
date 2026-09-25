"""Per-decision accounting (F-28, C-12). Pure standard library.

Every jev decision gets one JSONL record with its latency, token usage and cost.
The cost is the provider's when the response carries one ("provider"), otherwise
tokens x configured price ("computed"). Text-helper calls are logged with their
latency. Summaries are recomputed from the log, never typed in.
"""

import json
import time
from pathlib import Path


def tokens(usage):
    usage = usage or {}
    tin = usage.get("prompt_tokens", usage.get("input_tokens"))
    tout = usage.get("completion_tokens", usage.get("output_tokens"))
    if tin is None and tout is None and "total_tokens" in usage:
        tin, tout = usage["total_tokens"], 0
    return int(tin or 0), int(tout or 0)


def cost(usage, price_per_mtok):
    """(cost_usd, source). price_per_mtok = (input, output) USD per 1M tokens."""
    usage = usage or {}
    provider_cost = usage.get("cost")
    if provider_cost is None and isinstance(usage.get("cost_details"), dict):
        provider_cost = usage["cost_details"].get("upstream_inference_cost")
    if isinstance(provider_cost, (int, float)):
        return float(provider_cost), "provider"
    tin, tout = tokens(usage)
    return (tin * price_per_mtok[0] + tout * price_per_mtok[1]) / 1_000_000, "computed"


class BudgetExceeded(RuntimeError):
    pass


class DecisionLog:
    """Append-only JSONL log of jev decisions and text-helper calls for one job."""

    def __init__(self, path, *, job, run_id=None, phase=None, take=None, budget_usd=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.job, self.run_id, self.phase, self.take = job, run_id, phase, take
        self.budget_usd = budget_usd

    def context(self, *, run_id=None, phase=None, take=None):
        self.run_id = run_id if run_id is not None else self.run_id
        self.phase = phase if phase is not None else self.phase
        self.take = take if take is not None else self.take

    def _append(self, record):
        base = {"ts": round(time.time(), 3), "job": self.job, "run_id": self.run_id, "phase": self.phase,
                "take": self.take}
        with self.path.open("a") as f:
            f.write(json.dumps({**base, **record}, ensure_ascii=False) + "\n")

    def decision(self, *, step, n, offered, decision, model_cfg, response=None, extra=None):
        usage = decision.get("usage") or (response or {}).get("usage") or {}
        tin, tout = tokens(usage)
        usd, source = cost(usage, model_cfg["price"])
        rec = {
            "type": "decision",
            "step": step,
            "n": n,
            "offered": offered,
            "choice": decision.get("choice"),
            "operation": decision.get("operation"),
            "target": decision.get("target"),
            "probability": (decision.get("probabilities") or {}).get(decision.get("choice")),
            "confidence": decision.get("confidence"),
            "model": model_cfg["model"],
            "response_model": decision.get("model"),
            "latency_ms": decision.get("latency_ms"),
            "tokens_in": tin,
            "tokens_out": tout,
            "cost_usd": round(usd, 8),
            "cost_source": source,
            "usage": usage,
        }
        if extra:
            rec.update(extra)
        self._append(rec)
        return rec

    def event(self, type_, **fields):
        self._append({"type": type_, **fields})

    def text_call(self, *, step, latency_ms, model, usage=None, price=(0, 0)):
        usd, source = cost(usage, price)
        tin, tout = tokens(usage)
        self._append({"type": "text_call", "step": step, "model": model, "latency_ms": latency_ms,
                      "tokens_in": tin, "tokens_out": tout, "cost_usd": round(usd, 8), "cost_source": source})

    def records(self):
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def job_cost(self):
        return sum(r.get("cost_usd", 0) for r in self.records() if r.get("job") == self.job and r["type"] in
                   ("decision", "text_call"))

    def check_budget(self):
        if self.budget_usd is not None and self.job_cost() >= self.budget_usd:
            raise BudgetExceeded(f"job {self.job}: jev cost reached the ${self.budget_usd} budget (C-12)")


def summarize(records, group_by=None):
    """Decision count, jev seconds, mean ms per decision, tokens and $ — overall
    or grouped by a record field (e.g. "phase", "take", "run_id")."""

    def agg(rs):
        ds = [r for r in rs if r["type"] == "decision"]
        ts = [r for r in rs if r["type"] == "text_call"]
        ms = sum(r["latency_ms"] or 0 for r in ds)
        return {
            "decisions": len(ds),
            "jev_seconds": round(ms / 1000, 3),
            "ms_per_decision": round(ms / len(ds), 1) if ds else None,
            "tokens_in": sum(r["tokens_in"] for r in ds + ts),
            "tokens_out": sum(r["tokens_out"] for r in ds + ts),
            "jev_cost_usd": round(sum(r["cost_usd"] for r in ds), 8),
            "text_calls": len(ts),
            "text_cost_usd": round(sum(r["cost_usd"] for r in ts), 8),
            "cost_sources": sorted({r["cost_source"] for r in ds + ts}),
        }

    if not group_by:
        return agg(records)
    groups = {}
    for r in records:
        groups.setdefault(r.get(group_by), []).append(r)
    return {str(k): agg(v) for k, v in groups.items()}


def phase_record(records, *, job, phase, start, end, run_id=None, take=None, parallel_with=(), result=None,
                 agent_cost_usd=None):
    """The jev columns of one F-28 phase record, computed from the decision log."""
    rs = [r for r in records if r.get("job") == job and r.get("phase") == phase
          and (run_id is None or r.get("run_id") == run_id) and (take is None or r.get("take") == take)]
    s = summarize(rs)
    return {
        "job": job, "phase": phase, "start": start, "end": end, "seconds": round(end - start, 3),
        "run_id": run_id, "take": take, "jev_decisions": s["decisions"], "jev_seconds": s["jev_seconds"],
        "ms_per_decision": s["ms_per_decision"], "jev_cost_usd": s["jev_cost_usd"],
        "jev_tokens_in": s["tokens_in"], "jev_tokens_out": s["tokens_out"],
        "agent_cost_usd": agent_cost_usd, "parallel_with": list(parallel_with), "result": result,
    }
