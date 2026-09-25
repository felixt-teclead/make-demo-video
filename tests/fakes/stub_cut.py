"""STUB cutter for fast loop tests (the real one is M4 bin/vc-cut). Writes clips/index.json and cut/record.json in
M4's documented shape from a fake manifest. Usage: stub_cut.py RUN_DIR [--speed F]"""
import json
import os
import sys
import time

rd = sys.argv[1]
t0 = time.time()
man = json.load(open(os.path.join(rd, "manifest.json")))
os.makedirs(os.path.join(rd, "clips"), exist_ok=True)
os.makedirs(os.path.join(rd, "cut"), exist_ok=True)
clips = [{"index": i, "step": m["step"], "name": m["name"], "file": f"clips/{i + 1:02d}-{m['name']}.mp4",
          "duration": 12.0 if i == len(man["marks"]) - 1 else 3.5, "frames": 0, "holds": [], "events": []}
         for i, m in enumerate(man["marks"])]
rec = {"run_id": man["run_id"], "clips": clips, "timing": {"total_s": round(time.time() - t0, 3), "join_s": 0.01},
       "stub": True}
json.dump(rec, open(os.path.join(rd, "cut", "record.json"), "w"), indent=1)
json.dump({"run_id": man["run_id"], "clips": clips, "full": "full.mp4"}, open(os.path.join(rd, "clips", "index.json"), "w"), indent=1)
