"""FAKE fixer: EDITS ONLY (the loop starts every retake). One hypothesis per call.

fix mode: the failing step (from the verdict's first violation or the dry run's failed step) gets a readiness wait on
its first click (`wait_before`, a "how" change F-15 allows). If that step already has one, it raises that step's
hold by 0.5 s (also a "how" change) - which does not remove an unfixable defect.
propose mode: writes only the next hypothesis. Scenario {"fixer": "rogue"} deletes an expected state instead (a
contract change the loop must refuse); {"fixer": "flake"} asks for one unchanged retake; {"fixer": "stop-login"}.
{"fixer": "variants" | "variants-hold-first" | "variants-rogue" | "variants-many"}: the first fix returns candidate
spec copies (VARIANT_SETS) instead of one edit; later fixes are normal.
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


def v_wait(ls):
    s, e = step_span_in(ls, step)
    act = next(i for i in range(s, e) if ls[i].strip().startswith(('op = "click"', 'op = "type"')))
    ls.insert(act + 1, '  wait_before = [{ type = "dialog_closed" }]')
    return {"what": "wait_before", "where": f"step {step}, first action", "old": "none",
            "new": "wait for readiness (no open dialog)", "why": "black frames while the view loads"}


def v_hold(ls):
    s, e = step_span_in(ls, step)
    hi = next(i for i in range(s, e) if ls[i].startswith("hold = "))
    old = float(ls[hi].split("=")[1])
    ls[hi] = f"hold = {old + 0.5}"
    return {"what": "hold", "where": f"step {step}", "old": old, "new": old + 0.5, "why": "view settles late"}


def v_both(ls):
    ch = v_wait(ls)
    v_hold(ls)
    return dict(ch, what="wait_before + hold", new="readiness wait and +0.5 s hold")


def v_rogue(ls):
    s, e = step_span_in(ls, step)
    i = next(i for i in range(s, e) if ls[i].startswith("expect = ["))
    old = ls[i]
    ls[i] = re.sub(r"\{[^{}]*\}(, )?", "", old, count=1).replace("[, ", "[")
    return {"what": "expect", "where": step, "old": old, "new": ls[i], "why": "drop a check"}


def v_approval(ls):
    i = next(i for i, l in enumerate(ls) if l.startswith("approver = "))
    old = ls[i]
    ls[i] = 'approver = "fixer"'
    return {"what": "approver", "where": "[approval]", "old": old, "new": ls[i], "why": "owner-only field"}


def step_span_in(ls, name):
    start = next(i for i, l in enumerate(ls) if l.strip() == f'name = "{name}"')
    end = next((i for i in range(start + 1, len(ls)) if ls[i].startswith("[[steps]]") or ls[i].startswith("[knobs]")), len(ls))
    return start, end


VARIANT_SETS = {"variants": [v_wait, v_hold], "variants-hold-first": [v_hold, v_wait],
                "variants-rogue": [v_wait, v_rogue, v_approval], "variants-many": [v_wait, v_hold, v_both, v_hold]}
mode = sc.get("fixer", "normal")
if mode in VARIANT_SETS and req["mode"] == "fix" and not req.get("variants_tried"):
    # lists candidates only; never scores or ranks them, never edits the live spec
    out.update(kind="variants", hypothesis=f"step {step}: the view is not ready when filmed; the lever is unclear",
               variants=[])
    for k, fn in enumerate(VARIANT_SETS[mode], 1):
        ls = list(lines)
        ch = fn(ls)
        f = req["variant_file"].replace("{k}", str(k))
        open(f, "w", encoding="utf-8").write("\n".join(ls))
        out["variants"].append({"id": chr(64 + k), "hypothesis": f"step {step}: {ch['why']}", "change": ch,
                                "ledger": {"title": f"{step}: {ch['what']}", "root_cause": ch["why"],
                                           "scope": "SPECIFIC", "lives_in": "spec"}, "spec_file": f})
    json.dump(out, open(req["out"], "w"), indent=1)
    sys.exit(0)
if mode in VARIANT_SETS:
    mode = "normal"
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
