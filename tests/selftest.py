"""bin/vc-selftest: every test suite of the repo that runs without a browser, one command, one summary.

    bin/vc-selftest [--quick] [--only NAME ...] [--list] [--keep]

--quick skips the slow media suites (cutter verify, gate fixtures, loop end to end). Exit status: number of suites
that did not pass (failed or skipped because a tool is missing; a skipped suite is never counted as passed). Work files go to .verify/selftest (git-ignored). Media suites run pinned to the settings file's VC_CPUSET
(default 8-15) with ffmpeg from PATH or ~/.local/bin. Nothing here starts a container or a browser.
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
WORK = os.path.join(ROOT, ".verify", "selftest")
VENV_PY = os.path.join(ROOT, ".venv", "bin", "python")


def cpuset():
    v = "8-15"
    for f in (os.environ.get("VC_SETTINGS"), os.path.join(ROOT, "settings.env"), os.path.join(ROOT, "settings.example.env")):
        if f and os.path.exists(f):
            for line in open(f):
                if line.startswith("VC_CPUSET="):
                    return line.strip().split("=", 1)[1] or v
    return v


def pin(cmd):
    return (["taskset", "-c", cpuset()] if shutil.which("taskset") else []) + cmd


PY = sys.executable
# name, slow, cwd, command, extra env
SUITES = [
    ("jev-unit", False, "jev", [PY, "-m", "unittest", "discover", "-s", "tests"], {"PYTHONPATH": ".:tests"}),
    ("overlay-unit", False, ".", [PY, "-m", "unittest", "tests.test_overlay", "tests.test_viewstart"], {}),
    ("env", False, ".", [PY, "-m", "unittest", "tests.env.test_lock", "tests.env.test_chrome_launch",
                                     "tests.env.test_recorder_unit"], {}),
    ("loop-unit", False, "tests/loop", [PY, "-m", "unittest"], {}),
    ("review-unit", False, "tests/review", [PY, "-m", "unittest", "test_review"], {}),
    ("integration", False, ".", ["@venv", "-m", "unittest", "discover", "-s", "tests/integration", "-t", "."], {}),
    ("fixture-page", False, ".", [PY, "-m", "unittest", "discover", "-s", "tests/fixture", "-t", "."], {}),
    ("plugin", False, ".", [PY, "-m", "unittest", "discover", "-s", "tests/plugin", "-t", "."], {}),
    ("plugin-validate", False, ".", ["@claude", "plugin", "validate", "."], {}),
    ("gate-unit", True, "gate/tests", ["@venv", "-m", "unittest", "test_gate", "test_intfix"],   # + review fixes
     {"PYTHONPATH": ".."}),
    ("gate-s2", True, "gate/tests", ["@venv", "-m", "unittest", "test_s2"], {"PYTHONPATH": ".."}),   # Q-23, Q-03
    ("gate-fixtures", True, ".", ["bin/vc-gate-fixtures"], {}),
    ("cut-verify", True, ".", [PY, "tests/cut/verify.py", "all", os.path.join(WORK, "cut")], {}),
    ("loop-e2e", True, ".", [PY, "tests/loop/e2e_real.py"], {"VC_TEST_TMP": os.path.join(WORK, "e2e")}),
]


def resolve(cmd):
    if cmd[0] == "@venv":
        return [VENV_PY] + cmd[1:]
    if cmd[0] == "@claude":
        c = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
        return [c] + cmd[1:] if os.path.exists(c) else None
    return cmd


def main(argv):
    if "--list" in argv:
        for s in SUITES:
            print(f"{s[0]:16} {'slow' if s[1] else 'quick'}")
        return 0
    only = [a for a in argv if not a.startswith("-")]
    quick = "--quick" in argv
    os.makedirs(WORK, exist_ok=True)
    env0 = dict(os.environ)
    env0["PATH"] = os.path.expanduser("~/.local/bin") + os.pathsep + env0.get("PATH", "")
    if not os.path.exists(VENV_PY) or subprocess.run([PY, os.path.join(ROOT, "tests", "tools", "host_venv.py"),
                                                      "--check"]).returncode:
        print("selftest: creating .venv with numpy (tests/tools/host_venv.py)")
        subprocess.run([PY, os.path.join(ROOT, "tests", "tools", "host_venv.py")], check=True)
    rows, failed, skipped = [], [], []
    for name, slow, cwd, cmd, extra in SUITES:
        if (only and name not in only) or (quick and slow and not only):
            continue
        c = resolve(cmd)
        if c is None:
            rows.append((name, "SKIP", 0.0, "not available on this host"))
            skipped.append(name)
            print(f"SKIP {name:16}    0.0 s  not available on this host (counts as not passed)", flush=True)
            continue
        if slow:
            c = pin(c)
        env = dict(env0, **extra)
        if "VC_TEST_TMP" in extra:
            os.makedirs(extra["VC_TEST_TMP"], exist_ok=True)
        t = time.time()
        log = os.path.join(WORK, f"{name}.log")
        with open(log, "w") as fh:
            p = subprocess.run(c, cwd=os.path.join(ROOT, cwd), env=env, stdout=fh, stderr=subprocess.STDOUT)
        dt = time.time() - t
        tail = [ln.strip() for ln in open(log, errors="replace") if ln.strip()]
        summary = next((ln for ln in reversed(tail) if ln.startswith(("Ran ", "OK", "FAILED", "e2e:", "Validation"))
                        or "passed" in ln or "scored fixtures" in ln), tail[-1] if tail else "")
        ran = next((ln for ln in reversed(tail) if ln.startswith("Ran ")), "")
        status = "PASS" if p.returncode == 0 else "FAIL"
        if status == "FAIL":
            failed.append(name)
        rows.append((name, status, dt, (ran + " " + summary).strip() if ran and ran != summary else summary))
        print(f"{status:4} {name:16} {dt:6.1f} s  {rows[-1][3][:110]}", flush=True)
    print("\nselftest summary")
    for name, st, dt, msg in rows:
        print(f"  {st:4} {name:16} {dt:6.1f} s  {msg[:110]}")
    print(f"{len(rows) - len(failed) - len(skipped)} of {len(rows)} suites passed"
          + (f"; FAILED: {' '.join(failed)}" if failed else "")
          + (f"; SKIPPED (not passed): {' '.join(skipped)}" if skipped else "")
          + f" (logs: {os.path.relpath(WORK, ROOT)}/)")
    if "--keep" not in argv:
        for d in ("cut", "e2e"):
            shutil.rmtree(os.path.join(WORK, d), ignore_errors=True)
    return len(failed) + len(skipped)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
