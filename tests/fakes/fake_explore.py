"""FAKE exploration record (stands in for D-10 while the browser is blocked). Builds, for every jev action of a spec,
an observation of 'the view where the action starts' with the target control plus realistic distractors (write
controls from the app profile, twins, tabs). Marked "synthetic": true. Usage: fake_explore.py SPEC [OUT_DIR]"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "loop"))
from vcloop import profiles as P, spec as S  # noqa: E402


def own_label(target, params):
    if "label" in target:
        return target["label"]
    part = target.get("label_contains", "")
    for v in params.values():                      # the full visible label is usually a parameter value
        if isinstance(v, str) and part and part in v:
            return v
    return part


def build(spec_path, out_dir=None):
    sp = S.load(spec_path)
    res, _ = S.resolve(sp, {"unique": f"{sp['name']}-explore", "item": "x"})
    profs = P.for_spec(res)
    distract = [w for p in profs for w in p.get("deny_extra", [])] + ["Löschen", "Export Process", "Details",
                                                                      "Metadaten", "Vertikal"]
    obs = []
    params = {k: v for k, v in res.get("params", {}).items()}
    for s in S.setup_steps(res) + res["steps"]:
        for i, a in enumerate(s.get("actions", [])):
            if a["op"] not in ("click", "type", "select"):
                continue
            t = a["control"]
            kind = {"click": "click", "type": "fill", "select": "select"}[a["op"]]
            ctx = [t["within"] + " x"] if t.get("within") else ["main"]
            acts = [{"kind": kind, "role": t.get("role", "searchbox" if kind == "fill" else "button"),
                     "label": own_label(t, params), "own_label": own_label(t, params), "context": ctx, "node": 1,
                     "on_screen": True}]
            for j, d in enumerate(distract):
                if d.casefold() != acts[0]["own_label"].casefold():
                    acts.append({"kind": "click", "role": "tab" if d in ("Details", "Metadaten") else "button",
                                 "label": d, "own_label": d, "context": ["main"], "node": 100 + j, "on_screen": True})
            acts.append({"kind": "wait", "id": "wait", "label": "Wait"})
            obs.append({"step": s["name"], "action": i, "url": res["start"]["url"], "actions": acts})
    out_dir = out_dir or S.explore_dir_for(sp)
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "20260924-000000-synthetic.json")
    json.dump({"spec": sp["name"], "synthetic": True,
               "note": "FAKE exploration (browser blocked); real records come from D-10 exploration with jev",
               "observations": obs}, open(p, "w"), indent=1, ensure_ascii=False)
    return p


if __name__ == "__main__":
    print(build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
