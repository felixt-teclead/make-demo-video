"""FIXES-LEDGER.md entries written by the loop (owner rule: every fix the system makes gets one entry).

The fixer answers with a `ledger` object (title, root cause, scope, lives in); the loop adds the fields it knows
(date, case, symptom from the review's ledger candidates or the gate, the change it applied) and the next free FX id.
After the retake is judged, the entry's evidence line gets the retake's result.
"""
import fcntl
import os
import re

HEADING = "## Loop fixes (written by vc-loop)"
FX_RE = re.compile(r"^### FX-(\d+)\b", re.M)
FIELDS = ("date", "case/spec", "symptom", "root cause", "change", "evidence", "scope guess", "lives in",
          "owner decision")


def path(cfg, root):
    return os.environ.get("VC_LEDGER") or cfg.get("ledger") or os.path.join(root, "FIXES-LEDGER.md")


def next_id(text):
    return max((int(n) for n in FX_RE.findall(text)), default=0) + 1


def candidate_symptoms(md):
    """The `- symptom:` lines of a ledger-candidates file (review.ledger_candidates)."""
    return [ln[len("- symptom: "):] for ln in (md or "").splitlines() if ln.startswith("- symptom: ")]


def entry(fx_id, title, fields):
    lines = [f"### FX-{fx_id:02d} {title}".rstrip()]
    for k in FIELDS:
        lines.append(f"- {k}: {fields.get(k, '')}".rstrip())
    return "\n".join(lines) + "\n"


def append(ledger_path, title, fields):
    """Append one entry under the loop's heading with the next free id; returns the id (e.g. 'FX-27').
    Locked: parallel jobs share the ledger and must not take the same id."""
    with open(ledger_path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        text = f.read()
        n = next_id(text)
        block = "" if not text or text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n"
        if HEADING not in text:
            block += HEADING + "\n\n"
        f.write(block + entry(n, title, fields))
        fcntl.flock(f, fcntl.LOCK_UN)
    return f"FX-{n:02d}"


def set_evidence(ledger_path, fx_id, evidence):
    """Replace the evidence line of entry `fx_id`."""
    with open(ledger_path, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        text = f.read()
        m = re.search(rf"^### {re.escape(fx_id)}\b.*?(?=^### |\Z)", text, re.M | re.S)
        if m:
            block = re.sub(r"^- evidence:.*$", lambda _: f"- evidence: {evidence}", m.group(0), count=1, flags=re.M)
            text = text[:m.start()] + block + text[m.end():]
            f.seek(0)
            f.truncate()
            f.write(text)
        fcntl.flock(f, fcntl.LOCK_UN)
    return bool(m)
