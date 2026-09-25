"""Variant fixes (owner 2026-09-25): the fixer lists candidates, jev dry runs rank them, the close ones are filmed,
gate + frame check + viewer review pick. Stub jev results (tests/fakes/fake_runner.py "jev" rules), stub fixer, no
video. Run: cd tests/loop && python3 -m unittest test_variants"""
import json
import os
import re
import tempfile
import unittest

from harness import Workspace

from vcloop import variants as VW

C1 = "tests/loop/golden-frozen/c1-four-angles.toml"
BLACK_BPMN = {"step": "bpmn", "kind": "black", "fixable": True}
# jev struggles on bpmn unless the step waits for readiness: variant A (wait_before) scores clearly above B (hold)
JEV_PREFERS_WAIT = [{"step": "bpmn", "unless": "wait_before", "redecisions": 2, "confidence": 0.5, "transients": 1}]
UNATT = "human_reachable=no"


class VariantCase(unittest.TestCase):
    def ws(self, scenario):
        w = Workspace(C1, dict({"video": "none", "defect": BLACK_BPMN}, **scenario))
        self.addCleanup(w.cleanup)
        rc, out = w.approve()
        self.assertEqual(rc, 0, out)
        return w

    def takes(self, w):
        return [r for r in w.take_log() if "take" in r]

    def events(self, w):
        return [r for r in w.take_log() if r.get("event") == "variants"]


class Scoring(unittest.TestCase):
    """The score comes only from the jev log of a dry run (real M2 record shapes)."""

    def log(self, recs):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        with open(os.path.join(d, "take.log.jsonl"), "w") as f:
            for r in recs:
                f.write(json.dumps(r) + "\n")
        return d

    def test_signals_from_real_record_shapes(self):
        recs = [{"type": "step_start", "step": "a#1"},
                {"type": "decision", "step": "a#1", "confidence": 0.97, "probability": 1, "executed": True},
                {"type": "step_end", "step": "a#1", "ok": True, "decisions": 1},
                {"type": "step_start", "step": "b#1"},
                {"type": "transient", "step": "b#1", "kind": "stale"},
                {"type": "decision", "step": "b#1", "confidence": 0.5, "probability": 1, "executed": False},
                {"type": "decision", "step": "b#1", "confidence": 0.51, "probability": 1, "executed": True},
                {"type": "decision", "step": "b#1", "confidence": 0.47, "probability": 1, "executed": True},
                {"type": "step_end", "step": "b#1", "ok": True, "decisions": 3},
                {"type": "step_start", "step": "c#1"},
                {"type": "readiness_wait", "step": "c#1", "ok": False},
                {"type": "resolve_failed", "step": "c#1"},
                {"type": "step_end", "step": "c#1", "ok": False, "decisions": 0}]
        sig = VW.signals(self.log(recs), {"ok": False}, planned=4)
        self.assertEqual({k: sig[k] for k in ("planned", "verified", "failed", "redecisions", "first_hit",
                                              "transients", "timeouts", "decisions", "dry_ok")},
                         {"planned": 4, "verified": 2, "failed": 1, "redecisions": 2, "first_hit": 1,
                          "transients": 1, "timeouts": 2, "decisions": 3, "dry_ok": False})
        self.assertAlmostEqual(sig["confidence_mean"], (0.97 + 0.51 + 0.47) / 3, places=4)
        s, terms = VW.score(sig, VW.parse_weights(None))
        self.assertAlmostEqual(s, sum(terms.values()), places=4)
        self.assertEqual(terms["probability"], 0)                 # saturated signal: weight 0 by default
        self.assertAlmostEqual(terms["verified"], 0.5 * 2 / 4)
        self.assertAlmostEqual(terms["timeout"], -0.2)

    def test_rank_ties_keep_listing_order_and_margin(self):
        cands = [{"k": 1, "score": 0.9, "dry_ok": True}, {"k": 2, "score": 0.93, "dry_ok": True},
                 {"k": 3, "score": 0.93, "dry_ok": True}, {"k": 4, "score": 0.99, "dry_ok": False}]
        r = VW.rank(cands)
        self.assertEqual([c["k"] for c in r], [2, 3, 1])       # dry-failed never ranked; tie -> listing order
        self.assertEqual([c["k"] for c in VW.to_film(r, 0.05, 5)], [2, 3, 1])
        self.assertEqual([c["k"] for c in VW.to_film(r, 0.01, 5)], [2, 3])
        self.assertEqual([c["k"] for c in VW.to_film(r, 0.05, 1)], [2])   # take cap

    def test_pick_key(self):
        f = [{"k": 1, "hit": False, "violations": 0, "warnings": 0, "score": 1},
             {"k": 2, "hit": True, "violations": 0, "warnings": 2, "review_findings": 1, "distance": 3, "score": 0},
             {"k": 3, "hit": True, "violations": 0, "warnings": 2, "review_findings": 1, "distance": 1, "score": 0}]
        self.assertEqual(sorted(f, key=VW.pick_key)[0]["k"], 3)    # hit, then ... closest to the approved spec

    def test_weights_knob_is_checked(self):
        from vcloop import knobs as K
        with self.assertRaises(K.KnobError):
            K.resolve(None, {"variant_score_weights": "vibes=1"})
        vals, _ = K.resolve(None, {"max_variants": "2", "variant_margin": "0.1"})
        self.assertEqual((vals["max_variants"], vals["variant_margin"], vals["variant_dry_runs"]), (2, 0.1, 1))


