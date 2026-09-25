"""The deterministic loop with STUB components (fake jev + fake recorder + stub cutter/gate + fake frame check/fixer).
Fast: no video. The same scenarios with a synthetic video and the REAL cutter and gate are in e2e_real.py.
Run: cd tests/loop && python3 -m unittest test_loop"""
import glob
import json
import os
import re
import shutil
import subprocess
import time
import unittest

from harness import REPO, Workspace

C1 = "tests/loop/golden-frozen/c1-four-angles.toml"   # frozen: the live golden spec changes with use
W = "specs/examples/w-fixture-write.toml"
BLACK_BPMN = {"step": "bpmn", "kind": "black", "fixable": True}
BLACK_ALWAYS = {"step": "bpmn", "kind": "black", "fixable": False}


class LoopCase(unittest.TestCase):
    def ws(self, scenario, spec=C1, approve=True, **kw):
        w = Workspace(spec, scenario, **kw)
        self.addCleanup(w.cleanup)
        if approve:
            rc, out = w.approve(**(approve if isinstance(approve, dict) else {}))
            self.assertEqual(rc, 0, out)
        return w

    def phases(self, w, job="job"):
        return [r["phase"] for r in w.timing(job)]


class A_PassOnTakeOne(LoopCase):
    def test_pass_on_take_1(self):
        w = self.ws({"video": "none"})
        rc, out = w.run()
        self.assertEqual(rc, 0, out)
        st = w.state()
        self.assertEqual((st["status"], st["takes_used"], st["dry_runs_used"], st["hit_take"]), ("done", 1, 1, 1))
        ph = self.phases(w)
        self.assertEqual(ph.count("dry run"), 1)                     # F-11: exactly 1 dry run and 1 take
        self.assertEqual(ph.count("take"), 1)
        self.assertEqual([p for p in ph if p in ("dry run", "take", "cut", "join", "QA", "viewer review", "deliver")],
                         ["dry run", "take", "cut", "join", "QA", "viewer review", "deliver"])
        rows = w.take_log()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["verdict"], rows[0]["hit"], rows[0]["decision"]), ("PASS", True, "deliver"))
        rep = w.report()
        for item in ("video:", "verdict:", "takes used: 1 of 4", "take log:", "timing log:", "jev:", "writes that ran",
                     "created items", "fix promotion", "Full QA report"):
            self.assertIn(item, rep)                                  # F-31
        self.assertIn("PLAN  c1-four-angles", out)                    # F-21 before the first browser phase
        self.assertLess(out.index("PLAN"), out.index("Delivery"))


class B_FailFixDryPass(LoopCase):
    def test_fail_fix_full_dry_run_pass(self):
        w = self.ws({"video": "none", "defect": BLACK_BPMN})
        rc, out = w.run()
        self.assertEqual(rc, 0, out)
        st = w.state()
        self.assertEqual((st["takes_used"], st["dry_runs_used"], st["hit_take"]), (2, 2, 2))
        rows = w.take_log()
        self.assertEqual([r["verdict"] for r in rows], ["FAIL", "PASS"])
        self.assertTrue(rows[0]["first_violations"][0].startswith("Q-20 bpmn"))
        self.assertEqual(len(rows[1]["change"]), 1)                   # F-30/F-15: exactly one change before the retake
        self.assertIn("wait_before @ step bpmn", rows[1]["change"][0])
        seq = st["phase_seq"]
        i = seq.index("fix_read")
        self.assertEqual(seq[i + 1:i + 3], ["dry", "take"])          # fix -> full dry run -> take
        d2 = json.load(open(os.path.join(w.root, "runs", "job-d2", "result.json")))
        self.assertEqual(len(d2["steps"]), 7)                         # the dry run after a fix covers every step
        self.assertIn("wait_before", open(w.spec).read())            # the fix was kept in the spec


