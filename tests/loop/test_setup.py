"""start.setup: an approved write that runs off camera, before the cursor and the recording (docs/spec-format.md)."""
import copy
import os
import unittest

from harness import REPO, sibling  # noqa: F401

if sibling("jev"):
    os.environ.setdefault("VC_JEV_PATH", sibling("jev"))

from vcloop import knobs as K, spec as S, take_runner as TR  # noqa: E402
from test_take_runner import FakeRecorder, FakeSession  # noqa: E402

C2 = os.path.join(REPO, "specs", "golden", "c2-find-and-read.toml")


class Setup(unittest.TestCase):
    def test_c2_setup_is_an_approved_write_and_valid(self):
        sp = S.load(C2)
        self.assertEqual([p for p in S.validate(sp) if p.severity == "error"], [])
        self.assertEqual(S.contract(sp)["setup"]["write"], "new-chat")
        self.assertTrue(S.approval_status(sp)[0])

    def test_setup_rules(self):
        sp = S.load(C2)
        bad = copy.deepcopy(sp)
        bad["start"]["setup"]["actions"][0]["op"] = "type"
        self.assertIn("F-08.actor", {p.rule for p in S.validate(bad)})
        bad = copy.deepcopy(sp)
        bad["approval"]["writes"] = []
        self.assertIn("C-34", {p.rule for p in S.validate(bad)})
        bad = copy.deepcopy(sp)
        bad["start"]["setup"]["name"] = "prozesse"                 # a step's name
        self.assertIn("F-08.name", {p.rule for p in S.validate(bad)})

    def test_no_setup_keeps_the_contract(self):
        sp = S.load(os.path.join(REPO, "specs", "golden", "c1-four-angles.toml"))
        self.assertNotIn("setup", S.contract(sp))

    @unittest.skipUnless(sibling("jev"), "needs the M2 wrapper for its exception types")
    def test_setup_runs_off_camera_with_its_write_labels(self):
        sp = S.load(C2)
        res, _ = S.resolve(sp, {"unique": "u"})
        req = {"job": "j", "run_id": "j-t1", "mode": "take", "n": 1,
               "run_dir": os.environ.get("VC_TEST_TMP", "/tmp") + "/tr-setup", "spec": S.public(res), "deny_extra": [],
               "approved_write_labels": {"neues-gespraech": ["Neues Gespräch"]}, "knobs": K.defaults()}
        log, rec = [], FakeRecorder()

        def factory(hooks):
            s = FakeSession(log)
            s.h = type("H", (), {})()
            log.append(("hooks", s.h))
            orig = s.run_step

            def run_step(step):
                log.append(("rec_calls", len(rec.calls), step["name"], step.get("approved_write_labels")))
                return orig(step)
            s.run_step = run_step
            return s
        out = TR.run(req, session_factory=factory, recorder=rec,
                     hooks_factory=lambda ev, filming: {"camera": lambda runner, a, r: {"ok": True}})
        self.assertTrue(out["ok"] and out["completed"])
        first = next(x for x in log if x[0] == "rec_calls")
        self.assertEqual(first, ("rec_calls", 0, "neues-gespraech#1", ["Neues Gespräch"]))  # before recording
        self.assertEqual(rec.calls[0], ("start",))
        self.assertEqual(out["created_items"], [])


if __name__ == "__main__":
    unittest.main()
