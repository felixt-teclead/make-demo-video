"""vcjev command line.

  python -m vcjev models show|candidate|promote|rollback ...
  python -m vcjev summary LOG [--by phase|take|run_id]
  python -m vcjev step --cdp URL --env-file PATH --log LOG --step STEP.json [--start-url URL]

`step` runs one or more steps (a JSON object or list) through jev in the one
filmed tab and prints each step result plus the log summary (no secrets).
"""

import argparse
import datetime
import json
import os
import sys

from . import models as M
from .accounting import DecisionLog, summarize


def cmd_models(a):
    cfg = M.load(a.config)
    if a.action == "show":
        print(json.dumps({r: {"default": cfg[r]["default"], "candidate": cfg[r].get("candidate"),
                              "versions": list(cfg[r]["versions"])} for r in M.ROLES} | {"provider": cfg["provider"]},
                         indent=2))
        return 0
    if a.action == "candidate":
        M.add_candidate(cfg, a.role, a.version, a.price_in, a.price_out)
    elif a.action == "promote":
        with open(a.acceptance) as f:
            acceptance = json.load(f)
        M.promote(cfg, a.role, acceptance, datetime.date.today().isoformat())
    elif a.action == "rollback":
        M.rollback(cfg, a.role)
    M.save(cfg, a.config)
    print(f"{a.role}: default={cfg[a.role]['default']} candidate={cfg[a.role].get('candidate')}")
    return 0


def cmd_summary(a):
    log = DecisionLog(a.log, job=None)
    print(json.dumps(summarize(log.records(), a.by), indent=2))
    return 0


def cmd_step(a):
    from .runner import LoginRequired, Settings, StepFailed
    from .session import open_session

    with open(a.step) as f:
        steps = json.load(f)
    steps = steps if isinstance(steps, list) else [steps]
    vp = tuple(int(x) for x in a.viewport.split("x")) if a.viewport else None
    runner = open_session(cdp_url=a.cdp, env_file=a.env_file, log_path=a.log, job=a.job, run_id=a.run_id,
                          phase=a.phase, models_config=a.config, provider=a.provider, expect_viewport=vp,
                          settings=Settings(decisions_per_step=a.decisions_per_step))
    rc = 0
    try:
        if a.start_url:
            runner.tab.load_start_url(a.start_url)
        for s in steps:
            try:
                r = runner.run_step(s)
                print(json.dumps({"step": r.name, "ok": r.ok, "reason": r.reason, "decisions": r.decisions}))
            except (StepFailed, LoginRequired) as e:
                print(json.dumps({"step": s["name"], "ok": False, "reason": str(e)}))
                rc = 1
                break
    finally:
        runner.tab.close()
    recs = [r for r in DecisionLog(a.log, job=a.job).records() if r.get("job") == a.job]
    print(json.dumps({"summary": summarize(recs)}, indent=2))
    return rc


def main(argv=None):
    p = argparse.ArgumentParser(prog="vcjev")
    p.add_argument("--config", help="models config path (default: config/models.json)")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("models")
    m.add_argument("action", choices=["show", "candidate", "promote", "rollback"])
    m.add_argument("--role", choices=M.ROLES, default="jev")
    m.add_argument("--version")
    m.add_argument("--price-in", type=float)
    m.add_argument("--price-out", type=float)
    m.add_argument("--acceptance", help="acceptance summary JSON {version, passed, job_cost_usd}")
    s = sub.add_parser("summary")
    s.add_argument("log")
    s.add_argument("--by")
    st = sub.add_parser("step")
    st.add_argument("--cdp", default=os.environ.get("VC_CDP_URL"), help="default: $VC_CDP_URL")
    st.add_argument("--env-file", default=os.environ.get("VC_SECRETS"), help="default: $VC_SECRETS")
    st.add_argument("--log", required=True)
    st.add_argument("--step", required=True)
    st.add_argument("--start-url")
    st.add_argument("--job", default="manual")
    st.add_argument("--run-id")
    st.add_argument("--phase", default="dry run")
    st.add_argument("--provider")
    st.add_argument("--viewport", help="expected recording viewport, e.g. 1920x1080 (C-07)")
    st.add_argument("--decisions-per-step", type=int, default=6)
    a = p.parse_args(argv)
    if a.cmd == "step" and not (a.cdp and a.env_file):
        p.error("step needs --cdp and --env-file (or VC_CDP_URL and VC_SECRETS)")
    return {"models": cmd_models, "summary": cmd_summary, "step": cmd_step}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