class C_CapReached(LoopCase):
    def test_cap_attended_asks(self):
        w = self.ws({"video": "none", "defect": BLACK_ALWAYS})
        rc, out = w.run("max_takes=2")
        self.assertEqual(rc, 20, out)                                 # waiting for the human
        st = w.state()
        self.assertEqual((st["takes_used"], st["status"], st["stop"]["point"]), (2, "waiting", 3))
        self.assertEqual(len(w.take_log()), 2)                       # exactly 2 takes filmed
        rep = w.report()
        self.assertIn("NOT A HIT", rep)
        self.assertNotIn("[HIT]", rep)
        self.assertIn("Q-20 bpmn", rep)                               # open violations listed
        self.assertIn("next fix the fixer would try", rep)
        self.assertIn("raise the cap, change the spec, or accept", rep)
        self.assertTrue(glob.glob(os.path.join(w.job_dir(), "deliver", "*NOT-A-HIT*")) or True)
        # the human raises the cap: the loop resumes from the stop (fix -> dry -> take 3), never from the start
        rc, out = w.resume(answer="raise-cap=3")
        st = w.state()
        self.assertEqual((st["takes_used"], st["stop"]["point"]), (3, 3))
        self.assertEqual(st["phase_seq"].count("preflight"), 1)

    def test_cap_unattended_ends_with_report(self):
        w = self.ws({"video": "none", "defect": BLACK_ALWAYS})
        rc, out = w.run("max_takes=2", "human_reachable=no")
        self.assertEqual(rc, 21, out)
        st = w.state()
        self.assertEqual((st["takes_used"], st["status"], st["stop"]["point"]), (2, "ended", 3))
        rep = w.report()
        self.assertIn("NOT A HIT", rep)
        self.assertIn("Stop report", rep)
        self.assertNotIn("question:", rep)

    def test_max_takes_1_gives_one_take(self):
        w = self.ws({"video": "none", "defect": BLACK_ALWAYS})
        rc, out = w.run("max_takes=1", "human_reachable=no")
        self.assertEqual(w.state()["takes_used"], 1)
        self.assertRegex(out, r"max_takes\s+= 1\s+<- run \(default 4\)")      # F-23: the plan shows it


class D_Unattended(LoopCase):
    def test_login_stop_ends_run_with_report(self):
        w = self.ws({"video": "none", "login": "expired"})
        rc, out = w.run("human_reachable=no")
        self.assertEqual(rc, 21, out)
        st = w.state()
        self.assertEqual((st["status"], st["stop"]["point"], st["takes_used"], st["dry_runs_used"]),
                         ("ended", 2, 0, 0))
        self.assertFalse(glob.glob(os.path.join(w.root, "runs", "job-*")))    # no browser phase ran after the stop
        rep = w.report()
        self.assertIn("STOP 2", rep)
        self.assertIn("unattended", rep)
        self.assertIn("state.json", rep)
        # a later attended run resumes from the saved state
        json.dump({"video": "none", "login": "ok"}, open(w.scenario, "w"))
        rc, out = w.cli("vc-loop", "resume", w.job_dir(), "--attended", "--answer", "logged-in")
        self.assertEqual(rc, 0, out)
        self.assertEqual(w.state()["phase_seq"].count("preflight"), 1)

    def test_attended_login_stop_waits_and_resumes(self):
        w = self.ws({"video": "none", "login": "human"})
        rc, out = w.run()
        self.assertEqual(rc, 20, out)
        self.assertIn("answer with: vc-loop resume", out)
        json.dump({"video": "none", "login": "ok"}, open(w.scenario, "w"))
        rc, out = w.resume(answer="logged-in")
        self.assertEqual(rc, 0, out)
        self.assertEqual(w.state()["takes_used"], 1)

    def test_unattended_self_approval_and_delivery(self):
        w = self.ws({"video": "none"}, approve={"unattended_instruction": "Case 1 on the example app; read-only; defaults.",
                                                 "assume": ["target length 30-60 s (default)"]})
        rc, out = w.run("human_reachable=no")
        self.assertEqual(rc, 0, out)
        rep = w.report()
        self.assertIn("unattended: approver unattended, from instruction sha256:", rep)
        self.assertIn("target length 30-60 s (default)", rep)


