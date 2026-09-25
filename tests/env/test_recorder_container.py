"""The REAL recorder, supervisor and browser lock in the recorder image (C-16, C-18). Needs docker and the image.

Not part of bin/vc-selftest (nothing there starts a container). Run by hand:

    python3 -m unittest tests.env.test_recorder_container      # from the repo root

Every test starts its own throw-away container from the image with THIS checkout mounted at /opt/vc (as compose does)
and this checkout's vc-supervisor as the entrypoint, so the code under test is the working tree, not the image copy.
Chrome is not needed: Xvfb and ffmpeg record a blank screen. Skips cleanly without docker or without the image.

Settings (environment): VC_TEST_IMAGE (default vc-v1-recorder:latest), VC_TEST_CONTAINER (vcfix-rec-test),
VC_TEST_PORT (7811, loopback only), VC_TEST_CPUSET (8-15).
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "loop"))
from vcloop import components as C, knobs as K, take_runner as TR  # noqa: E402

IMAGE = os.environ.get("VC_TEST_IMAGE", "vc-v1-recorder:latest")
NAME = os.environ.get("VC_TEST_CONTAINER", "vcfix-rec-test")
PORT = int(os.environ.get("VC_TEST_PORT", "7811"))
CPUSET = os.environ.get("VC_TEST_CPUSET", "8-15")
URL = f"http://127.0.0.1:{PORT}"
FFPROBE = shutil.which("ffprobe") or os.path.expanduser("~/.local/bin/ffprobe")


def _docker_ok():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0


def http(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(URL + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


@unittest.skipUnless(_docker_ok(), f"needs docker and the image {IMAGE}")
class ContainerCase(unittest.TestCase):
    env = {}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vc-rec-")
        self.runs, self.state = os.path.join(self.tmp, "runs"), os.path.join(self.tmp, "state")
        os.makedirs(self.runs)
        os.makedirs(self.state)
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
        cmd = ["docker", "run", "-d", "--name", NAME, "--init", "--cpuset-cpus", CPUSET, "--shm-size", "512m",
               "-u", f"{os.getuid()}:{os.getgid()}", "-p", f"127.0.0.1:{PORT}:7777",
               "-v", f"{self.runs}:/runs", "-v", f"{self.state}:/state", "-v", f"{ROOT}:/opt/vc:ro",
               "--entrypoint", "/bin/bash"]
        for k, v in self.env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [IMAGE, "/opt/vc/env/rootfs/usr/local/bin/vc-supervisor"]
        subprocess.run(cmd, check=True, capture_output=True)
        self.addCleanup(self._cleanup)
        for _ in range(150):
            try:
                if http("GET", "/record/status", timeout=2)[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(0.2)
        else:
            self.fail("recorder API did not come up: " + self.dexec("sh", "-c", "tail -n 30 /tmp/vc-logs/*.log"))

    def _cleanup(self):
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def dexec(self, *args):
        return subprocess.run(["docker", "exec", NAME] + list(args), capture_output=True, text=True).stdout

    def playable(self, mp4):
        p = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                            "stream=nb_read_packets,width,height", "-of", "json", mp4], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        st = json.loads(p.stdout)["streams"][0]
        return int(st["nb_read_packets"]), st["width"], st["height"]


class C1_RunDirFromTheLoop(ContainerCase):
    """C1: the loop creates runs/<run_id>/ and writes request.json before the take; the recorder must film into it."""

    def test_start_accepts_a_run_dir_that_holds_only_the_request(self):
        rd = os.path.join(self.runs, "job-t1")
        os.makedirs(rd)
        with open(os.path.join(rd, "request.json"), "w") as f:
            json.dump({"run_id": "job-t1"}, f)
        code, body = http("POST", "/record/start", {"run_id": "job-t1", "label": "job-t1"})
        self.assertEqual(code, 200, body)
        time.sleep(1.0)
        code, m = http("POST", "/record/stop", {})
        self.assertEqual(code, 200, m)
        self.assertTrue(os.path.exists(os.path.join(rd, "manifest.json")))
        self.assertTrue(os.path.exists(os.path.join(rd, "request.json")))
        n, w, h = self.playable(os.path.join(rd, "raw.mp4"))
        self.assertGreater(n, 15)

    def test_a_run_dir_with_a_recording_is_still_refused(self):     # Q-58: a recut needs a new run folder
        rd = os.path.join(self.runs, "job-t2")
        os.makedirs(rd)
        with open(os.path.join(rd, "manifest.json"), "w") as f:
            f.write("{}")
        code, body = http("POST", "/record/start", {"run_id": "job-t2"})
        self.assertEqual(code, 409, body)


class VncPassword(ContainerCase):
    def test_remote_view_runs_and_its_password_is_in_no_command_line(self):
        pw = open(os.path.join(self.state, "vnc-password")).read().strip()
        self.assertGreaterEqual(len(pw), 8)
        for _ in range(50):
            if self.dexec("pgrep", "-x", "x11vnc").strip():
                break
            time.sleep(0.1)
        self.assertTrue(self.dexec("pgrep", "-x", "x11vnc").strip(), self.dexec("cat", "/tmp/vc-logs/x11vnc.log"))
        time.sleep(1.0)
        self.assertTrue(self.dexec("pgrep", "-x", "x11vnc").strip(), self.dexec("cat", "/tmp/vc-logs/x11vnc.log"))
        self.assertNotIn(pw, self.dexec("ps", "-eo", "args"))


class Major10_HungFfmpeg(ContainerCase):
    env = {"VC_REC_STOP_WAIT_S": "2"}

    def test_stop_kills_ffmpeg_that_does_not_exit(self):
        code, body = http("POST", "/record/start", {"run_id": "hung"})
        self.assertEqual(code, 200, body)
        time.sleep(1.0)
        self.dexec("pkill", "-STOP", "-x", "ffmpeg")                 # ffmpeg hangs: ignores q and SIGINT
        code, body = http("POST", "/record/stop", {}, timeout=90)
        self.assertNotEqual(code, 200)
        self.assertEqual(self.dexec("pgrep", "-x", "ffmpeg").strip(), "", "ffmpeg still runs after stop")
        self.assertEqual(http("GET", "/record/status")[1].get("recording"), None)
        code, body = http("POST", "/record/start", {"run_id": "after-hung"})     # the recorder is usable again
        self.assertEqual(code, 200, body)
        http("POST", "/record/stop", {})


class Major10_GracefulShutdown(ContainerCase):
    def test_docker_stop_finalises_the_running_recording(self):
        code, body = http("POST", "/record/start", {"run_id": "shutdown"})
        self.assertEqual(code, 200, body)
        for i in range(3):
            http("POST", "/mark", {"name": f"s{i + 1}", "step": i + 1})
            time.sleep(2.0)
        # the encoder lags a few seconds behind (a busy host): ffmpeg needs ~4 s to finish after "q"
        self.dexec("pkill", "-STOP", "-x", "ffmpeg")
        lag = subprocess.Popen(["docker", "exec", NAME, "sh", "-c", "sleep 4; pkill -CONT -x ffmpeg"])
        t0 = time.time()
        subprocess.run(["docker", "stop", "-t", "30", NAME], check=True, capture_output=True)
        lag.wait()
        self.assertLess(time.time() - t0, 30)
        rd = os.path.join(self.runs, "shutdown")
        self.assertTrue(os.path.exists(os.path.join(rd, "manifest.json")), os.listdir(rd))
        with open(os.path.join(rd, "manifest.json")) as f:
            m = json.load(f)
        n, w, h = self.playable(os.path.join(rd, "raw.mp4"))
        self.assertEqual(n, m["frames"])
        self.assertGreater(n, 5 * 30)
        self.assertEqual(len(m["marks"]), 3)


class C18_StaleRecording(ContainerCase):
    def request(self, run_id):
        rd = os.path.join(self.runs, run_id)
        spec = {"preconditions": [], "start": {"url": "http://127.0.0.1:8099/x", "ready": [], "warm": [], "hold": 0.1},
                "steps": [{"name": "s1", "actions": [], "expect": [], "hold": 0.1}]}
        return {"job": "j", "run_id": run_id, "mode": "take", "n": 2, "run_dir": rd, "spec": spec,
                "deny_extra": [], "approved_write_labels": {}, "knobs": K.defaults()}

    def test_orphaned_recording_is_stopped_before_the_next_take(self):
        code, body = http("POST", "/record/start", {"run_id": "j-t1"})    # a killed take left its recording running
        self.assertEqual(code, 200, body)

        class Tab:
            def load_start_url(self, u):
                pass

            def js(self, e):
                return None

            def login_form(self):
                return False

        class Session:
            tab = Tab()

            def check(self, checks, seconds):
                return True

        res = TR.run(self.request("j-t2"), session_factory=lambda hooks: Session(), recorder=TR.Recorder(URL))
        self.assertTrue(res["completed"], res)
        self.assertTrue(os.path.exists(os.path.join(self.runs, "j-t1", "manifest.json")))   # the orphan was finalised
        self.assertTrue(os.path.exists(os.path.join(self.runs, "j-t2", "manifest.json")))
        self.assertEqual(res.get("stale_recording_stopped"), "j-t1")


class C18_LockLivesInTheContainer(ContainerCase):
    """The browser lock must be held by the process that uses the browser, not by the host's docker exec client:
    killing the host side (loop killed, run timeout) must not free the lock while the in-container runner goes on."""

    def test_killing_the_host_client_keeps_the_lock_until_the_runner_ends(self):
        settings = os.path.join(self.tmp, "settings.env")
        with open(os.path.join(ROOT, "settings.example.env")) as f:
            base = [ln for ln in f if not ln.startswith(("VC_STATE_DIR=", "VC_RUNS_DIR=", "VC_CONTAINER_NAME=",
                                                         "VC_UID=", "VC_GID=", "VC_ENV_FILE=", "VC_LOCK_TIMEOUT="))]
        with open(settings, "w") as f:
            f.writelines(base)
            f.write(f"VC_STATE_DIR={self.state}\nVC_RUNS_DIR={self.runs}\nVC_CONTAINER_NAME={NAME}\n"
                    f"VC_UID={os.getuid()}\nVC_GID={os.getgid()}\nVC_ENV_FILE={self.tmp}/no-env\n")
        with open(os.path.join(ROOT, "config", "components.example.json")) as f:
            cfg = json.load(f)
        comp = C.Components(cfg, K.defaults(), self.state, os.path.join(self.tmp, "logs"))
        seen = []
        comp._run = lambda name, cmd, label, timeout=None: (seen.append(cmd), (0, ""))[1]
        comp.call("run", {"request": "x", "request_c": "/runs/x/request.json", "run_dir": "x"}, label="orphan",
                  browser=True)
        cmd = seen[0]
        i = cmd.index("vcloop.take_runner") - 2                        # python3 -m vcloop.take_runner REQUEST
        cmd = cmd[:i] + ["sleep", "40"]
        env = {k: v for k, v in os.environ.items() if k not in ("VC_STATE", "VC_LOCK_TIMEOUT")}
        env.update(VC_SETTINGS=settings, VC_LOCK_TIMEOUT="600")
        p = subprocess.Popen(cmd, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log = os.path.join(self.state, "lock.log")
        for _ in range(200):
            if os.path.exists(log):
                with open(log) as f:
                    if '"acquired","label":"orphan"' in f.read():
                        break
            time.sleep(0.05)
        else:
            p.kill()
            self.fail("the run never took the lock")
        os.killpg(p.pid, signal.SIGKILL)                               # the loop (and its docker exec client) dies
        p.wait()
        time.sleep(0.5)
        probe = subprocess.run([os.path.join(ROOT, "bin", "vc-lock"), "probe", "true"],
                               env=dict(env, VC_LOCK_TIMEOUT="2"), capture_output=True, text=True)
        self.assertEqual(probe.returncode, 75, "the lock was freed while the in-container runner still runs")


if __name__ == "__main__":
    unittest.main()
