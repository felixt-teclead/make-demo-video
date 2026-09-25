"""The real run component's mapping onto M2 steps and the M1 recorder, with a fake jev session and recorder."""
import json
import os
import unittest

from harness import REPO, sibling  # noqa: F401

if sibling("jev"):
    os.environ.setdefault("VC_JEV_PATH", sibling("jev"))

from vcloop import knobs as K, spec as S, take_runner as TR  # noqa: E402


class FakeTab:
    def __init__(self, log):
        self.log = log

    def js(self, expr, await_promise=False):
        return "http://app.test/view" if expr == "location.href" else None

    def load_start_url(self, url):
        self.log.append(("load", url))


class FakeSession:
    def __init__(self, log, fail_at=None, login_at=None):
        self.log, self.fail_at, self.login_at, self.tab = log, fail_at, login_at, FakeTab(log)

    def run_step(self, step):
        self.log.append(("step", step["name"], step["op"], step["target"], step.get("text"), step["check"]))
        if step["name"] == self.login_at:
            raise TR_login("login form")
        if step["name"] == self.fail_at:
            raise TR_failed(step["name"], "decision cap reached")

    def check(self, checks, seconds):
        self.log.append(("check", len(checks)))
        return True


try:
    from vcjev.runner import LoginRequired as TR_login, StepFailed as TR_failed
except Exception:  # noqa: BLE001
    TR_login, TR_failed = RuntimeError, RuntimeError


class FakeRecorder:
    def __init__(self):
        self.calls = []

    def start(self, run_id):
        self.calls.append(("start",))

    def mark(self, name, step, url=None):
        self.calls.append(("mark", name))
        self.urls = getattr(self, "urls", []) + [url]

    def hold(self, kind, seconds, step):
        self.calls.append(("hold", kind, seconds))

    def event(self, ev):
        self.calls.append(("event", ev["type"]))
        self.events = getattr(self, "events", []) + [ev]

    def stop(self):
        self.calls.append(("stop",))


def request(mode="take"):
    sp = S.load(os.path.join(REPO, "specs", "golden", "c1-four-angles.toml"))
    res, _ = S.resolve(sp, {"unique": "u"})
    return {"job": "j", "run_id": "j-t1", "mode": mode, "n": 1, "run_dir": os.environ.get("VC_TEST_TMP", "/tmp") + "/tr",
            "spec": S.public(res), "deny_extra": [], "approved_write_labels": {}, "knobs": K.defaults()}


@unittest.skipUnless(sibling("jev"), "needs the M2 wrapper for its exception types")
class TakeRunner(unittest.TestCase):
    def test_take_maps_actions_marks_and_holds(self):
        log, rec = [], FakeRecorder()
        res = TR.run(request(), session_factory=lambda hooks: FakeSession(log), recorder=rec)
        self.assertTrue(res["ok"] and res["completed"])
        steps = [x for x in log if x[0] == "step"]
        self.assertEqual(len(steps), 8)                                  # 8 jev actions in case 1
        self.assertEqual(steps[0][1:4], ("open-process#1", "click", {"label": "Urlaubsantrag", "role": "link"}))
        self.assertEqual(steps[4][5][0]["type"], "dialog_open")          # an action's done condition is its check
        marks = [c[1] for c in rec.calls if c[0] == "mark"]
        self.assertEqual(marks, ["open-process", "bpmn", "raci", "swimlanes", "step-dialog", "details", "metadaten"])
        holds = [c[1:] for c in rec.calls if c[0] == "hold"]
        self.assertEqual(holds[0], ("landing", 1.2))
        first_check = next(i for i, x in enumerate(log) if x[0] == "check")
        self.assertLess(log.index(("load", request()["spec"]["start"]["url"])), first_check)  # preconditions: start view
        self.assertIn(("dialog", 3.0), holds)
        self.assertEqual(holds[-1], ("final", 12.0))
        self.assertEqual(rec.calls[0], ("start",))
        self.assertEqual(rec.calls[-1], ("stop",))

    def test_dry_run_records_nothing(self):
        log, rec = [], FakeRecorder()
        res = TR.run(request("dry"), session_factory=lambda hooks: FakeSession(log), recorder=rec)
        self.assertTrue(res["ok"])
        self.assertEqual(rec.calls, [])

    def test_failed_step_and_login(self):
        rec = FakeRecorder()
        res = TR.run(request(), session_factory=lambda hooks: FakeSession([], fail_at="raci#1"), recorder=rec)
        self.assertEqual((res["ok"], res["completed"], res["failed_step"]), (False, False, "raci"))
        self.assertEqual(rec.calls[-1], ("stop",))
        res = TR.run(request(), session_factory=lambda hooks: FakeSession([], login_at="bpmn#1"), recorder=FakeRecorder())
        self.assertTrue(res["login_required"])


