"""F-28 accounting and the C-04 models config."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from vcjev import models as M
from vcjev.accounting import BudgetExceeded, DecisionLog, cost, phase_record, summarize

CFG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.json"


def dec(latency, usage):
    return {"choice": "e1", "operation": "CLICK", "probabilities": {"e1": 1}, "confidence": 1, "latency_ms": latency,
            "usage": usage, "model": "x"}


class AccountingTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "log.jsonl"

    def tearDown(self):
        self.dir.cleanup()

    def test_provider_cost_wins(self):
        self.assertEqual(cost({"input_tokens": 1000, "output_tokens": 10, "cost": 0.123}, (1, 1)), (0.123, "provider"))

    def test_computed_cost_when_provider_has_none(self):
        usd, src = cost({"prompt_tokens": 1_000_000, "completion_tokens": 500_000}, (0.042, 0.2))
        self.assertEqual(src, "computed")
        self.assertAlmostEqual(usd, 0.042 + 0.1)

    def test_summary_recomputes_from_log(self):
        log = DecisionLog(self.path, job="j", phase="dry run")
        m = {"model": "m", "price": (0.042, 0.0)}
        log.decision(step="a", n=1, offered=[], decision=dec(100, {"input_tokens": 1000, "output_tokens": 5,
                                                                    "cost": 0.001}), model_cfg=m)
        log.context(phase="take", take=1)
        log.decision(step="a", n=1, offered=[], decision=dec(300, {"input_tokens": 2_000_000, "output_tokens": 0}),
                     model_cfg=m)
        log.text_call(step="b", latency_ms=50, model="t", usage={"prompt_tokens": 10, "completion_tokens": 2})
        recs = log.records()
        s = summarize(recs)
        self.assertEqual(s["decisions"], 2)
        self.assertEqual(s["ms_per_decision"], 200.0)
        self.assertAlmostEqual(s["jev_cost_usd"], 0.001 + 0.084)
        self.assertEqual(s["cost_sources"], ["computed", "provider"])
        by = summarize(recs, "phase")
        self.assertEqual(by["dry run"]["decisions"], 1)
        self.assertEqual(by["take"]["jev_seconds"], 0.3)
        pr = phase_record(recs, job="j", phase="take", start=10.0, end=12.5, take=1)
        self.assertEqual((pr["seconds"], pr["jev_decisions"], pr["ms_per_decision"]), (2.5, 1, 300.0))
        self.assertIsNone(pr["agent_cost_usd"])  # never guessed

    def test_budget(self):
        log = DecisionLog(self.path, job="j", budget_usd=0.001)
        log.check_budget()
        log.decision(step="a", n=1, offered=[], decision=dec(1, {"cost": 0.002}), model_cfg={"model": "m",
                                                                                              "price": (0, 0)})
        with self.assertRaises(BudgetExceeded):
            log.check_budget()


class ModelsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = Path(self.dir.name) / "env"
        self.env.write_text("# test\nOPENROUTER_API_KEY=dummy-value-1\nexport STUB_API_KEY='stub-key'\n")
        os.chmod(self.env, 0o600)
        self.cfg = M.load(CFG_PATH)

    def tearDown(self):
        self.dir.cleanup()

    def test_default_resolution_and_no_key_in_public(self):
        r = M.resolve(self.cfg, env_file=self.env)
        self.assertEqual(r["jev"]["model"], "typesafe/jev-1.13")
        self.assertEqual(r["text"]["model"], "inception/mercury-2.5")
        self.assertEqual(r["jev"]["url"], "https://openrouter.ai/api/v1/systemone")
        self.assertEqual(r["jev"]["key"], "dummy-value-1")
        self.assertNotIn("dummy-value-1", json.dumps(M.public(r)))

    def test_provider_swap_by_config(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["provider"] = "stub"
        r = M.resolve(cfg, env_file=self.env)
        self.assertTrue(r["jev"]["url"].startswith("http://127.0.0.1"))
        self.assertTrue(r["text"]["base_url"].startswith("http://127.0.0.1"))
        self.assertEqual(r["jev"]["key"], "stub-key")

    def test_missing_key_names_the_variable_not_a_value(self):
        self.env.write_text("OTHER=1\n")
        with self.assertRaises(M.ConfigError) as cm:
            M.resolve(self.cfg, env_file=self.env)
        self.assertIn("OPENROUTER_API_KEY", str(cm.exception))

    def test_upgrade_needs_passing_acceptance_then_rollback_restores(self):
        cfg = copy.deepcopy(self.cfg)
        M.add_candidate(cfg, "jev", "typesafe/jev-1.14", 0.05, 0.0)
        self.assertEqual(M.resolve(cfg, env_file=self.env)["jev"]["model"], "typesafe/jev-1.13")
        self.assertEqual(M.resolve(cfg, env_file=self.env, use_candidate=True)["jev"]["model"], "typesafe/jev-1.14")
        with self.assertRaises(M.ConfigError):
            M.promote(cfg, "jev", {"version": "typesafe/jev-1.14", "passed": False, "job_cost_usd": 0.1}, "2026-10-01")
        with self.assertRaises(M.ConfigError):
            M.promote(cfg, "jev", {"version": "typesafe/jev-1.14", "passed": True, "job_cost_usd": 1.5}, "2026-10-01")
        M.promote(cfg, "jev", {"version": "typesafe/jev-1.14", "passed": True, "job_cost_usd": 0.02}, "2026-10-01")
        self.assertEqual(cfg["jev"]["default"], "typesafe/jev-1.14")
        self.assertEqual(cfg["jev"]["versions"]["typesafe/jev-1.14"]["verified"], "2026-10-01")
        M.rollback(cfg, "jev")
        self.assertEqual(cfg["jev"]["default"], "typesafe/jev-1.13")
        # the newer one stays listed and selectable
        self.assertEqual(M.resolve(cfg, env_file=self.env, jev_version="typesafe/jev-1.14")["jev"]["model"],
                         "typesafe/jev-1.14")

    def test_save_roundtrip_via_cli(self):
        from vcjev.__main__ import main
        p = Path(self.dir.name) / "models.json"
        p.write_text(CFG_PATH.read_text())
        acc = Path(self.dir.name) / "acc.json"
        acc.write_text(json.dumps({"version": "typesafe/jev-9", "passed": True, "job_cost_usd": 0.3}))
        main(["--config", str(p), "models", "candidate", "--version", "typesafe/jev-9", "--price-in", "0.1",
              "--price-out", "0"])
        main(["--config", str(p), "models", "promote", "--acceptance", str(acc)])
        self.assertEqual(M.load(p)["jev"]["default"], "typesafe/jev-9")
        main(["--config", str(p), "models", "rollback"])
        self.assertEqual(M.load(p)["jev"]["default"], "typesafe/jev-1.13")

    def test_config_has_no_code_defaults(self):
        # C-04: no endpoint, version or price literal in the code.
        src = "".join(p.read_text() for p in (Path(M.__file__).parent).glob("*.py"))
        for literal in ("openrouter.ai", "jev-1.13", "mercury", "0.042"):
            self.assertNotIn(literal, src)


if __name__ == "__main__":
    unittest.main()
