"""vc-lock (C-18) honours VC_LOCK_TIMEOUT from the environment and the knob table (F-23). Host only, no container."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOCK = os.path.join(ROOT, "bin", "vc-lock")
sys.path.insert(0, os.path.join(ROOT, "loop"))
from vcloop.knobs import TABLE  # noqa: E402


class LockTimeoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.settings = os.path.join(d, "settings.env")
        with open(os.path.join(ROOT, "settings.example.env")) as f:
            base = [ln for ln in f if not ln.startswith(("VC_STATE_DIR=", "VC_LOCK_TIMEOUT="))]
        self.state = os.path.join(d, "state")
        with open(self.settings, "w") as f:
            f.writelines(base)
            f.write(f"VC_STATE_DIR={self.state}\n")
        self.env = {k: v for k, v in os.environ.items() if k not in ("VC_LOCK_TIMEOUT", "VC_STATE")}
        self.env["VC_SETTINGS"] = self.settings
        # hold the lock for 30 s in the background
        self.holder = subprocess.Popen([LOCK, "holder", "sleep", "30"], env=self.env)
        for _ in range(100):
            log = os.path.join(self.state, "lock.log")
            if os.path.exists(log):
                with open(log) as f:
                    if "acquired" in f.read():
                        break
            time.sleep(0.05)

    def tearDown(self):
        self.holder.kill()
        self.holder.wait()
        self.tmp.cleanup()

    def waiter(self, env_extra=None, settings_line=None):
        if settings_line:
            with open(self.settings, "a") as f:
                f.write(settings_line + "\n")
        env = dict(self.env, **(env_extra or {}))
        t = time.monotonic()
        p = subprocess.run([LOCK, "waiter", "true"], env=env, capture_output=True, text=True, timeout=60)
        return p.returncode, time.monotonic() - t, p.stderr

    def test_env_timeout_wins(self):
        rc, dt, err = self.waiter({"VC_LOCK_TIMEOUT": "1"}, settings_line="VC_LOCK_TIMEOUT=20")
        self.assertEqual(rc, 75, err)
        self.assertLess(dt, 5)
        self.assertIn("after 1s", err)
        with open(os.path.join(self.state, "lock.log")) as f:
            events = [json.loads(ln)["event"] for ln in f]
        self.assertIn("timeout", events)

    def test_settings_line_used_without_env(self):
        rc, dt, err = self.waiter(settings_line="VC_LOCK_TIMEOUT=1")
        self.assertEqual(rc, 75, err)
        self.assertLess(dt, 5)

    def show(self, env_extra=None):
        env = dict(self.env, **(env_extra or {}))
        return subprocess.run([LOCK, "--show-timeout"], env=env, capture_output=True, text=True).stdout.strip()

    def test_precedence_env_settings_knob(self):
        self.assertEqual(self.show(), str(TABLE["lock_timeout_s"][0]))            # knob table default
        with open(self.settings, "a") as f:
            f.write("VC_LOCK_TIMEOUT=42\n")
        self.assertEqual(self.show(), "42")                                        # settings file
        self.assertEqual(self.show({"VC_LOCK_TIMEOUT": "7"}), "7")                 # environment (loop knob)
        p = subprocess.run([LOCK, "x", "true"], env=dict(self.env, VC_LOCK_TIMEOUT="abc"), capture_output=True)
        self.assertEqual(p.returncode, 64)

    def test_loop_passes_knob_as_env(self):
        from vcloop import components
        src = open(components.__file__).read()
        self.assertIn('env["VC_LOCK_TIMEOUT"] = str(self.knobs["lock_timeout_s"])', src)


if __name__ == "__main__":
    unittest.main()
