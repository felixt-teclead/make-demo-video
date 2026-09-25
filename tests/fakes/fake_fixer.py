"""FAKE fixer: EDITS ONLY (the loop starts every retake). One hypothesis per call.

fix mode: the failing step (from the verdict's first violation or the dry run's failed step) gets a readiness wait on
its first click (`wait_before`, a "how" change F-15 allows). If that step already has one, it raises that step's
hold by 0.5 s (also a "how" change) - which does not remove an unfixable defect.
propose mode: writes only the next hypothesis. Scenario {"fixer": "rogue"} deletes an expected state instead (a
contract change the loop must refuse); {"fixer": "flake"} asks for one unchanged retake; {"fixer": "stop-login"}.
"""
import json
import os
import re
import sys

req = json.load(open(sys.argv[1]))
sc = json.load(open(os.environ["VC_FAKE_SCENARIO"])) if os.environ.get("VC_FAKE_SCENARIO") else {}
src = req.get("source") or {}
step = src.get("failed_step")
if not step:
    for v in src.get("first_violations") or []:
        step = v.split()[1]
        break
if not step:                               # a frame-check failure names the step in its note
    m = re.match(r"([a-z0-9-]+): ", str(src.get("frame_check") or ""))
    step = m.group(1) if m else None
path = req["spec_path"]
text = open(path, encoding="utf-8").read()
lines = text.split("\n")
out = {"model": "fake/" + req.get("model", "?")}


def step_span(name):
    start = next(i for i, l in enumerate(lines) if l.strip() == f'name = "{name}"')
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[[steps]]") or lines[i].startswith("[knobs]")), len(lines))
    return start, end


mode = sc.get("fixer", "normal")
if req["mode"] == "propose":
    out.update(kind="fix", hypothesis=f"step {step}: try a longer readiness wait", next=f"step {step}: add an off-camera warm-up of its route")
elif mode == "flake":                      # asks for an unchanged retake every time (the loop allows one)
    out.update(kind="flake", hypothesis="the provider returned a 502 once (API blip)")
elif mode == "stop-login":
    out.update(kind="stop", stop={"reason": "login", "detail": "the session expired during the take"})
elif mode == "rogue":
    s, e = step_span(step)
    for i in range(s, e):
        if lines[i].startswith("expect = ["):
            old = lines[i]
            lines[i] = re.sub(r"\{[^{}]*\}(, )?", "", old, count=1).replace("[, ", "[")
            break
    open(path, "w", encoding="utf-8").write("\n".join(lines))
    out.update(kind="fix", hypothesis=f"step {step}: the expected text is too strict",
               change={"what": "expect", "where": step, "old": old, "new": lines[i], "why": "drop a check"})
else:
    s, e = step_span(step)
    act = next((i for i in range(s, e) if lines[i].strip().startswith('op = "click"') or lines[i].strip().startswith('op = "type"')), None)
    has = any("wait_before" in lines[i] for i in range(s, e))
    if not has and act is not None:
        exp = next(l for l in lines[s:e] if l.startswith("expect = ["))
        first = re.search(r"\{[^{}]*\}", exp).group(0)
        lines.insert(act + 1, f"  wait_before = [{{ type = \"dialog_closed\" }}]")
        out.update(kind="fix", hypothesis=f"step {step} fails because the view is still loading when it is filmed; "
                                          "waiting for readiness before the click fixes it",
                   change={"what": "wait_before", "where": f"step {step}, first action", "old": "none",
                           "new": "wait for readiness (no open dialog)", "why": "black frames while the view loads"})
    else:
        hi = next(i for i in range(s, e) if lines[i].startswith("hold = "))
        old = float(lines[hi].split("=")[1])
        lines[hi] = f"hold = {old + 0.5}"
        out.update(kind="fix", hypothesis=f"step {step}: a longer hold lets the view settle",
                   change={"what": "hold", "where": f"step {step}", "old": old, "new": old + 0.5, "why": "view settles late"})
    open(path, "w", encoding="utf-8").write("\n".join(lines))
if out.get("kind") == "fix" and req["mode"] == "fix":
    cands = (req.get("ledger") or {}).get("candidates")
    out["ledger"] = {"title": f"{step}: " + ("viewer-review blocker fixed" if cands else "readiness before filming"),
                     "root_cause": out.get("hypothesis"), "scope": "SPECIFIC", "lives_in": "spec"}
json.dump(out, open(req["out"], "w"), indent=1)
