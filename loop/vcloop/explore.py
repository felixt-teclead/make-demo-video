"""Read-only exploration through jev (D-10, docs/steps/align.md step 4). Runs INSIDE the recorder container, under
the browser lock (`bin/vc-lock explore bin/vc-env exec python3 -m vcloop.explore ...`).

    look [--url URL] [--step JSON]... [--wait S]
                                         the current (or a freshly loaded) page, after optional jev steps: every control jev observes, with its
                                         own label, role, container context and on_screen; plus the page text
    spec SPEC --out DIR [--upto STEP]    walk the spec like a dry run (jev clicks the spec's controls, the camera pans;
                                         no recording) and write the exploration record DIR/<stamp>.json that
                                         `vc-spec validate --explore` reads (D-11): one observation per jev action,
                                         taken right before it

Navigation is jev's (C-14): the start URL loads off camera, every click is a jev decision on the one intended
control; denied controls are never offered. The page is only read otherwise.
"""

import argparse
import json
import os
import sys
import time

from . import knobs as K
from . import profiles as P
from . import spec as S
from . import take_runner as T

FIELDS = ("kind", "role", "label", "own_label", "context", "node", "on_screen")


def _session(log_path, job, phase, hooks=None, deny=()):
    from vcjev.runner import Hooks, Settings
    from vcjev.session import open_session
    kv = K.defaults()
    return open_session(
        cdp_url=os.environ["VC_CDP_URL"], env_file=os.environ["VC_SECRETS"], log_path=log_path, job=job,
        run_id=job, phase=phase, expect_viewport=(1920, 1080), deny_extra=list(deny),
        settings=Settings(**{k: kv[k] for k in K.RUNNER_KNOBS}),
        hooks=hooks if hooks is not None else Hooks(), budget_usd=kv["job_budget_usd"])


def _observe(runner):
    page = runner.tab.observe(screenshot=False)
    acts = runner.tab.enrich(page["actions"])
    return page.get("url"), [{k: a.get(k) for k in FIELDS if k in a} for a in acts]


def look(o):
    os.makedirs("/runs/explore", exist_ok=True)
    deny = P.deny_extra(P.load_all())       # every profile's additions: exploration never offers a write
    runner = _session("/runs/explore/look.log.jsonl", "explore-look", "explore", deny=deny)
    if o.url:
        runner.tab.load_start_url(o.url)
    for st in o.step or []:                 # jev steps before looking: {"name","op","target","check"[,"text"]}
        try:
            r = runner.run_step(json.loads(st))
            print(json.dumps({"step": r.name, "ok": r.ok, "reason": r.reason, "decisions": r.decisions}),
                  file=sys.stderr)
        except Exception as e:  # noqa: BLE001 - report and still look at the page
            print(json.dumps({"step_failed": str(e)}), file=sys.stderr)
            break
    time.sleep(o.wait)
    url, acts = _observe(runner)
    text = runner.tab.js("document.body.innerText") or ""
    if o.shot:                              # read-only screenshot of the filmed tab (H3: reading state)
        import base64
        png = runner.tab.call("Page.captureScreenshot", format="png")["data"]
        with open(o.shot, "wb") as f:
            f.write(base64.b64decode(png))
    out = {"url": url, "actions": acts, "text": text[: o.text_chars]}
    print(json.dumps(out, ensure_ascii=False, indent=None if o.compact else 1))
    runner.tab.close()


def explore_spec(o):
    from vcjev.runner import Hooks
    sp = S.load(o.spec)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    job = f"explore-{sp['name']}-{stamp}"
    res, missing = S.resolve(sp, {"unique": f"{sp['name']}-{stamp}-x1"})
    if missing:
        sys.exit(f"unfilled parameters: {missing}")
    if o.upto:
        names = [s["name"] for s in res["steps"]]
        res["steps"] = res["steps"][: names.index(o.upto) + 1]
    profs = P.for_spec(res, None, "auto")
    deny = P.deny_extra(profs) + list(sp.get("deny") or [])
    rd = os.path.join("/runs/explore", job)
    os.makedirs(rd, exist_ok=True)
    kv = K.defaults()
    request = {"job": job, "run_id": job, "mode": "dry", "n": 0, "run_dir": rd, "spec_path": o.spec,
               "spec": S.public(res), "unique": f"{sp['name']}-{stamp}-x1", "deny_extra": deny,
               "approved_write_labels": {}, "knobs": {k: kv[k] for k in K.RUNNER_KNOBS + (
                   "speedup_factor", "hold_final_s", "job_budget_usd", "run_timeout_s")},
               "profiles": [P.public(p) for p in profs], "job_cost_usd": 0.0}
    observations = []

    def factory(hk):
        runner = _session(os.path.join(rd, "take.log.jsonl"), job, "explore", hooks=Hooks(**hk), deny=deny)
        inner = runner.run_step

        def resolves(acts, step):
            from . import match
            try:
                match.offered_set(acts, step["name"], step["target"], step["op"], match.DenyList(extra=deny))
                return True
            except Exception:  # noqa: BLE001 - 0/2+/denied: not (yet) resolvable
                return False

        def run_step(step):
            # the observation jev will decide on: re-observe while the page is still rendering the control, like the
            # runner's readiness wait (a snapshot taken right after the previous click may predate the control)
            name, _, idx = step["name"].partition("#")
            obs, t_end = None, time.time() + kv["ready_timeout_s"]
            while True:
                try:
                    url, acts = _observe(runner)
                    obs = {"step": name, "action": int(idx or 1) - 1, "url": url, "actions": acts}
                    if resolves(acts, step) or time.time() > t_end:
                        break
                except Exception as e:  # noqa: BLE001 - a stale page while observing is not an exploration failure
                    obs = {"step": name, "action": int(idx or 1) - 1, "error": str(e)}
                    if time.time() > t_end:
                        break
                time.sleep(kv["wait_poll_s"])
            observations.append(obs)
            return inner(step)

        runner.run_step = run_step
        return runner

    result = T.run(request, session_factory=factory)
    rec = {"spec": sp["name"], "synthetic": False, "explored_at": stamp, "by": "vcloop.explore (jev, read-only)",
           "result": {k: result.get(k) for k in ("ok", "completed", "failed_step", "reason", "steps")},
           "observations": observations}
    os.makedirs(o.out, exist_ok=True)
    path = os.path.join(o.out, f"{stamp}.json")
    with open(path, "w") as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)
    print(json.dumps({"record": path, **rec["result"]}, ensure_ascii=False, indent=1))
    return 0 if result.get("ok") else 1


def main(argv):
    ap = argparse.ArgumentParser(prog="vcloop.explore")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lk = sub.add_parser("look")
    lk.add_argument("--url")
    lk.add_argument("--step", action="append", help="a jev step as JSON, run before looking (repeatable)")
    lk.add_argument("--wait", type=float, default=1.5)
    lk.add_argument("--text-chars", type=int, default=3000)
    lk.add_argument("--compact", action="store_true")
    lk.add_argument("--shot", help="also save a screenshot (PNG) here")
    sp = sub.add_parser("spec")
    sp.add_argument("spec")
    sp.add_argument("--out", required=True)
    sp.add_argument("--upto")
    o = ap.parse_args(argv)
    if o.cmd == "look":
        return look(o) or 0
    return explore_spec(o)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