class ClearWinner(VariantCase):
    def test_top_jev_score_is_filmed_alone(self):
        w = self.ws({"fixer": "variants", "jev": JEV_PREFERS_WAIT})
        rc, out = w.run(UNATT)
        self.assertEqual(rc, 0, out)
        st = w.state()
        self.assertEqual((st["takes_used"], st["hit_take"]), (2, 2))        # only A filmed
        self.assertEqual(st["dry_runs_used"], 3)                             # first dry + one per candidate
        req = json.load(open(os.path.join(w.job_dir(), "fixes", "request-1.json")))
        self.assertEqual(req["max_variants"], 3)
        self.assertIn("{k}", req["variant_file"])
        t2 = self.takes(w)[1]
        self.assertEqual(t2["variant"]["id"], "A")
        self.assertIn("wait_before", t2["change"][0])
        ev = self.events(w)[0]
        self.assertEqual((ev["kept"], ev["kept_take"], ev["kept_hit"]), ("A", 2, True))
        self.assertTrue(ev["lines"][0].startswith("A: score"))
        self.assertIn("re-decisions 2", ev["lines"][1])                      # B's jev breakdown is logged
        self.assertIn("plan: film A", ev["plan"])
        rep = w.report()
        self.assertIn("variants tried, jev dry-run scores", rep)
        self.assertIn("kept A (take 2", rep)
        self.assertIn("wait_before", open(w.spec).read())
        md = open(w.ledger).read()
        entry = re.search(r"^### FX-01 .*?(?=^### |\Z)", md, re.M | re.S).group(0)
        self.assertIn("variant A of fix 1", entry)
        self.assertIn("variants that lost: B: score", entry)
        self.assertIn("- evidence: variant take 2", entry)
        self.assertEqual(len(re.findall(r"(?m)^### FX-", md)), 1)            # one entry for the kept variant

    def test_attended_offers_the_ranked_variants_then_films_the_plan(self):
        w = self.ws({"fixer": "variants", "jev": JEV_PREFERS_WAIT})
        rc, out = w.run()
        self.assertEqual(rc, 20, out)
        st = w.state()
        self.assertEqual((st["stop"]["point"], st["takes_used"]), (10, 1))   # nothing filmed or applied yet
        self.assertNotIn("wait_before", open(w.spec).read())
        q = open(os.path.join(w.job_dir(), "question.md")).read()
        self.assertIn("A: score", q)
        self.assertIn("B: score", q)
        self.assertIn("variant=ID", q)
        rc, out = w.resume(answer="variant=B")                               # the human picks the weaker one
        st = w.state()
        self.assertEqual(self.takes(w)[1]["variant"]["id"], "B")
        self.assertEqual(self.events(w)[0]["kept"], "B")


