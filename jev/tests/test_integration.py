"""Integration tests against a real Chrome and the local test page, with jev
served by an in-process stub provider (no paid calls).

Run inside the test container (see jev/tests/env/Dockerfile):
  VCJEV_CDP=http://127.0.0.1:9222 VCJEV_PAGE=http://127.0.0.1:8000 python -m unittest test_integration
Skipped when VCJEV_CDP is not set.
"""

import copy
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CDP = os.environ.get("VCJEV_CDP")
PAGE = os.environ.get("VCJEV_PAGE", "http://127.0.0.1:8000")
CFG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.json"


class Stub:
    """A provider stub: answers TypeSafe-format questions and OpenAI chat calls."""

    def __init__(self, port):
        self.requests, self.script = [], ["act"]
        stub = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append({"path": self.path, "auth": bool(self.headers.get("Authorization")),
                                      "model": body.get("model")})
                if self.path.endswith("/systemone"):
                    out = stub.answer(body)
                else:
                    out = {"choices": [{"message": {"content": json.dumps({"text": "stub"})}}],
                           "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, body):
        what = self.script[0] if len(self.script) == 1 else self.script.pop(0)
        ops = list(body["questions"]["operation"]["criteria"])
        op = {"act": next(o for o in ops if o in ("CLICK", "TYPE_TEXT", "SELECT")), "wait": "WAIT",
              "done": "DONE"}[what]
        answers = {"operation": {"choice": op, "probabilities": {o: float(o == op) for o in ops}, "confidence": 0.9}}
        for q, spec in body["questions"].items():
            if q.endswith("_target"):
                keys = list(spec["criteria"])
                answers[q] = {"choice": keys[0], "probabilities": {k: float(k == keys[0]) for k in keys},
                              "confidence": 0.9}
        return {"answers": answers, "model": body["model"], "usage": {"input_tokens": 900, "output_tokens": 60}}

    def close(self):
        self.server.shutdown()


@unittest.skipUnless(CDP, "needs VCJEV_CDP (a Chrome DevTools URL)")
class IntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        d = Path(cls.dir.name)
        cfg = json.loads(CFG_PATH.read_text())
        cls.port = 7819
        cfg["provider"] = "stub"
        cls.cfg_path = d / "models.json"
        cls.cfg_path.write_text(json.dumps(cfg))
        cls.env = d / "env"
        cls.env.write_text("STUB_API_KEY=stub-key\n")
        cls.stub = Stub(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.stub.close()
        cls.dir.cleanup()

    def setUp(self):
        from vcjev.session import open_session
        self.stub.requests.clear()
        self.stub.script = ["act"]
        self.events = []
        from vcjev.runner import Hooks, Settings
        hooks = Hooks(before_input=lambda a, p: self.events.append(("glide", p)),
                      after_input=lambda a, p, i: self.events.append(("ripple", p)))
        self.log = Path(self.dir.name) / f"{self._testMethodName}.jsonl"
        self.runner = open_session(cdp_url=CDP, env_file=self.env, log_path=self.log, job="it",
                                   models_config=self.cfg_path, hooks=hooks,
                                   settings=Settings(settle_s=1.5, ready_timeout_s=3))
        self.tab = self.runner.tab
        self.tab.load_start_url(PAGE + "/index.html")

    def tearDown(self):
        self.tab.close()

    def records(self, type_="decision"):
        return [json.loads(line) for line in self.log.read_text().splitlines() if json.loads(line)["type"] == type_]

    # C-04 (b)
    def test_provider_swap_redirects_jev_and_text_helper_to_stub(self):
        from vcjev import upstream
        upstream.ROUTE.calls.clear()
        r = self.runner.run_step({"name": "details", "op": "click", "target": {"label": "Details anzeigen"},
                                  "check": {"type": "dialog_open"}})
        self.assertTrue(r.ok)
        value, helper = upstream.field_text({"goal": "x", "field": {}, "page": {}, "recent_actions": []})
        self.assertEqual(value, "stub")
        paths = [q["path"] for q in self.stub.requests]
        self.assertEqual(paths, ["/v1/systemone", "/v1/chat/completions"])
        self.assertTrue(all(c["url"].startswith(f"http://127.0.0.1:{self.port}/") for c in upstream.ROUTE.calls))
        self.assertEqual(self.stub.requests[0]["model"], "typesafe/jev-1.13")
        self.assertEqual(self.stub.requests[1]["model"], "inception/mercury-2.5")
        d = self.records()[0]
        self.assertEqual(d["cost_source"], "computed")  # stub returns no cost -> tokens x configured price
        self.assertAlmostEqual(d["cost_usd"], 900 * 0.042 / 1e6)

    # C-07
    def test_one_tab_and_recording_viewport(self):
        vp = self.tab.viewport()
        self.assertEqual(len(self.tab.client.page_targets()), 1)
        self.runner.run_step({"name": "details", "op": "click", "target": {"label": "Details anzeigen"},
                              "check": {"type": "dialog_open"}})
        self.assertEqual(self.tab.assert_filmed_tab(vp), vp)
        want = os.environ.get("VCJEV_VIEWPORT")  # e.g. 1920x1080: the recording viewport
        if want:
            self.assertEqual(vp, tuple(int(x) for x in want.split("x")))

    # C-02: element_visible's role is the ARIA role jev reports, implicit ones included (a[href] = link)
    def test_element_visible_uses_implicit_roles(self):
        ok = lambda c: self.tab.check([{"type": "element_visible", **c}])["ok"]  # noqa: E731
        self.assertTrue(ok({"role": "link", "label": "Übersicht"}))
        self.assertFalse(ok({"role": "button", "label": "Übersicht"}))
        self.assertTrue(ok({"role": "row", "label_contains": "Erster Eintrag"}))    # explicit role still counts

    # C-01 / C-08 / OD-17 on the real DOM
    def test_filter_on_real_page(self):
        from vcjev.offer import DenyList, ResolveError, offered_set
        page = self.tab.observe(screenshot=False)
        acts = self.tab.enrich(page["actions"])
        row = offered_set(acts, "r", {"label": "Erster Eintrag", "role": "link"}, "click", DenyList())
        self.assertEqual(len(row), 2)
        self.assertIn("Löschen", row[0]["label"])  # upstream label includes the nested button text...
        self.assertEqual(row[0]["own_label"], "Erster Eintrag")  # ...its own label does not
        for label, kind in (("Öffnen", "many"), ("Löschen", "denied"), ("Delete", "denied"), ("Teilen", "denied"),
                            ("Abmelden", "denied"), ("Nichts", "none")):
            with self.subTest(label=label), self.assertRaises(ResolveError) as cm:
                offered_set(acts, "r", {"label": label}, "click", DenyList())
            self.assertEqual(cm.exception.kind, kind)
        self.assertIn("click Delete now", page["text"])

    # C-09
    def test_click_point_known_before_click_matches_real_click(self):
        self.tab.js("window.__clicks=[];document.addEventListener('mousedown',e=>__clicks.push([e.clientX,e.clientY]),true)")
        self.runner.run_step({"name": "details", "op": "click", "target": {"label": "Details anzeigen"},
                              "check": {"type": "dialog_open"}})
        glide = [p for k, p in self.events if k == "glide"][-1]
        clicks = self.tab.js("__clicks")
        self.assertEqual(len(clicks), 1)
        self.assertLessEqual(abs(clicks[0][0] - glide["x"]), 1)
        self.assertLessEqual(abs(clicks[0][1] - glide["y"]), 1)
        self.assertEqual([k for k, _ in self.events], ["glide", "ripple"])

    # C-02 on a real page: DONE while the check is false
    def test_done_with_false_check_fails(self):
        from vcjev.runner import StepFailed
        self.stub.script = ["act", "done"]
        with self.assertRaises(StepFailed) as cm:
            self.runner.run_step({"name": "falsch", "op": "click", "target": {"label": "Details anzeigen"},
                                  "check": {"type": "url_contains", "value": "#nie"}})
        self.assertIn("DONE", cm.exception.reason)

    # C-03 on a real page
    def test_cap_on_real_page(self):
        from vcjev.runner import StepFailed
        self.stub.script = ["wait"]
        t = time.monotonic()
        with self.assertRaises(StepFailed) as cm:
            self.runner.run_step({"name": "nie", "op": "click", "target": {"label": "Details anzeigen"},
                                  "check": {"type": "url_contains", "value": "#nie"}})
        self.assertIn("cap of 6", cm.exception.reason)
        self.assertEqual(len(self.records()), 6)
        self.assertLess(time.monotonic() - t, 20)

    # C-31
    def test_login_page_stops_without_decision(self):
        from vcjev.runner import LoginRequired
        self.tab.load_start_url(PAGE + "/login.html")
        with self.assertRaises(LoginRequired):
            self.runner.run_step({"name": "x", "op": "click", "target": {"label": "Einloggen"},
                                  "check": {"type": "url_contains", "value": "#"}})
        self.assertEqual(self.stub.requests, [])

    # typing hook (default typist) and late control (readiness wait)
    def test_type_and_late_control(self):
        r = self.runner.run_step({"name": "suche", "op": "type", "target": {"label": "Suche"},
                                  "text": "Zweiter   Eintrag", "submit": True,
                                  "check": {"type": "text_visible", "value": "Treffer: Zweiter Eintrag"}})
        self.assertTrue(r.ok)
        self.assertEqual(self.tab.js("document.getElementById('q').value"), "Zweiter Eintrag")
        self.tab.load_start_url(PAGE + "/index.html?late")
        r = self.runner.run_step({"name": "spaeter", "op": "click", "target": {"label": "Später"},
                                  "check": {"type": "text_visible", "value": "Später geklickt"}})
        self.assertTrue(r.ok)
        waits = self.records("readiness_wait")
        self.assertTrue(waits and waits[-1]["ok"])


if __name__ == "__main__":
    unittest.main()
