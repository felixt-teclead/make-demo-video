"""Recorder storage rules that need no container: page URLs are stored redacted (C-31). Host only."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "env", "recorder"))
import recorder as R  # noqa: E402


class UrlRedaction(unittest.TestCase):
    def test_mark_url_is_redacted_in_events_and_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            R.RUNS = d
            rec = R.Recording("r1", "r1")
            os.makedirs(rec.dir)
            rec.t0 = 1000.0
            url = ("https://app.test/cb?code=SECRETCODE&view=list&state=S1&id_token=IDT"
                   "#access_token=AT123&token_type=bearer&refresh_token=RT")
            rec.add({"type": "mark", "name": "s1", "step": 1, "t": 1001.0, "url": url})
            rec.add({"type": "event", "t": 1001.5, "url": "https://u:pw@app.test/x?token=T9&a=1", "href": "/y?apikey=K"})
            stored = open(os.path.join(rec.dir, "events.jsonl")).read()
            m = json.dumps(rec.manifest(60, 2.0, 1002.0, {}))
            for secret in ("SECRETCODE", "IDT", "AT123", "RT", "T9", "pw", "=K", "S1"):
                self.assertNotIn(secret, stored)
                self.assertNotIn(secret, m)
            ev = json.loads(stored.splitlines()[0])
            self.assertIn("view=list", ev["url"])                    # harmless parameters stay readable
            self.assertIn("token_type=bearer", ev["url"])
            self.assertTrue(ev["url"].startswith("https://app.test/cb?"))


class LoginFailsClosed(unittest.TestCase):
    def test_unknown_recorder_status_refuses_the_login(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            prof = os.path.join(d, "login.json")
            with open(prof, "w") as f:
                json.dump({"start_url": "http://127.0.0.1:9/", "credentials": {"user_var": "U", "pass_var": "P"},
                           "form": {"user": "#u", "password": "#p"}}, f)
            env = dict(os.environ, VC_RECORDER_URL="http://127.0.0.1:9", VC_CDP_URL="http://127.0.0.1:9")
            p = subprocess.run([sys.executable, os.path.join(ROOT, "env", "login", "scripted_login.py"), "--profile",
                                prof], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 22, p.stdout + p.stderr)
            self.assertIn("recorder status unknown", p.stdout)


class SupervisorVncPassword(unittest.TestCase):
    def test_vnc_password_is_never_a_command_argument(self):
        src = open(os.path.join(ROOT, "env", "rootfs", "usr", "local", "bin", "vc-supervisor")).read()
        self.assertNotIn('"$(cat /state/vnc-password)"', src)
        self.assertNotIn("-storepasswd", src)


if __name__ == "__main__":
    unittest.main()
