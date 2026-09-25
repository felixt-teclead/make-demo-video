"""STUB gate for fast loop tests (the real one is M6a bin/vc-gate). Same exit codes and report files.
FAILs Q-20 on the clip of a planted `black` defect, Q-44 on `no_ripple`; else PASS. Usage: stub_gate.py RUN_DIR"""
import json
import os
import sys

rd = sys.argv[1]
man = json.load(open(os.path.join(rd, "manifest.json")))
viol = []
for kind, step in (man.get("defects") or {}).items():
    check = {"black": "Q-20", "no_ripple": "Q-44"}.get(kind, "Q-20")
    viol.append({"check": check, "severity": "FAIL", "clip": step, "t0": 1.2, "t1": 2.7,
                 "reason": "solid BLACK frames for 1.50 s (limit 0.10 s)" if kind == "black" else "click without a ripple"})
rep = {"mode": "cut", "stub": True, "violations": viol, "warnings": [], "verdict": "FAIL" if viol else "PASS"}
os.makedirs(os.path.join(rd, "qa"), exist_ok=True)
json.dump(rep, open(os.path.join(rd, "qa", "report.json"), "w"), indent=1)
txt = "\n".join([f"  FAIL {v['check']} {v['clip']} {v['t0']:.2f} s: {v['reason']}" for v in viol] +
                [("PASS" if not viol else f"FAIL: {len(viol)} violations")])
open(os.path.join(rd, "qa", "report.txt"), "w").write("STUB gate\n" + txt + "\n")
sys.exit(1 if viol else 0)
