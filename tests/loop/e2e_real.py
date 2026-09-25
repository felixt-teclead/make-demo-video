"""End to end with a SYNTHETIC video (tests/fakes/synth_video.py, fake jev + fake recorder) and the REAL cutter (M4)
and REAL gate (M6a). Scenarios (a)-(e) of the milestone. Run: taskset -c 8-15 python3 tests/loop/e2e_real.py [a b c d e]
Prints one summary block per scenario and one EXPECT line per scenario; exit status = number of unmet expectations.
Workspaces are removed unless --keep."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import Workspace, sibling  # noqa: E402

C1 = "tests/loop/golden-frozen/c1-four-angles.toml"
KEEP = "--keep" in sys.argv
FAILED = []


def expect(tag, ok, what):
    print(f"EXPECT ({tag}) {'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        FAILED.append(tag)


def verdicts(w, job="job"):
    return [r["verdict"] for r in w.take_log(job)]


def show(tag, w, rc, t0, job="job"):
    st = w.state(job)
    print(f"\n===== ({tag}) exit {rc}, {time.time() - t0:.0f} s wall")
    print(f"status {st['status']}; takes {st['takes_used']} of {st['knobs']['max_takes']}; dry runs "
          f"{st['dry_runs_used']}; stop {st['stop'] and (st['stop']['point'], st['stop']['needed'][:140])}")
    print("phases: " + " > ".join(st["phase_seq"]))
    for r in w.take_log(job):
        print(f"  take {r['take']}: gate {r['verdict']} ({r['violations']} viol, {r['warnings']} warn) "
              f"first={r['first_violations'][:1]} fc={r['frame_check']} len={r['length']} change={r['change']} "
              f"-> {r['decision']}")
    rep = w.report(job)
    for line in rep.splitlines():
        if line.startswith(("video:", "verdict:", "takes used", "jev:", "STOP", "needed:", "next fix", "question:")):
            print("  | " + line)


def scen(tag):
    t0 = time.time()
    if tag == "a":
        w = Workspace(C1, {"video": "synth"}, components="real-cut-gate", keep=KEEP)
        w.approve()
        rc, out = w.run()
    elif tag == "b":
        w = Workspace(C1, {"video": "synth", "defect": {"step": "bpmn", "kind": "black", "fixable": True}},
                      components="real-cut-gate", keep=KEEP)
        w.approve()
        rc, out = w.run()
    elif tag == "c":
        w = Workspace(C1, {"video": "synth", "defect": {"step": "bpmn", "kind": "black", "fixable": False}},
                      components="real-cut-gate", keep=KEEP)
        w.approve()
        rc, out = w.run("max_takes=2")
    elif tag == "d":
        w = Workspace(C1, {"video": "synth", "login": "expired"}, components="real-cut-gate", keep=KEEP)
        w.approve(unattended_instruction="Film golden case 1 on the example app, read-only, defaults; no human reachable.")
        rc, out = w.run("human_reachable=no")
    elif tag == "e":
        w = Workspace(C1, {"video": "synth", "defect": {"step": "bpmn", "kind": "black"}, "fixer": "rogue"},
                      components="real-cut-gate", keep=KEEP)
        w.approve()
        t = open(w.spec).read()
        open(w.spec, "w").write(t.replace('value = "BPMN exportieren"', 'value = "BPMN"'))
        rc1, out1 = w.run(job="edited")
        print(f"\n===== (e1) edited expected state after approval: exit {rc1}: {out1.strip().splitlines()[-1][:200]}")
        expect("e1", rc1 == 2 and "contract changed" in out1, "a spec edited after approval is refused (exit 2, F-06)")
        rc, dout = w.cli("vc-spec", "diff", w.spec)
        print("  | " + "\n  | ".join(ln for ln in dout.splitlines() if "CONTRACT" in ln or ln.startswith("contract")))
        open(w.spec, "w").write(t)                                 # back to the approved text
        rc, out = w.run("human_reachable=no")
        show("e2 rogue fixer drops an expected state", w, rc, t0)
        st = w.state()
        expect("e2", rc == 21 and st["stop"] and st["stop"]["point"] == 1,
               "a fix that drops an expected state is reverted and ends at stop 1")
        w.cleanup()
        return
    show(tag, w, rc, t0)
    st, v = w.state(), verdicts(w)
    if tag == "a":
        expect("a", rc == 0 and st["status"] == "done" and v == ["PASS"], "clean take: hit on take 1, gate PASS")
    elif tag == "b":
        expect("b", rc == 0 and st["status"] == "done" and v == ["FAIL", "PASS"],
               "planted black frames: gate FAIL, one fix, hit on take 2")
    elif tag == "c":
        expect("c", rc == 20 and st["stop"] and st["stop"]["point"] == 3 and v == ["FAIL", "FAIL"]
               and "NOT-A-HIT" in w.report(), "unfixable defect: cap of 2 reached, NOT A HIT delivered, stop 3")
    elif tag == "d":
        expect("d", rc == 21 and st["stop"] and st["stop"]["point"] == 2 and st["takes_used"] == 0,
               "unattended + expired login: stop 2 before any take")
    w.cleanup()


if __name__ == "__main__":
    assert sibling("cut") and sibling("gate"), "needs the cutter and the gate"
    for tag in [a for a in sys.argv[1:] if not a.startswith("-")] or ["a", "b", "c", "d", "e"]:
        scen(tag)
    print(f"\ne2e: {'all expectations met' if not FAILED else 'UNMET: ' + ' '.join(FAILED)}")
    sys.exit(len(FAILED))
