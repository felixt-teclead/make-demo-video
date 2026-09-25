"""Every committed spec through the real CLI: golden cases valid; fixture specs valid as drafts; case W refused before
its write is approved and accepted after (Q-91 Verify, C-34)."""
import glob
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SPEC = os.path.join(ROOT, "bin", "vc-spec")


def run(*args, env=None):
    p = subprocess.run([SPEC, *args], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


class Specs(unittest.TestCase):
    def test_golden_cases_valid(self):
        for f in sorted(glob.glob(os.path.join(ROOT, "specs", "golden", "*.toml"))):
            rc, out = run("validate", f)
            self.assertEqual(rc, 0, out)

    def test_fixture_specs_valid_as_drafts(self):
        for name in ("q55-fixture-paste", "q30-fixture-late"):
            rc, out = run("validate", os.path.join(ROOT, "specs", "examples", name + ".toml"), "--draft")
            self.assertEqual(rc, 0, out)

    def test_case_w_refused_until_the_write_is_approved(self):
        tmp = tempfile.mkdtemp()
        try:
            src = os.path.join(ROOT, "specs", "examples")
            shutil.copy(os.path.join(src, "w-fixture-write.toml"), tmp)
            if os.path.isdir(os.path.join(src, "explore", "w-fixture-write")):
                shutil.copytree(os.path.join(src, "explore", "w-fixture-write"),
                                os.path.join(tmp, "explore", "w-fixture-write"))
            spec = os.path.join(tmp, "w-fixture-write.toml")
            env = dict(os.environ, VC_RUNS_DIR=os.path.join(tmp, "runs"), VC_STATE_DIR=os.path.join(tmp, "state"))
            rc, out = run("validate", spec, env=env)
            self.assertNotEqual(rc, 0)
            self.assertIn("C-34", out)
            rc, out = run("approve", spec, "--by", "selftest-owner", "--approve-write", "create-item", env=env)
            self.assertEqual(rc, 0, out)
            rc, out = run("validate", spec, env=env)
            self.assertEqual(rc, 0, out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
