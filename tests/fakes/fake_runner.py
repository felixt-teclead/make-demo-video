"""FAKE runner component for loop tests: a fake jev (decision records in M2's take.log.jsonl format) plus a fake
recorder (M1 run-dir layout). No browser, no network. Behaviour is scripted by a scenario JSON ($VC_FAKE_SCENARIO):

  {"video": "synth" | "none",          # synth: a real synthetic raw.mp4 (tests/fakes/synth_video.py) for the real
                                       #        cutter + gate; none: manifest only, for the stub cutter/gate
   "defect": {"step": "bpmn", "kind": "black", "fixable": true},
                                       # the defect appears in every take until the step's click has
                                       # `wait_before` (fixable) - or always (fixable: false)
   "dry_fail": {"step": "raci", "until_fix": true},   # dry runs fail at this step until it has `wait_before`
   "login_in_take": 0,                 # take number whose page asks for a login (C-31)
   "store": "/path/items.json"}        # the fixture page's item store (writes + cleanup, C-34)

Usage (as the loop's "run" component): python3 fake_runner.py REQUEST.json
"""
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def scenario():
    p = os.environ.get("VC_FAKE_SCENARIO")
    if p and os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {"video": "none"}


def has_fix(step):
    return any(a.get("wait_before") for a in step.get("actions", []))


def decision(log, req, step, n, action, i):
    seed = int(hashlib.sha256(f"{req['run_id']}:{step['name']}:{i}".encode()).hexdigest()[:6], 16)
    lat = 2200 + seed % 900                                      # deterministic "latency"
    tin = 1180 + seed % 60
    label = (action.get("control") or {}).get("label") or (action.get("control") or {}).get("label_contains")
    kind = {"click": "click", "type": "fill", "select": "select"}[action["op"]]
    rec = {"ts": time.time(), "job": req["job"], "run_id": req["run_id"], "phase": req["mode"],
           "take": req["n"] if req["mode"] == "take" else None, "type": "decision", "step": step["name"], "n": n,
           "offered": [{"id": f"e{i}{kind[0]}", "kind": kind, "role": "button", "own_label": label, "node": i},
                       {"id": "wait", "kind": "wait"}],
           "choice": f"e{i}{kind[0]}", "operation": kind.upper(), "target": label, "probability": 0.97,
           "confidence": 0.95, "model": "fake/jev", "latency_ms": lat, "tokens_in": tin, "tokens_out": 70,
           "cost_usd": round(tin * 4.2e-8, 9), "cost_source": "provider", "executed": True,
           "click_point": {"x": 400 + seed % 900, "y": 200 + seed % 600}}
    log.write(json.dumps(rec) + "\n")


def load_store(sc):
    p = sc.get("store")
    if not p:
        return None, None
    if os.path.exists(p):
        with open(p) as f:
            return p, json.load(f)
    return p, {"items": []}


def save_store(p, st):
    with open(p, "w") as f:
        json.dump(st, f, indent=1)


