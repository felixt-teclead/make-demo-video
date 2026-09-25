"""The deterministic retake loop (F-11, F-14): a script, not agent memory.

It owns the take counter, the caps, the phase order (login check -> dry run -> take -> cut -> QA -> viewer review ->
judge -> fix -> full dry run -> take ...), the take log, the timing log, the choice of best take, the stop points and
delivery. All state is on disk (job_dir/state.json, written atomically after every phase), so a restarted session
continues where it stopped and never restarts the count. Models only supply the viewer review and the fix; the fixer
only edits files, and this script starts every retake.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import components as C
from . import gatefeed
from . import knobs as K
from . import ledger as LG
from . import logs as L
from . import profiles as P
from . import review as RV
from . import spec as S

EXIT = {"done": 0, "handoff": 10, "waiting": 20, "ended": 21, "refused": 2}

STOP_POINTS = {
    1: ("spec approval / re-approval after a change of substance", ["approved (after vc-spec approve)", "abort"]),
    2: ("login needs the human", ["logged-in (after the login over the remote view)", "abort"]),
    3: ("take cap reached without a hit", ["raise-cap=N", "change-spec", "accept"]),
    4: ("a write was requested that is not approved", ["approved (after vc-spec approve --approve-write)", "abort"]),
    5: ("a mitigation outside the D-33 list, or one that needs owner approval", ["approved", "abort"]),
    6: ("dry-run cap reached (D-41)", ["raise-dry-cap=N", "change-spec", "abort"]),
    7: ("a fixer stop outside its scope (QA definition, app bug, recorder down, precondition)", ["continue", "abort"]),
    8: ("a proposal to promote a fix (F-35)", ["accept", "reject"]),
    9: ("cleanup without a clear, safe delete path", ["leave"]),
}
FIX_STOP_REASONS = {"login": 2, "write": 4, "mitigation": 5, "qa": 7, "app_bug": 7, "recorder_down": 7, "substance": 1}


def runs_dir():
    return os.path.abspath(os.environ.get("VC_RUNS_DIR") or os.path.join(P.ROOT, "runs"))


def state_dir():
    return os.path.abspath(os.environ.get("VC_STATE_DIR") or os.path.join(P.ROOT, "state"))


def _sha_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _ffmpeg():
    for p in (os.environ.get("VC_FFMPEG"), os.path.expanduser("~/.local/bin/ffmpeg"), shutil.which("ffmpeg")):
        if p and os.path.exists(p):
            return p
    return None


class Refused(RuntimeError):
    pass


class Stopped(Exception):
    pass


class Job:
    # ------------------------------------------------------------------------------------------- setup / state
    def __init__(self, job_dir):
        self.dir = os.path.abspath(job_dir)
        with open(os.path.join(self.dir, "state.json")) as f:
            self.st = json.load(f)
        self.cfg = C.load(os.path.join(self.dir, "components.json"))
        self.timing = L.Timing(os.path.join(self.dir, "timing.jsonl"), self.st["job"])
        self.take_log = os.path.join(self.dir, "take-log.jsonl")
        self.comp = C.Components(self.cfg, self.st["knobs"], state_dir(), os.path.join(self.dir, "logs"))
        self.out = []

    @classmethod
    def create(cls, spec_path, components_path, run_knobs=None, job_id=None, instruction_path=None):
        spec = S.load(spec_path)
        kv, src = K.resolve(spec.get("knobs"), run_knobs)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        job = job_id or f"{spec['name']}-{stamp}"
        jdir = os.path.join(runs_dir(), "jobs", job)
        if os.path.exists(os.path.join(jdir, "state.json")):
            raise Refused(f"job {job} exists: use `vc-loop resume {jdir}`")
        os.makedirs(jdir, exist_ok=True)
        shutil.copyfile(components_path, os.path.join(jdir, "components.json"))
        st = {"job": job, "spec_path": os.path.abspath(spec_path), "spec_name": spec["name"], "created": _now_iso(),
              "status": "running", "next": "preflight", "knobs": kv, "knob_sources": src,
              "approved_fingerprint": None, "contract_fingerprint": None, "takes_used": 0, "dry_runs_used": 0,
              "need_dry": True, "dry_ok_hash": None, "flakes_used": 0, "pending_changes": [], "last_verdict": None,
              "stop": None, "stops": [], "fix_count": 0, "fix_source": None, "created_items": [],
              "in_progress": None, "phase_seq": [], "hit_take": None, "delivery": None, "cleanup": None,
              "answers": [], "current": None, "instruction": instruction_path}
        with open(os.path.join(jdir, "state.json"), "w") as f:
            json.dump(st, f, indent=1)
        return cls(jdir)

    def save(self):
        tmp = os.path.join(self.dir, "state.json.tmp")
        with open(tmp, "w") as f:
            json.dump(self.st, f, indent=1, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(self.dir, "state.json"))

    def say(self, msg):
        self.out.append(msg)
        print(msg, flush=True)

    @property
    def kv(self):
        return self.st["knobs"]

    @property
    def attended(self):
        return bool(self.kv["human_reachable"])

    def spec(self):
        return S.load(self.st["spec_path"])

    def run_dir(self, run_id):
        return os.path.join(runs_dir(), run_id)

    def _fresh_run_id(self, base):
        """A run folder is used once (Q-58): a take number that did not count (a login or lock stop) may come back,
        and its folder may already hold that attempt's files, so the retry gets a suffix."""
        rid, i = base, 1
        while os.path.isdir(self.run_dir(rid)) and set(os.listdir(self.run_dir(rid))) - {"request.json"}:
            i += 1
            rid = f"{base}-r{i}"
        return rid

    # --------------------------------------------------------------------------------------------- main loop
    def run(self):
        if self.st["status"] == "running" and self.st.get("in_progress"):
            self._recover_interrupted()
        while self.st["status"] == "running":
            phase = self.st["next"]
            self.st["phase_seq"].append(phase)
            try:
                getattr(self, "ph_" + phase)()
            except Stopped:
                pass
            except Refused as e:
                self.st["status"] = "refused"
                self.st["refused"] = str(e)
                self.save()
                self.say(f"REFUSED: {e}")
                return EXIT["refused"]
            self.save()
            crash = os.environ.get("VC_LOOP_CRASH_AFTER")      # test hook: simulate a killed session (F-14)
            if crash and crash == f"{phase}:{self.st['takes_used']}":
                os._exit(137)
        return EXIT.get(self.st["status"], 1)

    def _recover_interrupted(self):
        ip = self.st["in_progress"]
        if ip.get("phase") == "take":
            row = {"take": ip["take"], "run_id": ip["run_id"], "time": _now_iso(), "recut": False,
                   "change": ip.get("change"), "completed": False, "verdict": None, "violations": None,
                   "first_violations": [], "warnings": None, "expected_state_ok": False, "frame_check": "not run",
                   "length": None, "hit": False, "decision": "retake: the take was interrupted (session killed)"}
            L.append_jsonl(self.take_log, row)
            self.st["pending_changes"] = [f"unchanged: retake after an interrupted take {ip['take']}"]
            self.st["next"] = "take"
        self.st["in_progress"] = None
        self.save()

    # ------------------------------------------------------------------------------------------------- stops
    def stop(self, point, needed, *, resume_next=None, extra=None):
        name, options = STOP_POINTS[point]
        best, hit = L.best_take(L.read_jsonl(self.take_log), self._length())
        s = {"point": point, "name": name, "needed": needed, "options": options, "at": _now_iso(),
             "phase": self.st["phase_seq"][-1] if self.st["phase_seq"] else None,
             "resume_next": resume_next or self.st["next"], "mode": "attended" if self.attended else "unattended",
             "best_take": (best["take"] if best else None), "best_is_hit": hit}
        if extra:
            s.update(extra)
        self.st["stop"] = s
        self.st["stops"].append(s)
        t0 = time.time()
        if self.attended:
            self.st["status"] = "waiting"
            q = self._stop_text(s, question=True)
            with open(os.path.join(self.dir, "question.md"), "w") as f:
                f.write(q)
            self.say(q)
        else:
            if point != 3 and self.st.get("created_items") and point != 2:
                self._cleanup_now()
            self.st["status"] = "ended"
            self._write_report(stop=s)
        self.timing.record("stop", t0, time.time(), result=f"stop {point}: {name}")
        self.save()
        raise Stopped()

    def _stop_text(self, s, question):
        lines = [f"STOP {s['point']} ({s['name']})", f"needed: {s['needed']}", "options: " + " | ".join(s["options"])]
        if s.get("best_take"):
            lines.append(f"best take so far: take {s['best_take']} ({'HIT' if s['best_is_hit'] else 'NOT A HIT'})")
        lines.append(f"loop state: {os.path.join(self.dir, 'state.json')} (takes {self.st['takes_used']} of "
                     f"{self.kv['max_takes']}, dry runs {self.st['dry_runs_used']} of {self.kv['dry_run_cap']})")
        if question:
            lines.append(f"answer with: vc-loop resume {self.dir} --answer <option>")
        return "\n".join(lines) + "\n"

    def answer(self, ans):
        """Apply a human answer to a waiting stop (attended), then the caller runs the loop again."""
        s = self.st.get("stop")
        if self.st["status"] not in ("waiting", "ended") or not s:
            raise Refused("the job is not at a stop point")
        opt, _, val = ans.partition("=")
        self.st["answers"].append({"at": _now_iso(), "point": s["point"], "answer": ans})
        p = s["point"]
        nxt = s["resume_next"]
        if opt == "abort":
            self.st["status"] = "ended"
            self._write_report(stop=s)
            self.save()
            return
        if p == 3:
            if opt == "raise-cap":
                self.st["knobs"]["max_takes"] = int(val)
                self.st["knob_sources"]["max_takes"] = "human answer"
                nxt = "fix"
            elif opt == "accept":
                self.st["accepted_not_a_hit"] = True
                nxt = "cleanup"
            elif opt == "change-spec":
                self.st["need_dry"] = True
                nxt = "preflight"
        elif p == 6:
            if opt == "raise-dry-cap":
                self.st["knobs"]["dry_run_cap"] = int(val)
                self.st["knob_sources"]["dry_run_cap"] = "human answer"
            elif opt == "change-spec":
                self.st["need_dry"] = True
                nxt = "preflight"
        elif p in (1, 4):
            nxt = "preflight"
            self.st["need_dry"] = True
        self.st["next"] = nxt
        self.st["status"] = "running"
        self.st["stop"] = None
        self.comp.knobs = self.st["knobs"]
        self.save()

    # --------------------------------------------------------------------------------------------- phases
    def _length(self):
        sp = self.spec()
        return sp.get("length") or [self.kv["length_min_s"], self.kv["length_max_s"]]

    def ph_preflight(self):
        t0 = time.time()
        sp = self.spec()
        probs = S.validate(sp)
        errs = S.errors(probs)
        if errs:
            raise Refused("the spec does not validate (F-10):\n  " + "\n  ".join(map(str, errs)))
        ok, why = S.approval_status(sp)
        fp = S.fingerprint(sp)
        self.st["contract_fingerprint"] = fp
        if not ok:
            if self.st["approved_fingerprint"] and fp != self.st["approved_fingerprint"]:
                self.stop(1, f"the contract changed after approval: {why}", resume_next="preflight")
            raise Refused(why)
        self.st["approved_fingerprint"] = sp["approval"]["fingerprint"]
        if sp.get("writes") and not self.kv["writes_allowed"]:
            self.stop(4, "the spec has approved writes but the knob writes_allowed is off", resume_next="preflight")
        self.profiles = P.for_spec(S.resolve(sp)[0], None, self.kv["app_profile"])
        self._plan(sp)
        self._approval_wait(sp)
        self.timing.record("preflight", t0, time.time(), result="ok")
        self.st["next"] = "dry" if self.st["need_dry"] else "take"

    def _approval_wait(self, sp):
        """The human's approval wait as its own F-28 record: from the last approval view of the approved contract
        to the approval (both stamped by vc-spec view / approve)."""
        if self.st.get("approval_logged"):
            return
        snap, _ = S.snapshot_paths(sp)
        evs = L.read_jsonl(os.path.join(os.path.dirname(snap), sp["name"] + ".events.jsonl"))
        fp = sp["approval"]["fingerprint"]
        appr = [e for e in evs if e.get("event") == "approve" and e.get("fingerprint") == fp]
        if appr:
            a = appr[-1]
            views = [e for e in evs if e.get("event") == "view" and e["t"] <= a["t"]]
            start = views[-1]["t"] if views else a["t"]
            self.timing.record("approval (human)", start, a["t"], human=True, result=f"approved by {a.get('approver')}")
        self.st["approval_logged"] = True

    def _plan(self, sp):
        est, _ = S.estimate_length(sp, self.kv)
        profs = P.for_spec(S.resolve(sp)[0], None, self.kv["app_profile"])
        mode = "attended (human reachable)" if self.attended else "UNATTENDED (no questions; every stop ends the run)"
        writes = [w["id"] for w in sp.get("writes", [])]
        n_act = sum(1 for s in sp["steps"] for a in s.get("actions", []) if a.get("op") in S.JEV_OPS)
        lines = [f"PLAN  {sp['name']}: {sp['title']}", f"  contract {S.fingerprint(sp)} approved by "
                 f"{sp['approval'].get('approver')} at {sp['approval'].get('approved_at')}",
                 f"  mode: {mode}", f"  app profile(s): {', '.join(p['name'] for p in profs)}",
                 f"  login mode: {self.kv['login_mode']}",
                 f"  writes: {'none (read-only)' if not writes else ', '.join(writes) + ' (approved)'}",
                 f"  estimated video length {est} s; machine time per take about {est + 3 + 0.7 * est + 15:.0f} s "
                 f"(take + cut + QA), dry run about {est:.0f} s; jev decisions per take about {n_act}-{n_act * 2}",
                 "  stop points: " + "; ".join(f"{k} {v[0]}" for k, v in STOP_POINTS.items()),
                 "  knobs (F-23):"] + K.table_lines(self.kv, self.st["knob_sources"])
        text = "\n".join(lines) + "\n"
        with open(os.path.join(self.dir, "plan.txt"), "w") as f:
            f.write(text)
        self.say(text)

    def _login_ok(self, before):
        """Cheap login check before every browser phase (F-26). A login stop never counts as a take."""
        if not self.cfg.get("login_check"):
            return
        t0 = time.time()
        try:
            rc, logf = self.comp.call("login_check", {"label": before}, label=f"login-{before}", browser=True,
                                      timeout=self._run_timeout())
        except subprocess.TimeoutExpired:
            rc, logf = None, None
        res = {0: "logged in", 20: "human needed", 21: "no credentials", 22: "recorder busy",
               C.LOCK_TIMEOUT_RC: "browser lock busy", None: "timed out"}.get(rc, f"error {rc}")
        self.timing.record("login", t0, time.time(), result=res)
        if rc == 0:
            return
        if rc == C.LOCK_TIMEOUT_RC:            # vc-lock timed out: the browser is busy, nothing is wrong with the login
            self.stop(7, self._lock_busy_text(), resume_next=before, extra={"cause": "lock_timeout"})
        if rc is None:
            self.stop(7, f"the login check did not finish within {self._run_timeout()} s (recorder or browser "
                         "down?)", resume_next=before, extra={"cause": "run_timeout"})
        if rc == 22:
            self.stop(7, "the recorder is busy (recording) or its status is unknown during the login check",
                      resume_next=before, extra={"cause": "recorder_busy"})
        if self.attended:
            self.stop(2, f"login check: {res}; log in once over the remote view (bin/vc-env url)", resume_next=before)
        self.stop(2, f"login check: {res}; unattended mode has no human login path (F-26/F-37)", resume_next=before)

    def _request(self, mode, run_id, n):
        sp = self.spec()
        job = self.st["job"]
        tail = run_id[len(job) + 1:] if run_id.startswith(job + "-") else run_id.rsplit("-", 1)[-1]
        unique = f"{sp['name']}-{job[-15:]}-{tail}"
        res, _missing = S.resolve(sp, {"unique": unique})
        profs = P.for_spec(res, None, self.kv["app_profile"])
        rd = self.run_dir(run_id)
        os.makedirs(rd, exist_ok=True)
        req = {"job": self.st["job"], "run_id": run_id, "mode": mode, "n": n, "run_dir": rd,
               "spec_path": self.st["spec_path"], "spec": S.public(res), "unique": unique,
               "deny_extra": P.deny_extra(profs) + list(sp.get("deny") or []),
               "approved_write_labels": {w["step"]: w.get("labels", []) for w in res.get("writes", [])},
               "knobs": {k: self.kv[k] for k in K.RUNNER_KNOBS + ("speedup_factor", "hold_final_s", "job_budget_usd", "run_timeout_s")},
               "profiles": [P.public(p) for p in profs], "job_cost_usd": self._job_cost()}
        path = os.path.join(rd, "request.json")
        with open(path, "w") as f:
            json.dump(req, f, indent=1, ensure_ascii=False)
        return req, path

    def _job_cost(self):
        return round(sum(r.get("jev_cost_usd") or 0 for r in self.timing.records()), 6)

    def _lock_busy_text(self):
        return (f"the browser lock was not free within lock_timeout_s={self.kv['lock_timeout_s']} s: another "
                f"explore, login check, dry run or take holds the one browser (C-18; holder: {state_dir()}/lock.log)")

    def _run_timeout(self):
        """Host-side cap on the run call: the lock wait, the runner's own run_timeout_s watchdog and a margin."""
        run = self.kv["run_timeout_s"]
        return self.kv["lock_timeout_s"] + run + min(60, run)

    def _run_component(self, extra, label):
        """Run the "run" component; returns (rc, logf), rc None on the host-side timeout."""
        try:
            return self.comp.call("run", extra, label=label, browser=True, timeout=self._run_timeout())
        except subprocess.TimeoutExpired:
            return None, os.path.join(self.dir, "logs", f"{label}.run.log")

    def _call_run(self, mode, run_id, n):
        req, path = self._request(mode, run_id, n)
        rc, logf = self._run_component({"request": path, "request_c": self._container_path(path),
                                        "run_dir": req["run_dir"]}, run_id)
        rp = os.path.join(req["run_dir"], "result.json")
        if rc is None and not os.path.exists(rp):
            return req, {"ok": False, "completed": False, "error_class": "run_timeout", "steps": [],
                         "reason": f"the run did not finish within {self._run_timeout()} s (log {logf})"}
        if not os.path.exists(rp):
            if rc == C.LOCK_TIMEOUT_RC:
                return req, {"ok": False, "completed": False, "error_class": "lock_timeout", "steps": [],
                             "reason": self._lock_busy_text()}
            return req, {"ok": False, "completed": False, "error_class": "recorder_down",
                         "reason": f"the runner wrote no result (exit {rc}, log {logf})", "steps": []}
        with open(rp) as f:
            res = json.load(f)
        for it in res.get("created_items", []):
            it = dict(it, run_id=run_id)
            if it not in self.st["created_items"]:
                self.st["created_items"].append(it)
        return req, res

    def _container_path(self, path):
        rd = runs_dir()
        return "/runs/" + os.path.relpath(path, rd) if path.startswith(rd) else path

    def _classify(self, res, resume_next):
        """Stops that a runner result can trigger (not failures of the take)."""
        if res.get("login_required"):
            self.stop(2, f"the page asked for a login during {res.get('mode', 'the run')} (C-31: no decision was "
                         "made on it)", resume_next=resume_next)
        if res.get("precondition_failed"):
            pf = res["precondition_failed"]
            self.stop(7, f"precondition not met: {pf.get('what')} (made true by: {pf.get('by')})",
                      resume_next=resume_next)
        if res.get("error_class") == "lock_timeout":
            self.stop(7, res.get("reason") or self._lock_busy_text(), resume_next=resume_next,
                      extra={"cause": "lock_timeout"})
        if res.get("error_class") == "run_timeout":
            self.stop(7, f"the run hung and was stopped: {res.get('reason')}", resume_next=resume_next,
                      extra={"cause": "run_timeout"})
        if res.get("error_class") == "recorder_down":
            self.stop(7, f"recorder or browser down: {res.get('reason')}", resume_next=resume_next,
                      extra={"cause": "recorder_down"})
        if res.get("error_class") == "budget":
            self.stop(7, f"the job's jev budget of ${self.kv['job_budget_usd']} is used up (C-12)",
                      resume_next=resume_next)

    def ph_dry(self):
        if self.st["dry_runs_used"] >= self.kv["dry_run_cap"]:
            self.stop(6, f"{self.st['dry_runs_used']} dry runs used, cap {self.kv['dry_run_cap']} (D-41); the last "
                         f"failure: {self.st.get('fix_source')}", resume_next="dry")
        self._login_ok("dry")
        n = self.st["dry_runs_used"] + 1
        run_id = self._fresh_run_id(f"{self.st['job']}-d{n}")
        self.st["dry_runs_used"] = n
        self.save()
        t0 = time.time()
        _req, res = self._call_run("dry", run_id, n)
        t1 = time.time()
        rd = self.run_dir(run_id)
        verdict = "green" if res.get("ok") else f"failed at {res.get('failed_step')}: {res.get('reason')}"
        self.timing.record("dry run", t0, t1, run_id=run_id, jev=L.jev_stats(rd), result=verdict,
                           steps_run=len(res.get("steps", [])))
        if res.get("login_required") or res.get("error_class") == "lock_timeout":
            self.st["dry_runs_used"] -= 1       # a login or lock stop is not a dry run that taught anything
        self._classify(res, "dry")
        if res.get("ok"):
            self.st["need_dry"] = False
            self.st["dry_ok_hash"] = _sha_file(self.st["spec_path"])
            self.st["next"] = "take"
        else:
            self.st["fix_source"] = {"kind": "dry run", "run_id": run_id, "failed_step": res.get("failed_step"),
                                     "reason": res.get("reason"), "run_dir": rd}
            self.st["next"] = "fix"

    def ph_take(self):
        if self.st["takes_used"] >= self.kv["max_takes"]:
            self.st["next"] = "cap"
            return
        if self.st["need_dry"] or self.st["dry_ok_hash"] != _sha_file(self.st["spec_path"]):
            self.st["need_dry"] = True           # any spec change since the green dry run -> a full dry run (F-11)
            self.st["next"] = "dry"
            return
        self._login_ok("take")
        k = self.st["takes_used"] + 1
        run_id = self._fresh_run_id(f"{self.st['job']}-t{k}")
        change = self.st["pending_changes"] or (["none (first take)"] if k == 1 else ["unchanged"])
        self.st["takes_used"] = k
        self.st["in_progress"] = {"phase": "take", "take": k, "run_id": run_id, "change": change}
        self.st["pending_changes"] = []
        self.save()
        t0 = time.time()
        _req, res = self._call_run("take", run_id, k)
        t1 = time.time()
        rd = self.run_dir(run_id)
        self.timing.record("take", t0, t1, run_id=run_id, take=k, jev=L.jev_stats(rd),
                           result="completed" if res.get("completed") else f"incomplete: {res.get('reason')}")
        if res.get("login_required") or res.get("error_class") == "lock_timeout":
            self.st["takes_used"] -= 1           # F-26: a login stop is not a failed take and does not count; nor
                                                 # is a take that never got the browser (lock busy, C-18)
            self.st["pending_changes"] = change
            self.st["in_progress"] = None
        self._classify(res, "take")
        self.st["current"] = {"take": k, "run_id": run_id, "run_dir": rd, "change": change, "result": res,
                              "time": _now_iso()}
        self.st["in_progress"] = None
        self.st["next"] = "cut" if res.get("completed") else "judge"

    def ph_cut(self):
        cur = self.st["current"]
        t0 = time.time()
        rc, logf = self.comp.call("cut", {"run_dir": cur["run_dir"], "run_id_c": cur["run_id"],
                                         "speedup_factor": self.kv["speedup_factor"]},
                                  label=f"{cur['run_id']}", browser=False, timeout=1800)
        t1 = time.time()
        timing = {}
        rp = os.path.join(cur["run_dir"], "cut", "record.json")
        if rc == 0 and os.path.exists(rp):
            with open(rp) as f:
                timing = json.load(f).get("timing", {})
        js = float(timing.get("join_s") or 0)
        self.timing.record("cut", t0, t1 - js, run_id=cur["run_id"], take=cur["take"],
                           result="ok" if rc == 0 else f"cutter exit {rc} (log {logf})", tool_timing=timing)
        self.timing.record("join", t1 - js, t1, run_id=cur["run_id"], take=cur["take"],
                           result="ok (cutter's join, timed by the cutter)" if rc == 0 else "not run")
        cur["cut_ok"] = rc == 0
        self.st["next"] = "gate" if rc == 0 else "judge"

    def ph_gate(self):
        cur = self.st["current"]
        sp_res, _ = S.resolve(self.spec(), {"unique": "-"})
        t0 = time.time()
        ev = gatefeed.build(cur["run_dir"], sp_res, cur["result"], dry_run=False)
        rc, logf = self.comp.call("gate", {"run_dir": cur["run_dir"], "run_id_c": cur["run_id"], "events": ev},
                                  label=cur["run_id"],
                                  browser=False, timeout=1800)
        t1 = time.time()
        rep_p = os.path.join(cur["run_dir"], "qa", "report.json")
        rep = {}
        if os.path.exists(rep_p):
            with open(rep_p) as f:
                rep = json.load(f)
        verdict = {0: "PASS", 1: "FAIL", 3: "ABORT"}.get(rc, f"ERROR {rc}")
        viol = rep.get("violations", []) if rc in (0, 1) else []
        cur["qa"] = {"verdict": verdict, "exit": rc, "violations": len(viol) if rc in (0, 1) else None,
                     "first": [f"{v.get('check')} {v.get('clip')} {v.get('t0')}s: {v.get('reason')}" for v in viol[:3]],
                     "warnings": len(rep.get("warnings", [])), "report": rep_p,
                     "report_txt": os.path.join(cur["run_dir"], "qa", "report.txt"), "log": logf,
                     "q11": any(v.get("check") == "Q-11" for v in viol)}
        self.timing.record("QA", t0, t1, run_id=cur["run_id"], take=cur["take"],
                           result=f"{verdict} ({cur['qa']['violations']} violations, {cur['qa']['warnings']} warnings)")
        self.st["next"] = "framecheck"

    # The viewer review (docs/steps/review.md) sits in the frame-check slot of the phase order: after a gate PASS,
    # before delivery. Component key "review" (old name "framecheck" still read).
    def _review_comp(self):
        return "review" if self.cfg.get("review") is not None else "framecheck"

    def ph_framecheck(self):
        cur = self.st["current"]
        if cur["qa"]["verdict"] != "PASS":
            j = RV.judge(None, cur["qa"]["verdict"])
            cur["frame_check"] = {"clean": None, "note": j["note"]}
            self.st["next"] = "judge"
            return
        t0 = time.time()
        sp = self.spec()
        sp_res, _ = S.resolve(sp, {"unique": "-"})
        rv_dir = os.path.join(cur["run_dir"], "review")
        try:
            req = RV.build_request(cur["run_dir"], RV.steps_from_spec(sp_res), model=self.kv["review_model"],
                                   privacy=sp.get("privacy") if self.kv["review_privacy"] else None, out_dir=rv_dir)
        except Exception as e:                    # no video or record: the reviewer is told, and must say so
            os.makedirs(rv_dir, exist_ok=True)
            req = {"run_dir": cur["run_dir"], "model": self.kv["review_model"], "sheets": [],
                   "steps": [dict(x, start=None, end=None) for x in RV.steps_from_spec(sp_res)],
                   "sample_error": f"{type(e).__name__}: {e}", "prompt": RV.checklist(),
                   "out": os.path.join(rv_dir, "result.json")}
            with open(os.path.join(rv_dir, "request.json"), "w") as f:
                json.dump(req, f, indent=1, ensure_ascii=False)
        rq = os.path.join(rv_dir, "request.json")
        cur["fc_t0"] = t0
        comp = self._review_comp()
        if self.comp.is_handoff(comp):
            self.st["next"] = "framecheck_read"
            self.st["status"] = "handoff"
            self.st["handoff"] = {"what": "review", "request": rq, "result": req["out"]}
            self.say(f"HANDOFF viewer review: review the sheets of {rq} (docs/steps/review.md), write {req['out']}, "
                     f"then `vc-loop resume {self.dir}`")
            return
        self.comp.call(comp, {"request": rq, "run_dir": cur["run_dir"]}, label=cur["run_id"], timeout=1800)
        self.st["next"] = "framecheck_read"

    def ph_framecheck_read(self):
        cur = self.st["current"]
        rv_dir = os.path.join(cur["run_dir"], "review")
        p = os.path.join(rv_dir, "result.json")
        if not os.path.exists(p):
            if self.comp.is_handoff(self._review_comp()):
                self.st["status"] = "handoff"
                self.st["phase_seq"].pop()
                return
            res = None
        else:
            with open(p) as f:
                res = json.load(f)
        j = RV.judge(res, cur["qa"]["verdict"])
        res = res or {}
        cur["frame_check"] = {"clean": j["clean"], "note": None if j["clean"] else RV.summary(j),
                              "blockers": j["blockers"], "minors": j["minors"],
                              "model": res.get("model", self.kv["review_model"]), "tokens": res.get("tokens"),
                              "seconds": res.get("seconds")}
        if j["blockers"]:                         # owner rule: each blocker is a candidate ledger entry
            cand = RV.ledger_candidates(j, case=self.spec().get("name"), run_id=cur["run_id"],
                                        date=_now_iso()[:10], review_dir=rv_dir)
            cp = os.path.join(rv_dir, "ledger-candidates.md")
            with open(cp, "w") as f:
                f.write(cand)
            cur["frame_check"]["ledger_candidates"] = cp
        self.timing.record("viewer review", cur.get("fc_t0", time.time()), time.time(), run_id=cur["run_id"],
                           take=cur["take"], agent_cost_usd=res.get("agent_cost_usd"), tokens=res.get("tokens"),
                           model_seconds=res.get("seconds"), model=res.get("model"),
                           result=RV.summary(j) if not j["clean"] else
                           f"clean ({len(j['minors'])} minor)")
        self.st["next"] = "judge"

    def ph_judge(self):
        cur = self.st["current"]
        res = cur["result"]
        qa = cur.get("qa") or {"verdict": "ABORT" if cur.get("cut_ok") is False else None, "violations": None,
                               "first": [], "warnings": None}
        fc = cur.get("frame_check") or {"clean": None, "note": "not run"}
        fp_now = S.fingerprint(self.spec())
        contract_ok = fp_now == self.st["approved_fingerprint"]
        exp_ok = bool(res.get("steps")) and all(s.get("verified") for s in res.get("steps", [])) and not qa.get("q11")
        hit = qa.get("verdict") == "PASS" and fc.get("clean") is True and contract_ok and res.get("completed")
        length = None
        idx_p = os.path.join(cur["run_dir"], "clips", "index.json")
        if os.path.exists(idx_p):
            with open(idx_p) as f:
                idx = json.load(f)
            length = round(sum(c.get("duration", 0) for c in idx.get("clips", [])), 2)
        if hit:
            decision = "deliver"
        elif self.st["takes_used"] < self.kv["max_takes"]:
            why = ("contract changed since approval (F-06)" if not contract_ok else
                   f"QA {qa.get('verdict')}" if qa.get("verdict") != "PASS" else "viewer review found blockers")
            decision = f"retake: {why}"
        else:
            decision = "stop: take cap reached without a hit"
        row = {"take": cur["take"], "run_id": cur["run_id"], "time": cur["time"], "recut": False,
               "change": cur["change"], "completed": bool(res.get("completed")), "verdict": qa.get("verdict"),
               "violations": qa.get("violations"), "first_violations": qa.get("first", []),
               "warnings": qa.get("warnings"), "expected_state_ok": exp_ok,
               "frame_check": ("clean" if fc.get("clean") else fc.get("note") or
                               "; ".join(f"{i.get('step')}: {i.get('what')}" for i in fc.get("issues", []))),
               "review_minor": [f"{RV._ts(m)} {m.get('region')}: {m.get('what')}" for m in fc.get("minors") or []],
               "ledger_candidates": fc.get("ledger_candidates"),
               "contract_ok": contract_ok, "length": length, "hit": bool(hit), "decision": decision,
               "qa_report": qa.get("report"), "run_dir": cur["run_dir"]}
        self._ledger_evidence(row)
        L.append_jsonl(self.take_log, row)
        self.st["last_verdict"] = {"take": cur["take"], "verdict": qa.get("verdict"), "hit": bool(hit)}
        if not hit:
            self.st["fix_source"] = {"kind": "take", "take": cur["take"], "run_id": cur["run_id"],
                                     "run_dir": cur["run_dir"], "verdict": qa.get("verdict"),
                                     "first_violations": qa.get("first", []), "frame_check": row["frame_check"],
                                     "ledger_candidates": row["ledger_candidates"],
                                     "reason": res.get("reason"), "failed_step": res.get("failed_step"),
                                     "contract_ok": contract_ok}
        if hit:
            self.st["hit_take"] = cur["take"]
            self.st["next"] = "deliver"
        elif self.st["takes_used"] < self.kv["max_takes"]:
            self.st["next"] = "fix"
        else:
            self.st["next"] = "cap"

    # ------------------------------------------------------------------------------------------------- fixing
    def _protected(self):
        pats = self.cfg.get("protected", [])
        out = {}
        for pat in pats:
            for f in sorted(glob.glob(pat.replace("{root}", P.ROOT), recursive=True)):
                if os.path.isfile(f):
                    out[f] = _sha_file(f)
        return out

    CODE_GLOBS = ["{root}/vc/**/*.py", "{root}/vc/**/*.js", "{root}/jev/vcjev/**/*.py", "{root}/loop/vcloop/**/*.py",
                  "{root}/profiles/**/*.json", "{root}/specs/quirks/*.md"]

    def _code(self):
        """Hashes of the fixable code outside the spec (a plain runner bug, a quirk record, a profile mitigation), without
        the protected QA files: a fix may be a code edit instead of a spec edit (docs/steps/on-fail.md, F-15)."""
        prot = set(self._protected())
        out = {}
        for pat in self.cfg.get("code", self.CODE_GLOBS):
            for f in sorted(glob.glob(pat.replace("{root}", P.ROOT), recursive=True)):
                if os.path.isfile(f) and f not in prot and "__pycache__" not in f:
                    out[f] = _sha_file(f)
        return out

    def ph_fix(self, mode="fix"):
        t0 = time.time()
        self.st["fix_count"] += 1
        n = self.st["fix_count"]
        fdir = os.path.join(self.dir, "fixes")
        os.makedirs(fdir, exist_ok=True)
        backup = os.path.join(fdir, f"spec-before-{n}.toml")
        shutil.copyfile(self.st["spec_path"], backup)
        req = {"mode": mode, "n": n, "model": self.kv["fixer_model"], "spec_path": self.st["spec_path"],
               "source": self.st.get("fix_source"), "take_log": self.take_log,
               "history": [r.get("change") for r in L.read_jsonl(self.take_log)],
               "flake_available": self.st["flakes_used"] < self.kv["flake_retakes"],
               "may_change": "the how: control descriptions, done conditions, waits, holds (not the final hold), "
                             "off-camera warm-ups, camera speed, masking, plain runner bugs (F-15)",
               "never": "QA checks/thresholds, cutter quality rules, contract parts: steps, expected states, "
                        "writes, final hold, length range (F-06/F-15)",
               "answer": {"kind": "fix | flake | stop", "hypothesis": "step X fails because Y; changing Z fixes it",
                          "change": {"what": "", "where": "", "old": "", "new": "", "why": ""},
                          "stop": {"reason": "login | write | mitigation | qa | app_bug | recorder_down | substance",
                                   "detail": ""}, "next": "(propose mode) the fix it would try next",
                          "ledger": {"title": "short name of the fix", "root_cause": "", "scope": "COMMON | SPECIFIC",
                                     "lives_in": "spec | quirk | profile | core code (<part>)"}},
               "ledger": self._ledger_request(),
               "out": os.path.join(fdir, f"fix-{n}.json")}
        rq = os.path.join(fdir, f"request-{n}.json")
        with open(rq, "w") as f:
            json.dump(req, f, indent=1, ensure_ascii=False)
        self.st["fix_ctx"] = {"n": n, "mode": mode, "request": rq, "out": req["out"], "backup": backup, "t0": t0,
                              "protected": self._protected(), "code": self._code(),
                              "fp_before": S.fingerprint(self.spec())}
        if self.comp.is_handoff("fixer"):
            self.st["status"] = "handoff"
            self.st["handoff"] = {"what": "fixer", "request": rq, "result": req["out"]}
            self.st["next"] = "fix_read" if mode == "fix" else "propose_read"
            self.say(f"HANDOFF fixer ({mode}): run the fixer subagent on {rq}; it edits only and writes {req['out']}; "
                     f"then `vc-loop resume {self.dir}`")
            return
        self.comp.call("fixer", {"request": rq}, label=f"fix-{n}")
        self.st["next"] = "fix_read" if mode == "fix" else "propose_read"

    def _ledger_request(self):
        src = self.st.get("fix_source") or {}
        cp = src.get("ledger_candidates")
        text = None
        if cp and os.path.exists(cp):
            with open(cp) as f:
                text = f.read()
        return {"file": LG.path(self.cfg, P.ROOT), "candidates_path": cp, "candidates": text}

    def _symptom(self):
        src = self.st.get("fix_source") or {}
        led = self._ledger_request()
        sy = LG.candidate_symptoms(led["candidates"])
        if sy:
            return "; ".join(sy)
        if src.get("kind") == "take":
            first = "; ".join(src.get("first_violations") or [])
            if src.get("verdict") != "PASS":
                return f"take {src.get('take')} ({src.get('run_id')}): gate {src.get('verdict')}" + (f": {first}" if first else "")
            return f"take {src.get('take')} ({src.get('run_id')}): viewer review: {src.get('frame_check')}"
        return (f"{src.get('kind', 'run')} {src.get('run_id', '')}: {src.get('reason') or ''} "
                f"{('step ' + src['failed_step']) if src.get('failed_step') else ''}").strip()

    def _ledger_add(self, fx, change_note, changed_files, kind):
        """One FIXES-LEDGER entry per accepted fix or flake retake; the evidence is filled in after the retake."""
        lg = fx.get("ledger") or {}
        ch = fx.get("change") or {}
        led = self._ledger_request()
        title = lg.get("title") or (f"{self.spec().get('name')}: flake retake, no change" if kind == "flake" else
                                    f"{self.spec().get('name')}: {ch.get('what')} @ {ch.get('where')}")
        files = ", ".join(f"`{os.path.relpath(f, P.ROOT) if f.startswith(P.ROOT + os.sep) else f}`"
                          for f in changed_files)
        cand = f"; review candidates {led['candidates_path']}" if led["candidates"] else ""
        fields = {"date": _now_iso()[:10], "case/spec": f"{self.spec().get('name')} (job {os.path.basename(self.dir)})",
                  "symptom": self._symptom(),
                  "root cause": lg.get("root_cause") or fx.get("hypothesis") or "(fixer gave none)",
                  "change": (change_note + (f"; files {files}" if files else "") + "; uncommitted (loop edit)"),
                  "evidence": f"pending: the next take's gate and viewer review{cand}",
                  "scope guess": lg.get("scope") or "SPECIFIC",
                  "lives in": lg.get("lives_in") or ("core code" if any(not f.endswith(".toml") for f in changed_files)
                                                     else "spec"),
                  "owner decision": ""}
        try:
            fid = LG.append(led["file"], title, fields)
        except OSError as e:                      # the fix itself stands; a ledger we cannot write is only reported
            self.say(f"warning: FIXES-LEDGER not written ({e})")
            return None
        self.st.setdefault("ledger_open", []).append(fid)
        return fid

    def _ledger_evidence(self, row):
        ids = self.st.get("ledger_open") or []
        if not ids:
            return
        ev = (f"retake take {row['take']} ({row['run_id']}): gate {row['verdict']}, viewer review "
              f"{row['frame_check']}; {'HIT' if row['hit'] else 'no hit'} ({row['decision']})")
        for fid in ids:
            try:
                LG.set_evidence(LG.path(self.cfg, P.ROOT), fid, ev)
            except OSError as e:
                self.say(f"warning: FIXES-LEDGER evidence for {fid} not written ({e})")
        row["ledger_entries"] = list(ids)
        self.st["ledger_open"] = []

    def _revert(self, ctx):
        shutil.copyfile(ctx["backup"], self.st["spec_path"])

    def ph_fix_read(self):
        ctx = self.st["fix_ctx"]
        if not os.path.exists(ctx["out"]):
            if self.comp.is_handoff("fixer"):
                self.st["status"] = "handoff"
                self.st["phase_seq"].pop()
                return
            self._revert(ctx)
            self.stop(7, "the fixer wrote no fix note", resume_next="fix")
        with open(ctx["out"]) as f:
            fx = json.load(f)
        changed_text = _sha_file(self.st["spec_path"]) != _sha_file(ctx["backup"])
        code_after = self._code() if "code" in ctx else {}
        changed_code = sorted(k for k in set(code_after) | set(ctx.get("code", {}))
                              if code_after.get(k) != ctx.get("code", {}).get(k)) if "code" in ctx else []
        fp_after = S.fingerprint(self.spec())
        prot_after = self._protected()
        t1 = time.time()
        kind = fx.get("kind")
        result = kind
        try:
            if prot_after != ctx["protected"]:
                bad = sorted(k for k in set(prot_after) | set(ctx["protected"])
                             if prot_after.get(k) != ctx["protected"].get(k))
                self._revert(ctx)
                result = "refused: protected files changed"
                self.stop(7, f"the fixer changed protected files (QA definition / cutter rules): {bad}; "
                             "patch the spec, not the judge (F-19)", resume_next="fix")
            if kind == "stop":
                reason = (fx.get("stop") or {}).get("reason", "qa")
                self._revert(ctx)
                result = f"stop: {reason}"
                self.stop(FIX_STOP_REASONS.get(reason, 7), f"the fixer stopped: {reason}: "
                          f"{(fx.get('stop') or {}).get('detail', '')}", resume_next="fix")
            if kind == "flake":
                if changed_text or changed_code:
                    self._revert(ctx)
                    result = "refused: a flake retake must be unchanged"
                    self.stop(7, "the fixer asked for an unchanged retake but edited the spec", resume_next="fix")
                if self.st["flakes_used"] >= self.kv["flake_retakes"]:
                    result = "refused: flake retake already used"
                    self.stop(7, "the fixer asked for a second unchanged retake (F-15 allows one)", resume_next="fix")
                self.st["flakes_used"] += 1
                self.st["pending_changes"].append(f"unchanged: flake because {fx.get('hypothesis', '')}")
                self._ledger_add(fx, f"none (unchanged retake): {fx.get('hypothesis', '')}", [], "flake")
                self.st["next"] = "take"
                return
            # a fix: exactly one edit, contract untouched, spec still valid
            if not changed_text and not changed_code:
                result = "refused: no edit"
                self.stop(7, "the fixer returned a fix note but changed nothing", resume_next="fix")
            if fp_after != ctx["fp_before"]:
                self._revert(ctx)
                result = "refused: contract change (reverted)"
                self.stop(1, f"the fix changes the contract (what the video shows): {fx.get('change')}; it was "
                             "reverted. A change of substance needs a new approval (F-06, F-15)", resume_next="fix")
            errs = S.errors(S.validate(self.spec()))
            if errs:
                self._revert(ctx)
                result = "refused: spec invalid (reverted)"
                self.stop(7, "the fixed spec does not validate: " + "; ".join(map(str, errs[:3])), resume_next="fix")
            ch = fx.get("change") or {}
            note = (f"{ch.get('what')} @ {ch.get('where')}: {ch.get('old')!r} -> {ch.get('new')!r} "
                    f"(why: {ch.get('why') or fx.get('hypothesis')})")
            if changed_code:
                note += " [code: " + ", ".join(os.path.relpath(f, P.ROOT) for f in changed_code) + "]"
            self.st["pending_changes"].append(note)
            files = ([self.st["spec_path"]] if changed_text else []) + changed_code
            fid = self._ledger_add(fx, note, files, "fix")
            if fid:
                result = f"{kind} ({fid})"
            self.st["need_dry"] = True
            self.st["next"] = "dry"
        finally:
            self.timing.record("fix", ctx["t0"], t1, agent_cost_usd=fx.get("agent_cost_usd"),
                               result=result, fixer_model=fx.get("model", self.kv["fixer_model"]),
                               hypothesis=fx.get("hypothesis"))

    def ph_cap(self):
        self.ph_fix(mode="propose")

    def ph_propose_read(self):
        ctx = self.st["fix_ctx"]
        if not os.path.exists(ctx["out"]):
            if self.comp.is_handoff("fixer"):
                self.st["status"] = "handoff"
                self.st["phase_seq"].pop()
                return
            fx = {}
        else:
            with open(ctx["out"]) as f:
                fx = json.load(f)
        if _sha_file(self.st["spec_path"]) != _sha_file(ctx["backup"]):
            self._revert(ctx)       # propose mode never edits
        self.st["next_fix"] = fx.get("next") or fx.get("hypothesis") or "(the fixer proposed nothing)"
        self.timing.record("fix", ctx["t0"], time.time(), result="proposal only (cap reached)",
                           agent_cost_usd=fx.get("agent_cost_usd"))
        self.st["next"] = "deliver"

    # ------------------------------------------------------------------------------------------- delivery
    def ph_deliver(self):
        t0 = time.time()
        rows = L.read_jsonl(self.take_log)
        best, hit = L.best_take(rows, self._length())
        dl = {"best_take": best["take"] if best else None, "hit": hit, "label": "HIT" if hit else "NOT A HIT",
              "video": None, "length": best.get("length") if best else None}
        if best and os.path.exists(os.path.join(best["run_dir"], "full.mp4")):
            ddir = os.path.join(self.dir, "deliver")
            os.makedirs(ddir, exist_ok=True)
            dst = os.path.join(ddir, f"{self.st['spec_name']}{'' if hit else '-NOT-A-HIT'}.mp4")
            shutil.copyfile(os.path.join(best["run_dir"], "full.mp4"), dst)
            dl["video"] = dst
        self.st["delivery"] = dl
        self.timing.record("deliver", t0, time.time(), take=dl["best_take"], result=dl["label"])
        if hit:
            self.st["next"] = "cleanup"
        else:
            self._write_report()
            self.st["next"] = "cleanup"
            if self.attended:
                self.stop(3, f"{self.st['takes_used']} of {self.kv['max_takes']} takes used without a hit; best take "
                             f"{dl['best_take']} delivered as NOT A HIT; next fix the fixer would try: "
                             f"{self.st.get('next_fix')}", resume_next="cleanup")
            self._cleanup_now()
            self.stop(3, f"{self.st['takes_used']} of {self.kv['max_takes']} takes used without a hit; best take "
                         f"{dl['best_take']} delivered as NOT A HIT; next fix: {self.st.get('next_fix')}",
                      resume_next="cleanup")

    def _cleanup_now(self):
        """C-34 / OD-26: delete only the job's own tracked items, only via the spec's safe delete path."""
        if self.st.get("cleanup"):
            return
        t0 = time.time()
        sp = self.spec()
        writes = {w["id"]: w for w in sp.get("writes", [])}
        profs = {p["name"]: p for p in P.for_spec(S.resolve(sp)[0], None, self.kv["app_profile"])}
        safe = any((p.get("cleanup") or {}).get("safe_delete") for p in profs.values())
        todo, left = [], []
        for it in self.st["created_items"]:
            w = writes.get(it.get("write"))
            if w and w.get("cleanup") and safe and it.get("id") and it.get("name"):
                todo.append(it)
            else:
                left.append(dict(it, why="no clear, safe delete path (C-34)"))
        deleted = []
        if todo:
            try:
                self._login_ok("cleanup")
            except Stopped:
                raise
            run_id = self._fresh_run_id(f"{self.st['job']}-c1")
            req, path = self._request("cleanup", run_id, 1)
            req["cleanup_items"] = todo
            req["cleanup"] = {it["write"]: writes[it["write"]]["cleanup"] for it in todo}
            with open(path, "w") as f:
                json.dump(req, f, indent=1, ensure_ascii=False)
            self._run_component({"request": path, "request_c": self._container_path(path),
                                 "run_dir": req["run_dir"]}, run_id)
            rp = os.path.join(req["run_dir"], "result.json")
            res = json.load(open(rp)) if os.path.exists(rp) else {}
            done_ids = {d.get("id") for d in res.get("deleted_items", [])}
            for it in todo:
                (deleted if it["id"] in done_ids else left).append(
                    it if it["id"] in done_ids else dict(it, why="delete did not confirm"))
            self.timing.record("cleanup", t0, time.time(), run_id=run_id, jev=L.jev_stats(req["run_dir"]),
                               result=f"{len(deleted)} deleted, {len(left)} left")
        else:
            self.timing.record("cleanup", t0, time.time(), result=f"0 deleted, {len(left)} left")
        self.st["cleanup"] = {"deleted": deleted, "left": left}
        self.save()

    def ph_cleanup(self):
        self._cleanup_now()
        self._write_report()
        self.st["status"] = "done"
        self.st["next"] = "done"

    def _write_report(self, stop=None):
        rows = L.read_jsonl(self.take_log)
        best, hit = L.best_take(rows, self._length())
        dl = self.st.get("delivery") or {}
        summ = L.summarize(self.timing.records())
        sp = self.spec()
        lines = [f"# Delivery: {sp['name']} ({self.st['job']})", ""]
        if best:
            lab = "HIT" if hit else "NOT A HIT"
            lines += [f"video: {dl.get('video') or os.path.join(best['run_dir'], 'full.mp4')} ({best.get('length')} s) "
                      f"[{lab}]",
                      f"verdict: take {best['take']}: QA {best.get('verdict')}, {best.get('violations')} violations, "
                      f"{best.get('warnings')} warnings; viewer review {best.get('frame_check')}; {lab}",
                      f"QA report: {best.get('qa_report')}"]
        else:
            lines.append("video: none (no completed take)")
        lines += [f"takes used: {self.st['takes_used']} of {self.kv['max_takes']}; dry runs "
                  f"{self.st['dry_runs_used']} of {self.kv['dry_run_cap']}",
                  f"take log: {self.take_log}", f"timing log: {self.timing.path}",
                  f"jev: {summ['jev_decisions']} decisions, mean {summ['ms_per_decision'] or 0:.0f} ms, "
                  f"${summ['jev_cost_usd']:.5f} (job budget ${self.kv['job_budget_usd']})"]
        if best and not hit:
            lines += ["open violations of the best take:"] + [f"  - {v}" for v in best.get("first_violations") or []]
            lines.append(f"next fix the fixer would try: {self.st.get('next_fix', '-')}")
            if self.attended and not stop:
                lines.append("question: raise the cap, change the spec, or accept the take?")
        writes = sp.get("writes", [])
        lines.append("writes that ran: " + (", ".join(w["id"] for w in writes) if writes else "none (read-only)"))
        cl = self.st.get("cleanup") or {}
        if self.st["created_items"]:
            lines.append("created items: deleted " + (", ".join(f"{i['name']} ({i['id']})" for i in cl.get("deleted", []))
                                                      or "none") +
                         "; left " + (", ".join(f"{i['name']} ({i['id']}): {i.get('why')}" for i in cl.get("left", []))
                                      or "none"))
        else:
            lines.append("created items: none")
        lines.append("fix promotion proposals (F-35): none (S2)")
        if best and best.get("review_minor"):
            lines += ["viewer review, minor findings (not blocking):"] + [f"  - {m}" for m in best["review_minor"]]
        if best and best.get("warnings"):
            lines.append(f"warnings kept: {best.get('warnings')} (see the QA report)")
        if not self.attended:
            ap = sp.get("approval", {})
            lines.append(f"unattended: approver {ap.get('approver')}; assumptions: "
                         + ("; ".join(ap.get("assumptions", [])) or "none recorded"))
        if stop:
            lines += ["", "## Stop report (F-22)", self._stop_text(stop, question=False)]
        lines += ["", "## Timing (F-28)"] + L.summary_lines(summ)
        lines += ["", "## Take log (F-30)"]
        for r in rows:
            lines.append(f"  take {r['take']}: {r.get('verdict')} ({r.get('violations')} viol., {r.get('warnings')} warn.),"
                         f" viewer review {r.get('frame_check')}, length {r.get('length')}, change: {r.get('change')}, "
                         f"-> {r.get('decision')}")
        if best and best.get("qa_report"):
            txt = best["qa_report"].replace("report.json", "report.txt")
            if os.path.exists(txt):
                lines += ["", "## Full QA report of the delivered take", "```", open(txt).read().rstrip(), "```"]
        text = "\n".join(lines) + "\n"
        with open(os.path.join(self.dir, "report.md"), "w") as f:
            f.write(text)
        self.say(text)