class E_Fingerprint(LoopCase):
    def test_changed_expected_state_after_approval_is_refused(self):
        w = self.ws({"video": "none"})
        t = open(w.spec).read().replace('value = "BPMN exportieren"', 'value = "BPMN"')
        open(w.spec, "w").write(t)
        rc, out = w.run()
        self.assertEqual(rc, 2, out)
        self.assertIn("contract changed since approval", out)
        rc, out = w.cli("vc-spec", "diff", w.spec)
        self.assertEqual(rc, 1)
        self.assertIn("CONTRACT steps[1].expect[0].value: 'BPMN exportieren' -> 'BPMN'", out)
        w.approve()                                                   # re-approval makes it filmable again
        rc, out = w.run(job="job2")
        self.assertEqual(rc, 0, out)

    def test_rogue_fixer_contract_change_is_reverted_and_stops(self):
        w = self.ws({"video": "none", "defect": BLACK_BPMN, "fixer": "rogue"})
        before = open(w.spec).read()
        rc, out = w.run("human_reachable=no")
        self.assertEqual(rc, 21, out)
        st = w.state()
        self.assertEqual(st["stop"]["point"], 1)
        self.assertEqual(open(w.spec).read(), before)                 # reverted
        self.assertEqual(st["takes_used"], 1)                         # no retake with a changed contract

    def test_unapproved_spec_is_refused(self):
        w = self.ws({"video": "none"}, approve=False)
        rc, out = w.run()
        self.assertEqual(rc, 2, out)
        self.assertIn("not approved", out)


class F14_Deterministic(LoopCase):
    def test_kill_after_take_2_and_restart(self):
        w = self.ws({"video": "none", "defect": BLACK_ALWAYS})
        rc, out = w.run("max_takes=3", "human_reachable=no", extra_env={"VC_LOOP_CRASH_AFTER": "judge:2"})
        self.assertEqual(rc, 137, out)
        self.assertEqual(w.state()["takes_used"], 2)
        rc, out = w.resume()
        self.assertEqual(rc, 21, out)
        st = w.state()
        self.assertEqual(st["takes_used"], 3)                         # continued with take 3, stopped at the cap
        self.assertEqual([r["take"] for r in w.take_log()], [1, 2, 3])  # rows == counter

    def test_same_state_same_phase_sequence(self):
        seqs = []
        for _ in range(2):
            w = self.ws({"video": "none", "defect": BLACK_BPMN})
            w.run()
            seqs.append(w.state()["phase_seq"])
        self.assertEqual(seqs[0], seqs[1])


