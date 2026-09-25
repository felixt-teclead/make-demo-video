"""The real "run" component: dry run, take or cleanup of one spec, inside the recorder container.

    python3 -m vcloop.take_runner /runs/<run_id>/request.json

It drives the page ONLY through the jev wrapper (M2 `vcjev`, C-14): one M2 step per click/type/select action, with the
spec's control, exact text and done condition; the step's expected state is checked at the end of the step (C-02).
In a take it also drives the recorder (M1 HTTP API): start, one mark per step (named after the step), every hold
(landing, action holds, step holds, final 12 s) and the click/typing events (OD-19). Holds are skipped in a dry run.
Camera actions (pan, reveal) need a camera hook (M3); without one they fail the step with a clear reason.
Writes result.json in the run dir (contract: docs/loop.md). Not run live yet: the environment's browser is blocked.

Events (docs/events.md, one stream: the recorder's events.jsonl, read by the cutter and the gate): every event this
runner or its hooks post carries the spec step number; marks carry the page URL; the jev wrapper's readiness waits
are posted as `readiness_wait` events with `t`/`end`; clicks carry `t`, `glide_t`, `click_t`, `x`, `y`; typing and
paste carry `end`; pans and reveals carry `end`.
"""
import importlib
import json
import os
import sys
import threading
import time
import urllib.request


