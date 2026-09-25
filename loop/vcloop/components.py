"""The components the loop drives, as commands from one JSON config (so real parts and stubs swap without a code
change). Placeholders in a command: {run_dir} {request} {request_c} {events} {root} {speedup_factor} and any knob.

  "lock":        command prefix for every browser phase, e.g. ["{root}/bin/vc-lock", "{label}"] (M1, C-18);
                 null = the loop's own flock on $VC_STATE_DIR/browser.lock with the lock_timeout_s knob
  "self_locking": components whose command takes the browser lock itself, INSIDE the container (e.g. "run" =
                 vc-env exec ... /opt/vc/bin/vc-lock {label} python3 -m vcloop.take_runner ...): the lock then lives
                 exactly as long as the process that uses the browser, not as long as the host's docker exec client
                 (C-18). The loop adds no lock of its own for them.
  "login_check": exit 0 logged in, 20 human needed, 21 no credentials, 22 recorder busy (M1 section 9)
  "run":         dry run / take / cleanup; reads {request}, writes <run_dir>/result.json (docs/loop.md)
  "cut":         M4 bin/vc-cut
  "gate":        M6a bin/vc-gate (exit 0 PASS, 1 FAIL, 3 ABORT, 2 usage)
  "review":      the viewer review (old key "framecheck"): reads {request} (<run_dir>/review/request.json), writes
                 the request's `out` (<run_dir>/review/result.json); or "handoff" (docs/steps/review.md)
  "fixer":       reads {request}, EDITS ONLY, writes the fix note to the request's `out`; or "handoff"
"handoff" = the loop saves the request, exits with status "handoff" (exit 10) and continues on `vc-loop resume`
once the orchestrator has run the model (the plugin's viewer review / fixer subagent) and written the result file.
"""
import fcntl
import json
import os
import subprocess
import time

from .profiles import ROOT


class ComponentError(RuntimeError):
    pass


class LockTimeout(ComponentError):
    pass


LOCK_TIMEOUT_RC = 75       # bin/vc-lock's exit status when the browser lock stays busy (docs/interfaces.md section 6)


def load(path):
    with open(path) as f:
        cfg = json.load(f)
    return cfg


def _fill(cmd, values):
    out = []
    for part in cmd:
        s = str(part)
        for k, v in values.items():
            s = s.replace("{" + k + "}", str(v))
        out.append(s)
    return out


class Components:
    def __init__(self, cfg, knobs, state_dir, log_dir):
        self.cfg, self.knobs, self.state_dir, self.log_dir = cfg, knobs, state_dir, log_dir
        os.makedirs(state_dir, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)

    def is_handoff(self, name):
        return self.cfg.get(name) == "handoff"

    def values(self, extra):
        v = {"root": ROOT}
        v.update({k: val for k, val in self.knobs.items()})
        v.update(extra)
        return v

    def _run(self, name, cmd, label, timeout=None):
        logf = os.path.join(self.log_dir, f"{label}.{name}.log")
        env = dict(os.environ)
        env["VC_LOCK_TIMEOUT"] = str(self.knobs["lock_timeout_s"])
        with open(logf, "ab") as lf:
            p = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env, timeout=timeout)
        return p.returncode, logf

    def call(self, name, extra, *, label, browser=False, timeout=None):
        """Run component `name`. Browser components hold the one browser lock (C-18)."""
        cmd = self.cfg.get(name)
        if not cmd or cmd == "handoff":
            raise ComponentError(f"component {name!r} is not configured as a command")
        cmd = _fill(cmd, self.values(dict(extra, label=label)))
        if browser and name not in (self.cfg.get("self_locking") or []):
            lock = self.cfg.get("lock")
            if lock:
                return self._run(name, _fill(lock, self.values(dict(extra, label=label))) + cmd, label, timeout)
            try:
                with self._flock(label):
                    return self._run(name, cmd, label, timeout)
            except LockTimeout as e:           # the same answer as vc-lock: exit 75
                logf = os.path.join(self.log_dir, f"{label}.{name}.log")
                with open(logf, "a") as f:
                    f.write(f"loop lock: {e}\n")
                return LOCK_TIMEOUT_RC, logf
        return self._run(name, cmd, label, timeout)

    # the loop's own lock when M1's vc-lock is not configured (same file, same log format)
    class _Lock:
        def __init__(self, outer, label):
            self.o, self.label = outer, label

        def _log(self, event):
            with open(os.path.join(self.o.state_dir, "lock.log"), "a") as f:
                f.write(json.dumps({"t": time.time(), "event": event, "label": self.label, "pid": os.getpid(),
                                    "host": os.uname().nodename}) + "\n")

        def __enter__(self):
            self.fh = open(os.path.join(self.o.state_dir, "browser.lock"), "a")
            self._log("wait")
            deadline = time.time() + float(self.o.knobs["lock_timeout_s"])
            while True:
                try:
                    fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.time() > deadline:
                        self._log("timeout")
                        raise LockTimeout(f"browser lock not free after {self.o.knobs['lock_timeout_s']} s")
                    time.sleep(0.2)
            self._log("acquired")
            return self

        def __exit__(self, *exc):
            self._log("released")
            fcntl.flock(self.fh, fcntl.LOCK_UN)
            self.fh.close()

    def _flock(self, label):
        return Components._Lock(self, label)