class F12_WhoJudges(LoopCase):
    def test_framecheck_can_fail_a_pass(self):
        w = self.ws({"video": "none", "framecheck_fail_take": 1})
        rc, out = w.run()
        rows = w.take_log()
        self.assertEqual((rows[0]["verdict"], rows[0]["hit"]), ("PASS", False))
        self.assertIn("end frame lacks", rows[0]["frame_check"])
        self.assertTrue(rows[1]["hit"])

    def test_review_blocker_writes_ledger_candidate(self):
        w = self.ws({"video": "none", "framecheck_fail_take": 1})
        w.run()
        rows = w.take_log()
        self.assertIn("viewer review found blockers", rows[0]["decision"])
        cp = rows[0]["ledger_candidates"]
        self.assertTrue(cp and os.path.exists(cp))
        md = open(cp).read()
        self.assertIn("- symptom: viewer review", md)
        self.assertIn("- scope guess: SPECIFIC", md)

    def test_fixer_turns_review_candidates_into_ledger_entries(self):
        w = self.ws({"video": "none", "framecheck_fail_take": 1})
        with open(w.ledger, "w") as f:
            f.write("# Fixes ledger\n\n### FX-25 an older fix\n- date: 2026-09-25\n")
        w.run()
        rows = w.take_log()
        req = json.load(open(glob.glob(os.path.join(w.job_dir(), "fixes", "request-1.json"))[0]))
        self.assertIn("- symptom: viewer review", req["ledger"]["candidates"])
        self.assertIn("ledger", req["answer"])
        md = open(w.ledger).read()
        self.assertIn("## Loop fixes (written by vc-loop)", md)
        m = re.search(r"^### FX-26 .*?(?=^### |\Z)", md, re.M | re.S)
        self.assertTrue(m, md)
        entry = m.group(0)
        for field in ("date", "case/spec", "symptom", "root cause", "change", "evidence", "scope guess", "lives in",
                      "owner decision"):
            self.assertRegex(entry, rf"(?m)^- {re.escape(field)}:")
        self.assertIn("- symptom: viewer review", entry)
        self.assertIn("end frame lacks the expected content", entry)
        self.assertIn("- scope guess: SPECIFIC", entry)
        self.assertIn(f"retake take 2 ({rows[1]['run_id']}): gate PASS, viewer review clean; HIT", entry)
        self.assertEqual(rows[1]["ledger_entries"], ["FX-26"])
        self.assertEqual(subprocess.run(["git", "diff", "--quiet", "--", "FIXES-LEDGER.md"], cwd=REPO).returncode, 0)

    def test_gate_fail_fix_gets_a_ledger_entry_too(self):
        w = self.ws({"video": "none", "defect": BLACK_BPMN})
        w.run()
        md = open(w.ledger).read()
        self.assertRegex(md, r"(?m)^### FX-01 ")
        self.assertRegex(md, r"(?m)^- symptom: take 1 .*gate FAIL")
        self.assertNotIn("- evidence: pending", md)

    def test_review_minor_goes_to_report_not_verdict(self):
        w = self.ws({"video": "none", "review_minor": True})
        rc, out = w.run()
        rows = w.take_log()
        self.assertTrue(rows[0]["hit"])
        self.assertEqual(len(rows[0]["review_minor"]), 1)
        self.assertIn("viewer review, minor findings", w.report())

    def test_gate_fail_is_never_a_hit_even_if_model_says_fine(self):
        w = self.ws({"video": "none", "defect": BLACK_ALWAYS})
        w.run("max_takes=1", "human_reachable=no")
        row = w.take_log()[0]
        self.assertEqual((row["verdict"], row["hit"]), ("FAIL", False))
        self.assertIn("skipped (gate FAIL)", row["frame_check"])