class Recorder:
    """M1 recorder control API (docs/interfaces.md section 4)."""

    def __init__(self, url=None):
        self.url = (url or os.environ.get("VC_RECORDER_URL", "http://127.0.0.1:7777")).rstrip("/")

    def post(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")

    def status(self):
        with urllib.request.urlopen(self.url + "/record/status", timeout=10) as r:
            return json.loads(r.read().decode() or "{}")

    def start(self, run_id):
        return self.post("/record/start", {"run_id": run_id, "label": run_id})

    def mark(self, name, step, url=None):
        body = {"name": name, "step": step, "t": time.time()}
        if url:
            body["url"] = url
        return self.post("/mark", body)

    def hold(self, kind, seconds, step):
        return self.post("/hold", {"kind": kind, "seconds": seconds, "step": step, "t": time.time(), "wait": True})

    def event(self, ev):
        return self.post("/event", ev)

    def stop(self):
        return self.post("/record/stop", {})


def jev_step(step, i, action, request):
    """Map one spec action to the M2 step dict (docs/jev-interface.md section 2)."""
    s = {"name": f"{step['name']}#{i + 1}", "op": action["op"], "target": dict(action.get("control") or {}),
         "approved_write_labels": list((request.get("approved_write_labels") or {}).get(step["name"], []))
         if step.get("write") else []}
    if action["op"] == "type":
        s["text"] = action["text"]
        s["submit"] = bool(action.get("submit", False))
    if action["op"] == "select" and action.get("option"):
        s["target"]["option"] = action["option"]
    s["check"] = action.get("done") or step.get("expect")
    return s


def run_setup(runner, spec, request, kn, failed):
    """Off camera (start.setup, docs/spec-format.md): the spec's approved setup actions run after the start view is
    ready and before the cursor is installed and the recording starts, with the on-camera hooks switched off (no
    glide, no event). Then the setup's expect and the start view's ready condition must hold again. `failed` is the
    runner's StepFailed class."""
    su = (spec.get("start") or {}).get("setup")
    if not su:
        return
    saved = getattr(runner, "h", None)
    if saved is not None:
        runner.h = type(saved)()                     # plain hooks: nothing is drawn or posted off camera
    try:
        for i, a in enumerate(su.get("actions", [])):
            runner.run_step(jev_step(su, i, a, request))
    finally:
        if saved is not None:
            runner.h = saved
    if not _check(runner, su.get("expect") or [], kn["ready_timeout_s"]):
        raise failed(su["name"], "setup (off camera): expected state not reached")
    if not _check(runner, spec["start"]["ready"], kn["ready_timeout_s"]):
        raise failed(su["name"], "setup (off camera): the start view is not ready again")


def _install_view_start(runner, request, vs):
    """Profile `view_start` rules (vc/overlay/viewstart.py, D-33 item 3): installed on the filmed tab as a
    document-start script, so a matching view paints its first frame at the start position. Returns an error text for
    a bad rule, else None."""
    try:
        from vc.overlay import viewstart
    except ImportError:                              # a loop without the overlay package: nothing to install
        return None
    try:
        vs["rules"] = viewstart.rules_from_profiles(request.get("profiles"))
    except viewstart.RuleError as e:
        return f"app profile: {e}"
    call = getattr(runner.tab, "call", None)
    if vs["rules"] and call is not None:
        viewstart.install(call, vs["rules"])
    return None


def _view_start_log(runner):
    try:
        from vc.overlay import viewstart
        js = runner.tab.js
    except (ImportError, AttributeError):
        return []
    return viewstart.summary(viewstart.read_log(js))


def _check(runner, checks, seconds):
    fn = getattr(runner, "check", None) or getattr(runner, "_poll_check")
    r = fn(checks, seconds)
    return bool(r.get("ok")) if isinstance(r, dict) else bool(r)     # M2's _poll_check returns {"ok": ..}


class StepEvents:
    """The recorder as the hooks see it: every posted event gets the current spec step (1-based)."""

    def __init__(self, rec, current):
        self.rec, self.current = rec, current

    def event(self, ev):
        if self.rec is None:
            return None
        ev = dict(ev)
        ev.setdefault("step", self.current["step"])
        return self.rec.event(ev)

    def __getattr__(self, name):
        return getattr(self.rec, name)


def _login_form(runner):
    """The page shows a login form (M2's detector). Fakes without one never show a login."""
    fn = getattr(runner.tab, "login_form", None)
    try:
        return bool(fn()) if fn else False
    except Exception:  # noqa: BLE001 - a navigating page: the ready check decides
        return False


def _stop_stale_recording(rec):
    """C-18: this runner holds the browser lock, so a recording that is still running belongs to a take that died
    (its session was killed, or the runner was). Finalise it before filming; return its run id (or None)."""
    status = getattr(rec, "status", None)
    if status is None:
        return None
    cur = (status() or {}).get("recording")
    if cur:
        rec.stop()
    return cur


def _page_url(runner):
    try:
        return runner.tab.js("location.href")
    except Exception:  # noqa: BLE001 - fakes and a navigating page
        return None


def _forward_waits(runner, events):
    """Post the jev wrapper's readiness waits (take.log `readiness_wait` start/end) as recorder events, so the one
    event stream holds them (the cutter speeds them up, Q-56; the gate sees them)."""
    log = getattr(runner, "log", None)
    if log is None or not hasattr(log, "event"):
        return
    orig = log.event

    def event(type_, **kw):
        r = orig(type_, **kw)
        if type_ == "readiness_wait" and kw.get("start") and kw.get("end"):
            try:
                events.event({"type": "readiness_wait", "t": kw["start"], "end": kw["end"], "ok": kw.get("ok"),
                              "jev_step": kw.get("step")})
            except Exception:  # noqa: BLE001 - a lost wait only loses a speed-up
                pass
        return r
    log.event = event


def run(request, *, session_factory=None, recorder=None, hooks_factory=None, ctx=None):
    """ctx (optional dict) is shared with the run-timeout watchdog of main(): the result so far, the recorder and
    whether this run's recording is running."""
    mode, rd = request["mode"], request["run_dir"]
    spec, kn = request["spec"], request["knobs"]
    os.makedirs(rd, exist_ok=True)
    res = {"mode": mode, "run_id": request["run_id"], "ok": True, "completed": False, "steps": [],
           "failed_step": None, "reason": None, "created_items": [], "deleted_items": []}
    filming = mode == "take"
    rec = recorder if recorder is not None else (Recorder() if filming else None)
    ctx = ctx if ctx is not None else {}
    ctx.update(res=res, rec=rec, recording=False)
    if session_factory is None:
        from vcjev.runner import Hooks, Settings
        from vcjev.session import open_session

        def session_factory(hooks):
            return open_session(
                cdp_url=os.environ["VC_CDP_URL"], env_file=os.environ["VC_SECRETS"],
                log_path=os.path.join(rd, "take.log.jsonl"), job=request["job"], run_id=request["run_id"],
                phase=mode, take=request["n"] if filming else None, expect_viewport=(1920, 1080),
                deny_extra=request.get("deny_extra", []),
                settings=Settings(**{k: kn[k] for k in ("decisions_per_step", "transient_retries", "settle_s",
                                                        "ready_timeout_s", "wait_poll_s", "check_poll_s",
                                                        "point_tolerance_px", "point_rechecks")}),
                hooks=hooks, budget_usd=kn.get("job_budget_usd", 1.0) - request.get("job_cost_usd", 0.0))
        hooks_cls = Hooks
    else:
        hooks_cls = None
    current = {"step": None, "glide_t": None, "click_t": None}
    events = StepEvents(rec if filming else None, current)

    # fallback hooks without an overlay (VC_HOOKS unset): no cursor on camera, but the same event fields
    def before_input(action, point):
        current["glide_t"] = time.time()

    def before_click(action, point):
        current["click_t"] = time.time()

    def after_input(action, point, info):
        if filming and rec is not None:
            info = info or {}
            x = point.get("x") if isinstance(point, dict) else (point[0] if point else None)
            y = point.get("y") if isinstance(point, dict) else (point[1] if point else None)
            if action.get("kind") == "fill":
                events.event({"type": "typing", "label": action.get("own_label") or action.get("label"),
                              "t": info.get("t_first") or current["click_t"] or time.time(),
                              "end": info.get("t_last") or time.time(), "chars": info.get("chars"), "x": x, "y": y})
            else:
                t = current["click_t"] or time.time()
                events.event({"type": "click", "label": action.get("own_label") or action.get("label"), "x": x,
                              "y": y, "t": t, "glide_t": current["glide_t"] or t, "click_t": time.time()})

    hook_mod = os.environ.get("VC_HOOKS")          # M3 cursor/ripple/typing/camera hooks: module with make_hooks()
    extra = importlib.import_module(hook_mod).make_hooks(events, filming) if hook_mod else {}
    if hooks_factory:
        extra.update(hooks_factory(events, filming))
    hk = {"before_input": extra.get("before_input", before_input), "after_input": extra.get("after_input", after_input)}
    if "before_click" in extra or not hook_mod:
        hk["before_click"] = extra.get("before_click", before_click)
    if extra.get("input_aborted"):
        hk["input_aborted"] = extra["input_aborted"]
    if extra.get("type_text"):
        hk["type_text"] = extra["type_text"]
    runner = session_factory(hooks_cls(**hk) if hooks_cls else hk)
    if extra.get("bind"):
        extra["bind"](runner.tab)
    if filming:
        _forward_waits(runner, events)
    camera = extra.get("camera")
    vs = {"rules": []}
    mark_copied = extra.get("mark_copied")
    started = False
    try:
        from vcjev.runner import LoginRequired, StepFailed  # noqa: F401
    except Exception:  # noqa: BLE001 - tests without vcjev
        class LoginRequired(Exception):
            pass

        class StepFailed(Exception):
            def __init__(self, step, reason, result=None):
                super().__init__(f"step {step!r} failed: {reason}")
                self.step, self.reason = step, reason
    try:
        if mode == "cleanup":
            for it in request.get("cleanup_items", []):
                cl = request["cleanup"][it["write"]]
                tgt = json.loads(json.dumps(cl["control"]).replace("{item}", it["name"]))
                chk = json.loads(json.dumps(cl.get("check") or []).replace("{item}", it["name"]))
                label = tgt.get("label") or tgt.get("label_contains")
                runner.run_step({"name": f"cleanup-{it['id']}", "op": cl.get("op", "click"), "target": tgt,
                                 "check": chk, "approved_write_labels": [label]})
                res["deleted_items"].append({"id": it["id"], "name": it["name"]})
            res["completed"] = True
            return res
        # D-33 item 3: the profiles' view start positions, before any page of the run loads (dry run proves it)
        vs_err = _install_view_start(runner, request, vs)
        if vs_err:
            res.update(ok=False, error_class="profile", reason=vs_err)
            return res
        # off camera: warm routes, then the start view, then its ready condition
        for u in spec["start"].get("warm", []):
            runner.tab.load_start_url(u)
        runner.tab.load_start_url(spec["start"]["url"])
        if _login_form(runner):                      # the start URL redirected to a login page (C-31, F-26)
            raise LoginRequired(f"the start URL shows a login page ({spec['start']['url']}); no decision made")
        if not _check(runner, spec["start"]["ready"], kn["ready_timeout_s"]):
            if _login_form(runner):
                raise LoginRequired(f"the start URL shows a login page ({spec['start']['url']}); no decision made")
            res.update(ok=False, reason="the start view never became ready", failed_step=spec["steps"][0]["name"])
            return res
        run_setup(runner, spec, request, kn, StepFailed)   # approved off-camera setup, e.g. an empty start page
        # preconditions with a check, on the ready start view (they are about it: a list's view mode, an element
        # visible without scrolling; F-07). A failed precondition is not a failed take.
        for p in spec.get("preconditions", []):
            if p.get("check") and not _check(runner, p["check"], kn["settle_s"]):
                if _login_form(runner):              # the session expired: a login stop, not a precondition
                    raise LoginRequired("a login page is showing; no decision made (C-31)")
                res.update(ok=False, precondition_failed={"what": p["what"], "by": p["by"]},
                           reason="precondition not met")
                return res
        if extra.get("install"):
            extra["install"]()                       # the cursor is on the page before the first frame (Q-43)
        if filming:
            stale = _stop_stale_recording(rec)
            if stale:
                res["stale_recording_stopped"] = stale
            rec.start(request["run_id"])
            started = True
            ctx["recording"] = True
        for si, step in enumerate(spec["steps"]):
            current["step"] = si + 1
            if filming:
                rec.mark(step["name"], si + 1, _page_url(runner))
                if si == 0:
                    rec.hold("landing", float(spec["start"].get("hold", 1.0)), 1)
            if step.get("write"):
                # C-34: track the item BEFORE the first input: a step that fails after the create (a later action,
                # the check) must still leave the item on the cleanup list. `confirmed` = the step's check passed.
                for a in step.get("actions", []):
                    if a["op"] == "type":
                        res["created_items"].append({"id": f"name:{a['text']}", "name": a["text"],
                                                     "write": step["write"], "step": step["name"],
                                                     "confirmed": False})
            for i, a in enumerate(step.get("actions", [])):
                if a.get("wait_before") and not _check(runner, a["wait_before"], kn["ready_timeout_s"]):
                    raise StepFailed(step["name"], f"action {i + 1}: readiness condition never held")
                if a["op"] in ("click", "type", "select"):
                    runner.run_step(jev_step(step, i, a, request))
                elif a["op"] in ("pan", "reveal"):
                    if not camera:
                        raise StepFailed(step["name"], f"action {i + 1}: {a['op']} needs the camera hook (M3)")
                    r = camera(runner, a, rec if filming else None)
                    if isinstance(r, dict) and not r.get("ok", True):
                        raise StepFailed(step["name"], f"action {i + 1}: {r.get('reason')}")
                elif a["op"] == "wait":
                    if not _check(runner, a.get("until") or [], kn["ready_timeout_s"]):
                        raise StepFailed(step["name"], f"action {i + 1}: wait condition never held")
                if a.get("copies") and mark_copied:      # this action copied a value: typing it later is a paste
                    mark_copied(a["copies"])
                if a.get("hold") is not None and filming:
                    rec.hold(a.get("hold_kind", "step"), float(a["hold"]), si + 1)
                elif a["op"] == "hold" and filming:
                    rec.hold(a.get("kind", "reading"), float(a["seconds"]), si + 1)
            ok = _check(runner, step["expect"], kn["settle_s"])
            res["steps"].append({"name": step["name"], "ok": ok, "verified": ok,
                                 "reason": None if ok else "expected state not visible at the end of the step"})
            if not ok:
                res.update(ok=False, failed_step=step["name"], reason="expected state not reached")
                break
            if step.get("write"):
                for it in res["created_items"]:
                    if it["step"] == step["name"]:
                        it["confirmed"] = True
            if filming:
                rec.hold(step.get("hold_kind", "step"), float(step.get("hold", 1.0)), si + 1)
        res["completed"] = res["ok"]
    except LoginRequired as e:
        res.update(ok=False, login_required=True, reason=str(e))
    except StepFailed as e:
        name = getattr(e, "step", "?").split("#")[0] if isinstance(getattr(e, "step", None), str) else "?"
        res["steps"].append({"name": name, "ok": False, "verified": False, "reason": str(e)})
        res.update(ok=False, failed_step=name, reason=str(e))
    except Exception as e:  # noqa: BLE001 - recorder/browser trouble is a stop, not a failed take
        res.update(ok=False, error_class="budget" if "budget" in str(e).lower() else "recorder_down",
                   reason=f"{type(e).__name__}: {e}")
    finally:
        if vs["rules"]:
            res["view_start"] = _view_start_log(runner)
        if started:
            try:
                ctx["recording"] = False
                rec.stop()
            except Exception as e:  # noqa: BLE001
                res.update(ok=False, completed=False, error_class="recorder_down", reason=f"recorder stop: {e}")
    return res


def _write_result(request, res):
    path = os.path.join(request["run_dir"], "result.json")
    with open(path + ".tmp", "w") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def _watchdog(request, ctx, seconds):
    """Run timeout (knob run_timeout_s, C-18): a run that hangs (a stuck page, provider or recorder call) must not
    hold the one browser and the recorder forever. Finalise this run's recording, write the result, exit 124."""
    def fire():
        res = dict(ctx.get("res") or {"mode": request["mode"], "run_id": request["run_id"], "steps": [],
                                      "created_items": [], "deleted_items": []})
        res.update(ok=False, completed=False, error_class="run_timeout",
                   reason=f"the run did not finish within run_timeout_s={seconds:g} s")
        if ctx.get("recording") and ctx.get("rec") is not None:
            try:
                ctx["rec"].stop()
            except Exception as e:  # noqa: BLE001
                res["reason"] += f"; recorder stop: {e}"
        try:
            _write_result(request, res)
        finally:
            os._exit(124)
    t = threading.Timer(seconds, fire)
    t.daemon = True
    t.start()
    return t


def localize(request, request_path):
    """The loop writes the request on the host (host paths); the runner reads it inside the container, where the
    same folder is mounted elsewhere (/runs/<run_id>). The request file always lives in its run dir, so that folder
    is the run dir here."""
    here = os.path.dirname(os.path.abspath(request_path))
    if os.path.abspath(request.get("run_dir") or "") != here:
        request = dict(request, run_dir=here, run_dir_host=request.get("run_dir"))
    return request


def main(argv):
    with open(argv[0]) as f:
        request = localize(json.load(f), argv[0])
    ctx = {}
    limit = float((request.get("knobs") or {}).get("run_timeout_s") or 0)
    if limit > 0:
        _watchdog(request, ctx, limit)
    res = run(request, ctx=ctx)
    _write_result(request, res)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