class CloseScores(VariantCase):
    def test_close_variants_each_filmed_gate_picks(self):
        for mode in ("variants", "variants-hold-first"):
            with self.subTest(mode=mode):
                w = self.ws({"fixer": mode})                                 # no jev rule: equal scores
                rc, out = w.run(UNATT)
                self.assertEqual(rc, 0, out)
                st = w.state()
                rows = self.takes(w)
                self.assertEqual(st["takes_used"], 3)
                self.assertEqual({r["variant"]["id"] for r in rows[1:]}, {"A", "B"})
                ev = self.events(w)[0]
                a = "A" if mode == "variants" else "B"                       # the wait_before candidate's id
                self.assertEqual(ev["kept"], a)
                kept_take = next(r["take"] for r in rows[1:] if r["variant"]["id"] == a)
                self.assertEqual((ev["kept_take"], st["hit_take"], st["delivery"]["best_take"]),
                                 (kept_take, kept_take, kept_take))
                self.assertIn("wait_before", open(w.spec).read())            # the kept variant is the live spec
                self.assertNotIn("hold = 1.5", open(w.spec).read())
                self.assertEqual(len(ev["filmed"]), 2)
                self.assertIn("the HIT", ev["why"])

    def test_take_cap_limits_the_filmed_variants(self):
        w = self.ws({"fixer": "variants-hold-first"})
        rc, out = w.run(UNATT, "max_takes=2")
        st = w.state()
        self.assertEqual(st["takes_used"], 2)
        ev = self.events(w)[0]
        self.assertEqual([f["id"] for f in ev["filmed"]], ["A"])            # tie -> listing order; 1 take left
        self.assertIn("1 takes left", ev["plan"])


class Limits(VariantCase):
    def test_owner_only_and_contract_variants_are_dropped(self):
        w = self.ws({"fixer": "variants-rogue"})
        before_ap = re.search(r"(?ms)^\[approval\].*", open(w.spec).read()).group(0)
        rc, out = w.run(UNATT)
        self.assertEqual(rc, 0, out)
        st = w.state()
        self.assertEqual((st["takes_used"], st["dry_runs_used"]), (2, 2))    # one usable -> a plain single fix
        self.assertEqual(self.events(w), [])
        spec = open(w.spec).read()
        self.assertIn(before_ap.split("\n")[1], spec)
        self.assertNotIn('approver = "fixer"', spec)
        md = open(w.ledger).read()
        self.assertRegex(md, r"variants dropped: B \((spec invalid|changes the contract)")
        self.assertIn("C (changes the owner-only [approval] table)", md)

    def test_max_variants_drops_the_extra(self):
        w = self.ws({"fixer": "variants-many", "jev": JEV_PREFERS_WAIT})
        rc, out = w.run(UNATT)
        self.assertEqual(rc, 0, out)
        ev = self.events(w)[0]
        self.assertEqual(len(ev["lines"]), 4)
        self.assertIn("D: not scored (over max_variants=3)", ev["lines"][3])

    def test_max_variants_1_is_one_fix_only(self):
        w = self.ws({"fixer": "variants", "jev": JEV_PREFERS_WAIT})
        rc, out = w.run(UNATT, "max_variants=1")
        self.assertEqual(rc, 0, out)
        self.assertEqual(w.state()["takes_used"], 2)
        self.assertEqual(self.events(w), [])
        self.assertIn("B (over max_variants=1)", open(w.ledger).read())

    def test_dry_failed_variant_is_never_filmed(self):
        w = self.ws({"fixer": "variants-hold-first", "dry_fail": {"step": "bpmn", "until_fix": True},
                     "defect": {}})
        # the first dry run already fails at bpmn: the fixer's candidates are scored; B (hold) fails its dry run
        rc, out = w.run(UNATT)
        self.assertEqual(rc, 0, out)
        ev = self.events(w)[0]
        self.assertIn("dry FAILED", ev["lines"][0])
        self.assertEqual([f["id"] for f in ev["filmed"]], ["B"])
        self.assertEqual(ev["kept"], "B")
        self.assertEqual(w.state()["takes_used"], 1)


if __name__ == "__main__":
    unittest.main()