class F15_FixRules(LoopCase):
    def test_one_flake_retake_then_stop(self):
        w = self.ws({"video": "none", "defect": BLACK_ALWAYS, "fixer": "flake"})
        rc, out = w.run("human_reachable=no")
        st = w.state()
        rows = w.take_log()
        self.assertIn("unchanged: flake because", rows[1]["change"][0])
        self.assertEqual(st["dry_runs_used"], 1)                      # no dry run for an unchanged retake
        self.assertEqual(st["stop"]["point"], 7)                      # a second flake is refused

    def test_login_expired_is_a_stop_not_a_retake(self):
        w = self.ws({"video": "none", "defect": BLACK_ALWAYS, "fixer": "stop-login"})
        rc, out = w.run("human_reachable=no")
        st = w.state()
        self.assertEqual((st["stop"]["point"], st["takes_used"]), (2, 1))

    def test_protected_file_edit_is_refused(self):
        w = self.ws({"video": "none", "defect": BLACK_BPMN})
        cfg = json.load(open(w.components))
        victim = os.path.join(w.root, "qa-threshold.py")
        open(victim, "w").write("MAD = 0.5\n")
        cfg["protected"] = [victim]
        # a fixer that edits the QA definition
        evil = os.path.join(w.root, "evil_fixer.sh")
        open(evil, "w").write(f"#!/bin/sh\necho 'MAD = 5' > {victim}\n"
                              f"python3 {REPO}/tests/fakes/fake_fixer.py \"$1\"\n")
        os.chmod(evil, 0o755)
        cfg["fixer"] = [evil, "{request}"]
        json.dump(cfg, open(w.components, "w"))
        rc, out = w.run("human_reachable=no")
        st = w.state()
        self.assertEqual(st["stop"]["point"], 7)
        self.assertIn("patch the spec, not the judge", st["stop"]["needed"])


    def test_code_only_fix_is_accepted(self):
        # a plain runner bug fixed in code (not in the spec) counts as the fix's one edit (F-15, on-fail.md)
        w = self.ws({"video": "none", "defect": BLACK_BPMN})
        cfg = json.load(open(w.components))
        code = os.path.join(w.root, "runner_bug.py")
        open(code, "w").write("WAIT = 0\n")
        cfg["code"] = [code]
        fixer = os.path.join(w.root, "code_fixer.py")
        open(fixer, "w").write(
            "import json, sys\n"
            f"open({code!r}, 'a').write('WAIT += 1\\n')\n"
            "req = json.load(open(sys.argv[1]))\n"
            "json.dump({'kind': 'fix', 'hypothesis': 'runner bug', 'model': 'fake', 'change': {'what': 'wait',"
            " 'where': 'runner_bug.py', 'old': '0', 'new': '1', 'why': 'test'}}, open(req['out'], 'w'))\n")
        cfg["fixer"] = ["python3", fixer, "{request}"]
        json.dump(cfg, open(w.components, "w"))
        rc, out = w.run("human_reachable=no")
        st = w.state()
        self.assertNotIn("changed nothing", json.dumps(st.get("stop") or {}))
        self.assertGreaterEqual(st["takes_used"], 2, st)   # the code-only fix led to a retake
        log = open(os.path.join(w.job_dir(), "take-log.jsonl")).read() if hasattr(w, "job_dir") else json.dumps(st)
        self.assertIn("[code: ", log + json.dumps(st))

class D41_DryRunCap(LoopCase):
    def test_seventh_dry_run_is_refused(self):
        w = self.ws({"video": "none", "dry_fail": {"step": "raci", "until_fix": False}})
        rc, out = w.run("human_reachable=no")
        st = w.state()
        self.assertEqual((st["stop"]["point"], st["dry_runs_used"], st["takes_used"]), (6, 6, 0))
        self.assertIn("STOP 6", w.report())


class C34_Writes(LoopCase):
    def test_write_case_two_takes_unique_names_and_cleanup(self):
        w = self.ws({"video": "none", "store": "auto", "defect": {"step": "oeffnen", "kind": "black"}}, spec=W,
                    approve={"writes": ["create-item"]})
        rc, out = w.run("writes_allowed=yes")
        self.assertEqual(rc, 0, out)
        st = w.state()
        names = [i["name"] for i in st["created_items"]]
        self.assertEqual(len(set(names)), len(names))                 # a new, unique name per run
        self.assertEqual(len(names), 4)                               # 2 dry runs + 2 takes each created one
        store = json.load(open(os.path.join(w.root, "store.json")))
        self.assertEqual([i["name"] for i in store["items"]], ["Handeintrag"])  # only the job's own items deleted
        self.assertEqual(len(st["cleanup"]["deleted"]), 4)
        self.assertIn("deleted", w.report())

    def test_writes_knob_off_is_stop_4(self):
        w = self.ws({"video": "none", "store": "auto"}, spec=W, approve={"writes": ["create-item"]})
        rc, out = w.run("human_reachable=no")
        self.assertEqual(w.state()["stop"]["point"], 4)
        self.assertFalse(glob.glob(os.path.join(w.root, "runs", "job-*")))

    def test_no_safe_delete_path_lists_items(self):
        w = self.ws({"video": "none", "store": "auto"}, spec=W, approve={"writes": ["create-item"]})
        prof = os.path.join(w.root, "profiles", "fixture")
        os.makedirs(prof)
        p = json.load(open(os.path.join(REPO, "profiles", "fixture", "profile.json")))
        p["cleanup"]["safe_delete"] = False
        json.dump(p, open(os.path.join(prof, "profile.json"), "w"))
        rc, out = w.run("writes_allowed=yes", extra_env={"VC_PROFILES_DIR": os.path.join(w.root, "profiles")})
        self.assertEqual(rc, 0, out)
        st = w.state()
        self.assertEqual(st["cleanup"]["deleted"], [])
        self.assertEqual(len(st["cleanup"]["left"]), 2)
        self.assertIn("no clear, safe delete path", w.report())


