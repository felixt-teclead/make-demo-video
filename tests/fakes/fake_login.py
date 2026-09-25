"""FAKE login check with M1's exit codes: 0 logged in, 20 human needed, 21 no credentials configured."""
import json
import os
import sys

sc = json.load(open(os.environ["VC_FAKE_SCENARIO"])) if os.environ.get("VC_FAKE_SCENARIO") else {}
sys.exit({"ok": 0, "human": 20, "expired": 21}.get(sc.get("login", "ok"), 0))