class FakeLog(list):
    """The session's step list (FakeSession appends to .log) doubling as M2's decision log (.event)."""

    def event(self, type_, **kw):
        self.append(("log", type_, kw))


@unittest.skipUnless(sibling("jev"), "needs the M2 wrapper for its exception types")
class TakeRunnerEvents(unittest.TestCase):
    """One event stream (docs/events.md): step numbers, mark URLs, readiness waits, camera, paste marking."""

    def session(self, log, hooks_seen):
        def factory(hooks):
            hooks_seen.append(hooks)
            s = FakeSession(FakeLog())
            orig = s.run_step

            def run_step(step):
                orig(step)
                s.log.event("readiness_wait", step=step["name"], start=1.79e9, end=1.79e9 + 2.5, ok=True)
                hk = hooks_seen[0]
                get = (lambda k: getattr(hk, k)) if not isinstance(hk, dict) else hk.get
                pt = {"x": 10.0, "y": 20.0}
                a = {"kind": "click", "own_label": step["target"].get("label")}
                get("before_input")(a, pt)
                if get("before_click"):
                    get("before_click")(a, pt)
                get("after_input")(a, pt, {})
            s.run_step = run_step
            return s
        return factory

    def test_events_carry_step_url_waits_and_click_fields(self):
        log, rec, seen = [], FakeRecorder(), []
        res = TR.run(request(), session_factory=self.session(log, seen), recorder=rec)
        self.assertTrue(res["ok"], res)
        self.assertTrue(all(u == "http://app.test/view" for u in rec.urls))
        clicks = [e for e in rec.events if e["type"] == "click"]
        waits = [e for e in rec.events if e["type"] == "readiness_wait"]
        self.assertEqual(len(clicks), 8)
        for c in clicks:
            for k in ("t", "glide_t", "click_t", "x", "y", "step", "label"):
                self.assertIn(k, c)
            self.assertLessEqual(c["glide_t"], c["t"])
        self.assertEqual(len(waits), 8)
        self.assertEqual(waits[0]["end"] - waits[0]["t"], 2.5)
        self.assertEqual(waits[0]["step"], 1)
        import sys
        sys.path.insert(0, REPO)
        from vc import events as E
        self.assertEqual(E.check_stream(rec.events), [])            # docs/events.md contract

    def test_camera_failure_fails_the_step_not_the_recorder(self):
        sp_req = request()
        step = sp_req["spec"]["steps"][1]
        step["actions"].insert(0, {"op": "pan", "to": {"type": "text_visible", "value": "X"}, "direction": "down"})
        res = TR.run(sp_req, session_factory=lambda hooks: FakeSession([]), recorder=FakeRecorder(),
                     hooks_factory=lambda rec, filming: {"camera": lambda r, a, rc: {"ok": False, "reason": "nope"}})
        self.assertEqual((res["ok"], res["failed_step"]), (False, step["name"]))
        self.assertNotIn("error_class", res)

    def test_copies_marks_the_value_for_a_paste(self):
        sp_req = request()
        sp_req["spec"]["steps"][0]["actions"][0]["copies"] = "PR-1"
        got = []
        res = TR.run(sp_req, session_factory=lambda hooks: FakeSession([]), recorder=FakeRecorder(),
                     hooks_factory=lambda rec, filming: {"mark_copied": got.append})
        self.assertTrue(res["ok"])
        self.assertEqual(got, ["PR-1"])


