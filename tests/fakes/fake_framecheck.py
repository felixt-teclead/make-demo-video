"""FAKE viewer-review model: no findings unless the scenario says {"framecheck_fail_take": k} for this take's run id
(then one blocker at the last step's end), plus one minor finding when {"review_minor": true}."""
import json
import os
import sys

req = json.load(open(sys.argv[1]))
sc = json.load(open(os.environ["VC_FAKE_SCENARIO"])) if os.environ.get("VC_FAKE_SCENARIO") else {}
k = sc.get("framecheck_fail_take")
bad = k is not None and req["run_dir"].rstrip("/").endswith(f"-t{k}")
steps = req.get("steps") or []
last = steps[-1]["step"] if steps else None
findings = []
if bad:
    findings.append({"t": steps[-1]["end"] if steps else 0.0, "step": last, "region": "centre", "box": [760, 400, 400, 280],
                     "category": "expected_missing", "severity": "blocker", "what": "end frame lacks the expected content"})
if sc.get("review_minor"):
    findings.append({"t": 1.0, "step": steps[0]["step"] if steps else None, "region": "around the first click",
                     "category": "flicker_hole", "severity": "minor", "what": "small patch around the ripple"})
out = {"steps": [{"step": s["step"], "expected_visible": True, "note": ""} for s in steps], "findings": findings,
       "model": "fake/" + req.get("model", "?"), "tokens": {"input": 0, "output": 0}, "seconds": 0.0,
       "sheets_seen": len(req.get("sheets") or []), "sample_error": req.get("sample_error")}
json.dump(out, open(req["out"], "w"), indent=1)
