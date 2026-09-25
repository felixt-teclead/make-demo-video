"""Variant fixes (owner 2026-09-25): when the fixer sees real alternatives it writes 2-3 candidate specs; jev ranks
them, not the fixer. Each candidate gets a dry run (no recording); its score is computed only from what jev did in
that run (the runner's take.log.jsonl, M2 format). The loop films the top candidate, plus every other candidate whose
score is within `variant_margin` of the top; the gate, the frame check and the viewer review pick among the filmed
takes (pick_key).

jev signals actually in the log (vcjev/accounting.py, vcjev/runner.py):
- step_start / step_end per action, step_end with `ok` and `decisions` -> verified, failed, re-decisions, first hit
  (the offered set is the one intended control plus WAIT, so one decision that satisfied the check = hit on the
  first decision);
- `transient` events (stale page, provider refusal) -> retries;
- `readiness_wait` with ok=false, `resolve_failed`, a run_timeout result -> timeouts;
- decision `confidence`: the TypeSafe operation head's confidence, returned by the provider (OpenRouter
  typesafe/jev-*); real and varying (0.47-0.99 over the 224 decisions of the c1-c3 runs of 2026-09-25);
- decision `probability`: the chosen target's probability from the same answer; real, but 1.0 in every one of those
  224 decisions (the offered set is one target plus WAIT), so its default weight is 0.
No token logprobs: the provider's jev endpoint returns choice probabilities, not token logprobs.
"""
import difflib
import json
import os

DEFAULT_WEIGHTS = "verified=0.5,first_hit=0.2,confidence=0.3,probability=0,redecision=0.05,transient=0.05,timeout=0.1"
KEYS = ("verified", "first_hit", "confidence", "probability", "redecision", "transient", "timeout")


def parse_weights(text):
    w = dict.fromkeys(KEYS, 0.0)
    for part in (text or DEFAULT_WEIGHTS).split(","):
        if not part.strip():
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        if k not in w:
            raise ValueError(f"variant_score_weights: unknown signal {k!r} (known: {', '.join(KEYS)})")
        w[k] = float(v)
    return w


def _read(path):
    out = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def signals(run_dir, result, planned):
    """Raw counts of one dry run. `planned` = the spec's jev actions (a run that stopped early did not start all)."""
    recs = _read(os.path.join(run_dir, "take.log.jsonl"))
    ends = [r for r in recs if r.get("type") == "step_end"]
    starts = [r for r in recs if r.get("type") == "step_start"]
    trans = [r for r in recs if r.get("type") == "transient"]
    if ends:
        verified = sum(1 for e in ends if e.get("ok"))
        failed = sum(1 for e in ends if not e.get("ok"))
        redec = sum(max(0, int(e.get("decisions") or 0) - 1) for e in ends)
        retried = {t.get("step") for t in trans}
        first_hit = sum(1 for e in ends if e.get("ok") and int(e.get("decisions") or 0) == 1
                        and e.get("step") not in retried)
    else:                                   # no action events: fall back to the step list of result.json
        steps = result.get("steps") or []
        verified = sum(1 for s in steps if s.get("verified"))
        failed = sum(1 for s in steps if not s.get("verified"))
        redec = sum(max(0, int(s.get("decisions") or 1) - 1) for s in steps)
        first_hit = sum(1 for s in steps if s.get("verified") and int(s.get("decisions") or 1) <= 1)
    timeouts = (sum(1 for r in recs if r.get("type") == "readiness_wait" and r.get("ok") is False)
                + sum(1 for r in recs if r.get("type") == "resolve_failed")
                + (1 if result.get("error_class") == "run_timeout" else 0))
    dec = [r for r in recs if r.get("type") == "decision" and r.get("executed") is not False]
    conf = [float(r["confidence"]) for r in dec if isinstance(r.get("confidence"), (int, float))]
    prob = [float(r["probability"]) for r in dec if isinstance(r.get("probability"), (int, float))]
    planned = max(int(planned or 0), len(starts), verified + failed, 1)
    return {"planned": planned, "verified": verified, "failed": failed, "redecisions": redec, "first_hit": first_hit,
            "transients": len(trans), "timeouts": timeouts, "decisions": len(dec),
            "confidence_mean": round(sum(conf) / len(conf), 4) if conf else None,
            "probability_min": round(min(prob), 4) if prob else None, "dry_ok": bool(result.get("ok"))}


def score(sig, weights):
    """Deterministic score (higher is better) and its per-signal terms. A missing confidence/probability adds 0."""
    n = sig["planned"]
    terms = {"verified": weights["verified"] * sig["verified"] / n,
             "first_hit": weights["first_hit"] * sig["first_hit"] / n,
             "confidence": weights["confidence"] * (sig["confidence_mean"] or 0.0),
             "probability": weights["probability"] * (sig["probability_min"] or 0.0),
             "redecision": -weights["redecision"] * sig["redecisions"],
             "transient": -weights["transient"] * sig["transients"],
             "timeout": -weights["timeout"] * sig["timeouts"]}
    terms = {k: round(v, 4) for k, v in terms.items()}
    return round(sum(terms.values()), 4), terms


def combine(runs):
    """Several dry runs of one candidate: mean score; a candidate is filmable only if every dry run was green."""
    s = round(sum(r["score"] for r in runs) / len(runs), 4)
    return s, all(r["signals"]["dry_ok"] for r in runs)


def breakdown(v):
    """One line per candidate for the take log, the report and the ledger."""
    if not v.get("runs"):
        return f"{v['id']}: not scored ({v.get('dropped') or v.get('status')})"
    sig = v["runs"][-1]["signals"]
    conf = "-" if sig["confidence_mean"] is None else f"{sig['confidence_mean']:.2f}"
    return (f"{v['id']}: score {v['score']:.4f} ({'dry green' if v['dry_ok'] else 'dry FAILED'}; verified "
            f"{sig['verified']}/{sig['planned']}, failed {sig['failed']}, first-decision hits {sig['first_hit']}, "
            f"re-decisions {sig['redecisions']}, retries {sig['transients']}, timeouts {sig['timeouts']}, jev "
            f"confidence {conf}; {len(v['runs'])} dry run(s))")


def rank(cands):
    """Green candidates, best score first; ties keep the fixer's listing order."""
    return sorted((c for c in cands if c.get("dry_ok")), key=lambda c: (-c["score"], c["k"]))


def to_film(ranked, margin, takes_left):
    if not ranked or takes_left < 1:
        return []
    top = ranked[0]["score"]
    close = [c for c in ranked if top - c["score"] <= margin + 1e-9]
    return close[:takes_left]


def distance(text, ref_text):
    """Changed lines against the approved spec: the tie-break 'closest to what the owner approved'."""
    if ref_text is None:
        return 0
    d = difflib.unified_diff(ref_text.splitlines(), text.splitlines(), lineterm="", n=0)
    return sum(1 for ln in d if ln[:1] in "+-" and not ln.startswith(("+++", "---")))


def pick_key(f):
    """Among filmed candidates: a hit first, then fewest violations, warnings, review findings, then closest to the
    approved spec, then jev score, then listing order."""
    viol = f.get("violations")
    return (0 if f.get("hit") else 1, 10 ** 6 if viol is None else viol, f.get("warnings") or 0,
            f.get("review_findings") or 0, f.get("distance") or 0, -f.get("score", 0), f["k"])