class LoginTab(FakeTab):
    def login_form(self):
        return True


class LoginSession(FakeSession):
    """The start URL redirects to the app's login page: the ready condition never holds there."""

    def __init__(self, log):
        super().__init__(log)
        self.tab = LoginTab(log)

    def check(self, checks, seconds):
        return False


def write_request(mode="take"):
    sp = S.load(os.path.join(REPO, "specs", "examples", "w-fixture-write.toml"))
    res, _ = S.resolve(sp, {"unique": "w-u1"})
    return {"job": "j", "run_id": "j-t1", "mode": mode, "n": 1, "run_dir": os.environ.get("VC_TEST_TMP", "/tmp") + "/tr",
            "spec": S.public(res), "deny_extra": [], "approved_write_labels": {"anlegen": ["Erstellen"]},
            "knobs": K.defaults()}


@unittest.skipUnless(sibling("jev"), "needs the M2 wrapper for its exception types")
class ReviewFixes(unittest.TestCase):
    def test_start_url_on_a_login_page_is_the_login_stop(self):          # Major 11, C-31 / F-26
        rec = FakeRecorder()
        res = TR.run(request(), session_factory=lambda hooks: LoginSession([]), recorder=rec)
        self.assertTrue(res.get("login_required"), res)
        self.assertEqual(rec.calls, [])                                 # nothing filmed

    def test_write_step_failing_after_the_create_still_tracks_the_item(self):   # Major 12, C-34
        res = TR.run(write_request(), session_factory=lambda hooks: FakeSession([], fail_at="anlegen#2"),
                     recorder=FakeRecorder())
        self.assertEqual((res["ok"], res["failed_step"]), (False, "anlegen"))
        self.assertEqual([i["name"] for i in res["created_items"]], ["w-u1"])

    def test_write_step_whose_check_fails_still_tracks_the_item(self):
        class NoCheck(FakeSession):
            def check(self, checks, seconds):
                return not any("w-u1" in json.dumps(c) for c in checks)
        res = TR.run(write_request("dry"), session_factory=lambda hooks: NoCheck([]), recorder=None)
        self.assertEqual(res["failed_step"], "anlegen")
        self.assertEqual([i["name"] for i in res["created_items"]], ["w-u1"])


class RunWatchdog(unittest.TestCase):
    """C-18 / run_timeout_s: a hung runner finalises its recording, writes result.json and exits 124."""

    def test_hung_run_is_ended_with_the_recording_finalised(self):
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            code = (
                "import sys, time, json; sys.path.insert(0, %r)\n"
                "from vcloop import take_runner as TR\n"
                "class Rec:\n"
                "    def stop(self): open(%r, 'w').write('stopped')\n"
                "req = {'mode': 'take', 'run_id': 'j-t1', 'run_dir': %r}\n"
                "TR._watchdog(req, {'res': {'mode': 'take', 'run_id': 'j-t1', 'steps': [1]}, 'rec': Rec(),"
                " 'recording': True}, 0.3)\n"
                "time.sleep(30)\n") % (os.path.join(REPO, "loop"), os.path.join(d, "rec"), d)
            p = subprocess.run([sys.executable, "-c", code], timeout=20)
            self.assertEqual(p.returncode, 124)
            res = json.load(open(os.path.join(d, "result.json")))
            self.assertEqual((res["error_class"], res["ok"], res["steps"]), ("run_timeout", False, [1]))
            self.assertEqual(open(os.path.join(d, "rec")).read(), "stopped")


if __name__ == "__main__":
    unittest.main()


class Localize(unittest.TestCase):
    def test_run_dir_is_the_request_folder_inside_the_container(self):
        req = {"run_dir": "/srv/someone/repo/runs/j-d1", "mode": "dry"}
        out = TR.localize(req, "/runs/j-d1/request.json")
        self.assertEqual(out["run_dir"], "/runs/j-d1")
        self.assertEqual(out["run_dir_host"], "/srv/someone/repo/runs/j-d1")
        same = TR.localize({"run_dir": "/runs/j-d1"}, "/runs/j-d1/request.json")
        self.assertEqual(same, {"run_dir": "/runs/j-d1"})