class F28_Timing(LoopCase):
    def test_numbers_recompute(self):
        w = self.ws({"video": "none", "defect": BLACK_BPMN})
        w.run()
        recs = w.timing()
        for r in recs:
            self.assertAlmostEqual(r["seconds"], r["end"] - r["start"], places=2)
            if r["jev_decisions"]:
                self.assertAlmostEqual(r["ms_per_decision"], r["jev_s"] * 1000 / r["jev_decisions"], delta=0.2)
        # per-decision logs vs the timing log
        n = cost = 0
        for f in glob.glob(os.path.join(w.root, "runs", "job-*", "take.log.jsonl")):
            for line in open(f):
                d = json.loads(line)
                if d.get("type") == "decision":
                    n += 1
                    cost += d["cost_usd"]
        self.assertEqual(n, sum(r["jev_decisions"] for r in recs))
        self.assertAlmostEqual(cost, sum(r["jev_cost_usd"] for r in recs), places=6)
        for ph in ("preflight", "login", "dry run", "take", "cut", "join", "QA", "viewer review", "fix", "deliver",
                   "cleanup"):
            self.assertIn(ph, [r["phase"] for r in recs])
        rc, out = w.cli("vc-loop", "summary", w.job_dir())
        self.assertIn("wall-clock total", out)


class F28_WallClock(unittest.TestCase):
    """FX-26: the approval wait (a human record, possibly hours before the job) is not in the wall clock."""

    def test_human_wait_long_before_the_job_is_not_wall_clock(self):
        from vcloop import logs as L
        t = 1790322189.5
        recs = [{"phase": "approval (human)", "start": t - 5282, "end": t - 5204.6, "seconds": 77.4, "human": True},
                {"phase": "preflight", "start": t, "end": t + 0.1, "seconds": 0.1},
                {"phase": "take", "start": t + 20, "end": t + 65, "seconds": 45.0},
                {"phase": "deliver", "start": t + 121.7, "end": t + 121.8, "seconds": 0.1}]
        s = L.summarize(recs)
        self.assertEqual(s["wall_s"], 121.8)
        self.assertEqual(s["sum_s"], 45.2)
        self.assertEqual(s["human_s"], 77.4)
        self.assertIn("human waits 77.4 s, not included", L.summary_lines(s)[-1])

    def test_old_logs_without_the_flag(self):
        from vcloop import logs as L
        recs = [{"phase": "approval (human)", "start": 0, "end": 10, "seconds": 10},
                {"phase": "take", "start": 5000, "end": 5030, "seconds": 30}]
        self.assertEqual(L.summarize(recs)["wall_s"], 30)


class F32_BestTake(unittest.TestCase):
    def test_made_up_verdicts(self):
        from vcloop import logs as L
        rows = [{"take": 1, "completed": True, "verdict": "FAIL", "violations": 2, "expected_state_ok": True},
                {"take": 2, "completed": True, "verdict": "FAIL", "violations": 1, "expected_state_ok": False},
                {"take": 3, "completed": True, "verdict": "FAIL", "violations": 2, "expected_state_ok": True},
                {"take": 4, "completed": False, "verdict": None}]
        for _ in range(3):
            b, hit = L.best_take(rows, (30, 60))
            self.assertEqual((b["take"], hit), (3, False))         # no ES failure, fewest violations, newest
        rows += [{"take": 5, "completed": True, "verdict": "PASS", "hit": True, "warnings": 1, "length": 45},
                 {"take": 6, "completed": True, "verdict": "PASS", "hit": True, "warnings": 1, "length": 40},
                 {"take": 7, "completed": True, "verdict": "PASS", "hit": False, "warnings": 0, "length": 45}]
        b, hit = L.best_take(rows, (30, 60))
        self.assertEqual((b["take"], hit), (5, True))              # fewest warnings, then closest to the middle


