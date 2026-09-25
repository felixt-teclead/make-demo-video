"""Chrome launch config (M1) without a browser: the page area is exactly the 1920x1080 screen (fullscreen without a
window manager is 1919x1079 + a 1 px black edge; the bars go above the screen), spell check off (M3 saw a squiggle),
and no sandbox-weakening flag in the launcher (the sandbox stays on: env/compose.yaml, owner decision 2026-09-25)."""
import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "env", "lib"))
import window_fit  # noqa: E402

LAUNCH = os.path.join(ROOT, "env", "rootfs", "usr", "local", "bin", "vc-chrome")
POLICY = os.path.join(ROOT, "env", "rootfs", "etc", "opt", "chrome", "policies", "managed", "vc.json")


class FakeChrome:
    """A window manager-less Chrome as measured live: fullscreen comes out 1919x1079, a window of the screen's size is
    clamped `short` px smaller, and a normal window shows `bars` px of tab strip and toolbar above the page."""

    def __init__(self, short=1, state="fullscreen", bars=87):
        self.short, self.bars = short, bars
        self.bounds = {"left": 0, "top": 0, "width": 1919, "height": 1079, "windowState": state}

    def call(self, method, **p):
        if method == "Target.getTargetInfo":
            return {"targetInfo": {"targetId": "T"}}
        if method == "Browser.getWindowForTarget":
            return {"windowId": 1, "bounds": dict(self.bounds)}
        if method == "Browser.setWindowBounds":
            b = p["bounds"]
            if "windowState" in b:
                self.bounds["windowState"] = b["windowState"]
            else:
                clamp = self.short if (b["width"], b["height"]) == (1920, 1080) or self.short and b["top"] == 0 else 0
                self.bounds.update(left=b["left"], top=b["top"], width=b["width"] - clamp,
                                   height=b["height"] - clamp)
            return {}
        raise AssertionError(method)

    def evaluate(self, expr):
        b = self.bounds
        bars = 0 if b["windowState"] == "fullscreen" else self.bars
        return {"w": b["width"], "h": b["height"] - bars, "ow": b["width"], "oh": b["height"], "x": b["left"],
                "y": b["top"]}


class ChromeLaunch(unittest.TestCase):
    def setUp(self):
        window_fit.time.sleep = lambda s: None

    def test_window_fit_puts_the_bars_above_the_screen(self):
        ok, hist = window_fit.fit(FakeChrome(short=1))
        self.assertTrue(ok, hist)
        v = hist[-1]["view"]
        self.assertEqual((v["w"], v["h"], v["x"]), (1920, 1080, 0))
        self.assertEqual(v["y"] + v["oh"] - v["h"], 0, "the page area starts at the screen's top edge")
        self.assertEqual(hist[-1]["bounds"]["top"], -87, "tab strip and toolbar are above the screen")

    def test_window_fit_compensates_a_short_window(self):
        fake = FakeChrome(short=1)
        fake.call = (lambda orig: lambda m, **p: orig(m, **({"bounds": {**p["bounds"], "width": p["bounds"]["width"]
                     - 1}} if m == "Browser.setWindowBounds" and "width" in p.get("bounds", {}) else p)))(fake.call)
        ok, hist = window_fit.fit(fake)
        self.assertTrue(ok, hist)
        self.assertEqual(len(hist), 3, "one compensation step")

    def test_window_fit_exact_chrome_needs_one_step(self):
        ok, hist = window_fit.fit(FakeChrome(short=0))
        self.assertTrue(ok)
        self.assertEqual(len(hist), 2)

    def test_window_fit_accepts_an_exact_page_area_as_is(self):
        fake = FakeChrome(short=0, state="normal")
        fake.bounds.update(top=-87, width=1920, height=1167)
        ok, hist = window_fit.fit(fake)
        self.assertTrue(ok)
        self.assertEqual(len(hist), 1)

    def test_launch_flags(self):
        src = open(LAUNCH).read()
        self.assertIn('--window-size="$W,$H"', src)
        self.assertIn("--window-position=0,0", src)
        self.assertIn("window_fit.py", src)
        self.assertIn("--disable-spell-checking", src)
        self.assertIn('"enable_spellchecking"] = False', src)
        for bad in ("--no-sandbox", "--disable-setuid-sandbox", "seccomp", "--privileged"):
            self.assertNotIn(bad, src, "the sandbox setting is the owner's decision")

    def test_spellcheck_policy(self):
        pol = json.load(open(POLICY))
        self.assertIs(pol["SpellcheckEnabled"], False)
        self.assertIs(pol["SpellCheckServiceEnabled"], False)

    def test_compose_sandbox_profile_and_hardening(self):
        """Owner decision 2026-09-25 (option a): Chrome's sandbox stays on through a seccomp profile; nothing else
        is loosened (non-root, not privileged, no added capabilities, loopback ports)."""
        comp = open(os.path.join(ROOT, "env", "compose.yaml")).read()
        self.assertIn("- seccomp=${VC_REPO_ABS}/env/seccomp-chrome.json", comp)
        self.assertNotIn("unconfined", comp)
        for bad in ("privileged:", "cap_add", "SYS_ADMIN", "apparmor"):
            self.assertNotIn(bad, comp)
        self.assertIn('user: "${VC_UID}:${VC_GID}"', comp)
        self.assertIn('"${VC_BIND}:${VC_CONTROL_PORT}:7777"', comp)

    def test_seccomp_profile_is_default_plus_namespaces_only(self):
        prof = json.load(open(os.path.join(ROOT, "env", "seccomp-chrome.json")))
        self.assertEqual(prof["defaultAction"], "SCMP_ACT_ERRNO")
        ours = [e for e in prof["syscalls"] if e.get("comment", "").startswith("vc-v1")]
        self.assertEqual(len(ours), 1)
        self.assertEqual(sorted(ours[0]["names"]), ["clone", "clone3", "setns", "unshare"])
        self.assertEqual(ours[0]["action"], "SCMP_ACT_ALLOW")
        risky = {"mount", "umount2", "bpf", "ptrace", "init_module", "reboot", "chroot", "perf_event_open", "keyctl"}
        for e in prof["syscalls"]:
            if e is ours[0] or e.get("includes") or e["action"] != "SCMP_ACT_ALLOW":
                continue
            self.assertFalse(risky & set(e["names"]), e)

if __name__ == "__main__":
    unittest.main()