def main(req_path):
    with open(req_path) as f:
        req = json.load(f)
    sc = scenario()
    rd = req["run_dir"]
    os.makedirs(rd, exist_ok=True)
    spec = req["spec"]
    res = {"mode": req["mode"], "run_id": req["run_id"], "ok": True, "completed": True, "steps": [],
           "failed_step": None, "reason": None, "created_items": [], "deleted_items": []}
    store_p, store = load_store(sc)
    if req["mode"] == "cleanup":
        mine = {(i["id"], i["name"]) for i in req.get("cleanup_items", [])}
        keep = []
        for it in (store or {}).get("items", []):
            if (it["id"], it["name"]) in mine:
                res["deleted_items"].append({"id": it["id"], "name": it["name"]})
            else:
                keep.append(it)
        if store is not None:
            store["items"] = keep
            save_store(store_p, store)
        with open(os.path.join(rd, "result.json"), "w") as f:
            json.dump(res, f, indent=1)
        return 0
    if req["mode"] == "take" and sc.get("login_in_take") == req["n"]:
        res.update(ok=False, completed=False, login_required=True, reason="login page", failed_step=spec["steps"][0]["name"])
        with open(os.path.join(rd, "result.json"), "w") as f:
            json.dump(res, f, indent=1)
        return 0
    defects = {}
    with open(os.path.join(rd, "take.log.jsonl"), "a") as log:
        for si, step in enumerate(spec["steps"]):
            n = 0
            for i, a in enumerate(step.get("actions", [])):
                if a["op"] in ("click", "type", "select"):
                    n += 1
                    decision(log, req, step, n, a, si * 10 + i)
            ok, reason = True, None
            df = sc.get("dry_fail") or {}
            if req["mode"] == "dry" and df.get("step") == step["name"] and not (df.get("until_fix") and has_fix(step)):
                ok, reason = False, "expected state not reached within settle_s: the view was still loading"
            if ok and step.get("write"):
                for a in step.get("actions", []):
                    if a["op"] == "type" and store is not None:
                        it = {"id": "it-" + hashlib.sha256(a["text"].encode()).hexdigest()[:8], "name": a["text"],
                              "by": req["job"]}
                        store["items"].append(it)
                        res["created_items"].append({"id": it["id"], "name": it["name"], "write": step["write"],
                                                     "step": step["name"]})
            res["steps"].append({"name": step["name"], "ok": ok, "verified": ok, "reason": reason,
                                 "decisions": n})
            if not ok:
                res.update(ok=False, failed_step=step["name"], reason=reason)
                break
    if store is not None:
        save_store(store_p, store)
    d = sc.get("defect") or {}
    if req["mode"] == "take" and d.get("step"):
        st = next((s for s in spec["steps"] if s["name"] == d["step"]), None)
        if st and not (d.get("fixable", True) and has_fix(st)):
            defects[d.get("kind", "black")] = d["step"]
    if req["mode"] == "take":
        steps = [{"name": s["name"], "actions": [
            {"kind": "type" if a["op"] == "type" else "click",
             "label": (a.get("control") or {}).get("label") or (a.get("control") or {}).get("label_contains") or a["op"],
             "hold": float(a["hold"]) if a.get("hold") is not None else None,
             "hold_kind": a.get("hold_kind", "dialog")}
            for a in s.get("actions", []) if a["op"] in ("click", "type", "select")]} for s in spec["steps"]]
        # the step-end hold goes on the step's last action; action-level holds stay on their action
        for s, spec_s in zip(steps, spec["steps"]):
            if not s["actions"]:
                s["actions"] = [{"kind": "click", "label": spec_s["name"], "hold": None}]
            for a in s["actions"][:-1]:
                if a["hold"] is None:
                    a["hold"] = 0.0
            s["actions"][-1]["hold"] = float(spec_s.get("hold", 1.0))
            s["actions"][-1]["hold_kind"] = spec_s.get("hold_kind", "step")
            for a in s["actions"]:
                if a["hold"] == 0.0:
                    a["hold"] = 1.0
                    a["hold_kind"] = "step"
        with open(os.path.join(rd, "fake-video-plan.json"), "w") as f:
            json.dump({"steps": steps, "defects": defects, "landing_hold": float(spec["start"].get("hold", 1.2))}, f,
                      indent=1)
        if sc.get("video") == "synth":
            sys.path.insert(0, HERE)
            import synth_video
            synth_video.build_take(rd, steps, landing_hold=float(spec["start"].get("hold", 1.2)), defects=defects)
        else:
            man = {"run_id": req["run_id"], "raw": "raw.mp4", "fake": True, "defects": defects,
                   "marks": [{"name": s["name"], "step": i + 1} for i, s in enumerate(steps)]}
            with open(os.path.join(rd, "manifest.json"), "w") as f:
                json.dump(man, f, indent=1)
    with open(os.path.join(rd, "result.json"), "w") as f:
        json.dump(res, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