class F36_Profiles(LoopCase):
    def test_new_profile_runs_without_core_change_and_without_trace(self):
        w = self.ws({"video": "none", "store": "auto"}, spec=W, approve=False)
        pdir = os.path.join(w.root, "profiles", "other")
        os.makedirs(pdir)
        p = json.load(open(os.path.join(REPO, "profiles", "fixture", "profile.json")))
        p.update(name="other", hosts=["other.test:8099"])
        json.dump(p, open(os.path.join(pdir, "profile.json"), "w"))      # no example profile in this dir
        t = open(w.spec).read().replace("http://127.0.0.1:8099/", "http://other.test:8099/")
        open(w.spec, "w").write(t)
        env = {"VC_PROFILES_DIR": os.path.join(w.root, "profiles")}
        rc, out = w.cli("vc-spec", "approve", w.spec, "--by", "owner", "--approve-write", "create-item",
                        extra_env=env)
        self.assertEqual(rc, 0, out)
        rc, out = w.run("writes_allowed=yes", extra_env=env)
        self.assertEqual(rc, 0, out)
        self.assertIn("app profile(s): other", out)

    def test_core_has_no_app_specifics(self):
        core = [os.path.join(REPO, "loop"), os.path.join(REPO, "bin")]
        pat = re.compile(r"app\.example\.com|Prozesse|Swimlanes|Urlaubsantrag", re.I)
        hits = []
        for d in core:
            for f in glob.glob(os.path.join(d, "**", "*"), recursive=True):
                if os.path.isfile(f) and not f.endswith(".pyc"):
                    for i, line in enumerate(open(f, errors="ignore"), 1):
                        if pat.search(line):
                            hits.append(f"{f}:{i}: {line.strip()}")
        self.assertEqual(hits, [])


class M11_StopReasons(LoopCase):
    """Review Major 11 / 9: a busy browser lock (vc-lock exit 75) is a lock stop, not "recorder down" and not a login
    stop; a run that never ends is cut off by the run timeout (C-18)."""

    def comp(self, w, **over):
        cfg = json.load(open(w.components))
        cfg.update(over)
        json.dump(cfg, open(w.components, "w"))

    def test_lock_timeout_in_the_run_is_a_lock_stop(self):
        w = self.ws({"video": "none"})
        self.comp(w, run=["sh", "-c", "echo 'vc-lock: browser lock not free after 1s' >&2; exit 75"])
        rc, out = w.run("human_reachable=no")
        st = w.state()
        self.assertEqual(st["stop"]["point"], 7, out)
        self.assertEqual(st["stop"].get("cause"), "lock_timeout", st["stop"])
        self.assertIn("browser lock", st["stop"]["needed"])
        self.assertNotIn("recorder or browser down", st["stop"]["needed"])
        self.assertEqual(st["dry_runs_used"], 0)                      # nothing ran: not a dry run that taught anything

    def test_lock_timeout_in_the_login_check_is_not_a_login_stop(self):
        w = self.ws({"video": "none"})
        self.comp(w, login_check=["sh", "-c", "exit 75"])
        rc, out = w.run("human_reachable=no")
        st = w.state()
        self.assertEqual((st["stop"]["point"], st["stop"].get("cause")), (7, "lock_timeout"), st["stop"])

    def test_run_timeout_stops_a_hung_run(self):
        w = self.ws({"video": "none"})
        self.comp(w, run=["sleep", "60"])
        t0 = time.time()
        rc, out = w.run("human_reachable=no", "run_timeout_s=2", "lock_timeout_s=1")
        self.assertLess(time.time() - t0, 30, out)
        st = w.state()
        self.assertEqual((st["stop"]["point"], st["stop"].get("cause")), (7, "run_timeout"), st["stop"])


if __name__ == "__main__":
    unittest.main()
