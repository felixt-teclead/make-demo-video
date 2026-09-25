"""vc-gate-fixtures: the Q-90 fixture suite as one command.

For each case in gate/fixtures/cases.json it builds a take directory (symlinks to the fixture clips, clips/index.json,
the case's event log and cut record), runs the gate CLI on it as a subprocess, and compares the result with the
expected verdict and check. Prints a per-fixture table and a confusion table. Exit 0 when every case of the selected
stage behaves as expected.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CASES = os.path.join(REPO, "gate", "fixtures", "cases.json")
VERDICTS = ["FAIL", "WARN", "PASS", "ABORT"]


def default_fixture_dir():
    env = os.environ.get("VC_FIXTURES")
    if env:
        return env
    for cand in (os.path.join(REPO, "..", "vc-v1-spec", "acceptance", "fixtures"),
                 os.path.join(REPO, "acceptance", "fixtures")):
        if os.path.isdir(cand):
            return os.path.abspath(cand)
    return None


def build_take(case, fixdir, work):
    if case.get("take_dir"):
        src = os.path.join(fixdir, case["take_dir"])
        take = os.path.join(work, case["id"])
        shutil.copytree(src, take)
    else:
        take = os.path.join(work, case["id"])
        os.makedirs(os.path.join(take, "clips"))
        clips = []
        for i, rel in enumerate(case["clips"]):
            name = os.path.splitext(os.path.basename(rel))[0]
            dst = os.path.join(take, "clips", f"{i + 1:02d}-{name}.mp4")
            os.symlink(os.path.join(fixdir, rel), dst)
            clips.append({"index": i, "step": i + 1, "name": name, "file": os.path.relpath(dst, take), "holds": []})
        with open(os.path.join(take, "clips", "index.json"), "w") as fh:
            json.dump({"run_id": case["id"], "clips": clips}, fh, indent=1)
    if case.get("events") is not None:
        os.makedirs(os.path.join(take, "clips"), exist_ok=True)
        with open(os.path.join(take, "clips", "events.json"), "w") as fh:
            json.dump(case["events"], fh, indent=1)
    if case.get("record"):
        os.makedirs(os.path.join(take, "cut"), exist_ok=True)
        with open(os.path.join(take, "cut", "record.json"), "w") as fh:
            json.dump(case["record"], fh, indent=1)
    return take


def judge(case, status, res, stdout):
    """Returns (got_verdict, fired_checks, ok, why)."""
    exp = case["expect"]
    if status == 3:
        got = "ABORT"
        fired = []
        named = exp.get("clip", "") in stdout
        count_printed = "violation" in stdout.lower()
        ok = exp["verdict"] == "ABORT" and named and not count_printed
        why = "" if ok else f"abort: clip named={named}, count printed={count_printed}"
        return got, fired, ok, why
    if res is None:
        return "ERROR", [], False, f"gate exit {status}"
    fails = res["violations"]
    warns = res["warnings"]
    fired = sorted({v["check"] for v in fails})
    warned = sorted({v["check"] for v in warns})
    if fails:
        got = "FAIL"
    elif warns:
        got = "WARN"
    else:
        got = "PASS"
    want = exp["verdict"]
    why = ""
    if want == "FAIL":
        hits = [v for v in fails if v["check"] == exp["check"]]
        ok = got == "FAIL" and bool(hits)
        if ok and exp.get("detail"):
            ok = any(exp["detail"].lower() in v["reason"].lower() for v in hits)
            why = "" if ok else f"{exp['check']} fired but not '{exp['detail']}'"
        if ok and exp.get("count"):
            ok = len(hits) == exp["count"]
            why = "" if ok else f"{exp['check']} fired {len(hits)}x, expected {exp['count']}"
        for extra in exp.get("also", []):
            if ok and extra not in fired:
                why = f"{extra} not fired (optional)"
        if not ok and not why:
            why = f"expected {exp['check']} to FAIL"
    elif want == "WARN":
        ok = got in ("WARN",) and exp["check"] in warned
        why = "" if ok else f"expected a {exp['check']} WARN with no FAIL"
    elif want == "PASS":
        ok = got in ("PASS", "WARN") and len(warns) <= exp.get("max_warn", 0)
        why = "" if ok else "expected PASS"
        if ok and got == "WARN":
            got = "PASS"   # a take with warnings still passes (Q-07)
    else:
        ok = False
        why = "expected ABORT"
    return got, fired + [w + "(W)" for w in warned], ok, why


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vc-gate-fixtures")
    ap.add_argument("--fixtures", default=default_fixture_dir())
    ap.add_argument("--stage", default="all", choices=["S1", "S2", "all"],
                    help="all (default, S2 built): score every case; S1: the S1 suite only (q23-* listed, not scored)")
    ap.add_argument("--only", nargs="*", help="case ids to run")
    ap.add_argument("--keep", action="store_true", help="keep the generated take dirs and reports")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every gate report")
    a = ap.parse_args(argv)
    if not a.fixtures or not os.path.isdir(a.fixtures):
        print("SKIP vc-gate-fixtures: fixture dir not found. The Q-90 fixture clips are recordings of a private app "
              "and not in the public repo; set VC_FIXTURES (or --fixtures) to their directory to run the suite")
        return 0
    with open(CASES) as fh:
        cases = json.load(fh)["cases"]
    if a.only:
        cases = [c for c in cases if c["id"] in a.only]
    gate = os.path.join(REPO, "bin", "vc-gate")
    work = tempfile.mkdtemp(prefix="vc-gate-fixtures-")
    rows = []
    t_all = time.time()
    for case in cases:
        scored = a.stage == "all" or case["stage"] == "S1" or a.stage == case["stage"]
        take = build_take(case, a.fixtures, work)
        out = os.path.join(take, "qa")
        cmd = [gate, take, "--json", "--out", out]
        if case.get("events") is None:
            cmd.append("--no-events")
        t0 = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True)
        dt = time.time() - t0
        res = None
        if p.returncode in (0, 1):
            try:
                res = json.loads(p.stdout)
            except ValueError:
                res = None
        text = open(os.path.join(out, "report.txt")).read() if os.path.exists(os.path.join(out, "report.txt")) else p.stdout
        got, fired, ok, why = judge(case, p.returncode, res, p.stdout + text)
        rows.append({"id": case["id"], "stage": case["stage"], "scored": scored, "want": case["expect"]["verdict"],
                     "check": case["expect"].get("check", ""), "got": got, "fired": fired, "ok": ok, "why": why,
                     "time": dt})
        if a.verbose:
            print(f"===== {case['id']} (exit {p.returncode})")
            print(text.rstrip())
            if p.stderr.strip():
                print(p.stderr.rstrip())

    w = max(len(r["id"]) for r in rows) if rows else 10
    print(f"{'fixture':<{w}}  stage  expected         got    fired checks                          time  result")
    for r in rows:
        exp = r["want"] + (" " + r["check"] if r["check"] else "")
        res = ("ok" if r["ok"] else "MISMATCH " + r["why"]) if r["scored"] else \
            ("(not scored: " + r["stage"] + ") " + ("ok" if r["ok"] else r["why"]))
        print(f"{r['id']:<{w}}  {r['stage']:<5}  {exp:<15}  {r['got']:<5}  {', '.join(r['fired']) or '-':<38}"
              f"{r['time']:5.1f}s  {res}")

    scored = [r for r in rows if r["scored"]]
    print("\nconfusion table (scored cases; rows = expected, columns = gate verdict; "
          "FAIL counts only when the expected check fired):")
    cols = VERDICTS + ["other"]
    print(f"{'expected':<10}" + "".join(f"{c:>8}" for c in cols))
    for want in VERDICTS:
        cells = []
        for col in cols:
            n = 0
            for r in scored:
                if r["want"] != want:
                    continue
                g = r["got"]
                if want == "FAIL" and g == "FAIL" and not r["ok"]:
                    g = "other"   # failed, but on another check
                if (g if g in VERDICTS else "other") == col:
                    n += 1
            cells.append(n)
        if sum(cells):
            print(f"{want:<10}" + "".join(f"{n:>8}" for n in cells))
    bad = [r for r in scored if not r["ok"]]
    print(f"\n{len(scored) - len(bad)}/{len(scored)} scored fixtures as expected; total {time.time() - t_all:.1f} s")
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print("take dirs kept in", work)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
