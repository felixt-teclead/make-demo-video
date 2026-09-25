"""Command lines: `vc-spec` (validate, view, approve, fingerprint, diff) and `vc-loop` (run, resume, status, best,
summary). Exit codes of vc-loop: 0 done, 10 handoff (a model result is needed), 20 waiting for the human (attended
stop), 21 ended at a stop (unattended), 2 refused (spec invalid or not approved), 1 error."""
import argparse
import json
import os
import sys
import time

from . import knobs as K
from . import logs as L
from . import loop as LP
from . import spec as S


def _events_path(sp):
    snap, _ = S.snapshot_paths(sp)
    return os.path.join(os.path.dirname(snap), sp["name"] + ".events.jsonl")


def spec_main(argv=None):
    ap = argparse.ArgumentParser(prog="vc-spec", description="Use-case spec: validate, view, approve, diff (F-05..F-10)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="machine validation (F-10); exit 1 on any error")
    v.add_argument("spec")
    v.add_argument("--explore", help="exploration record dir (default: <spec dir>/explore/<name>/)")
    v.add_argument("--draft", action="store_true", help="draft check: a missing exploration record is not an error")
    v.add_argument("--json", action="store_true")
    w = sub.add_parser("view", help="the approval view (F-05), generated from the spec file")
    w.add_argument("spec")
    a = sub.add_parser("approve", help="record an explicit approval (F-06, C-34, F-37)")
    a.add_argument("spec")
    a.add_argument("--by", help="the human who approved (attended)")
    a.add_argument("--approve-write", action="append", default=[], help="write id the owner approved (C-34)")
    a.add_argument("--unattended", action="store_true", help="self-approval from the instruction (F-37)")
    a.add_argument("--instruction", help="instruction text file (unattended)")
    a.add_argument("--assume", action="append", default=[], help="an assumption made at alignment (unattended)")
    a.add_argument("--explore")
    f = sub.add_parser("fingerprint", help="print the contract fingerprint (F-06)")
    f.add_argument("spec")
    c = sub.add_parser("contract", help="print the contract parts as JSON")
    c.add_argument("spec")
    d = sub.add_parser("diff", help="diff against what was approved; exit 1 if the contract changed")
    d.add_argument("spec")
    o = ap.parse_args(argv)
    try:
        sp = S.load(o.spec)
        if o.cmd == "validate":
            probs = S.validate(sp, explore_dir=o.explore, require_explore=not o.draft)
            if o.json:
                print(json.dumps([p.as_dict() for p in probs], indent=1, ensure_ascii=False))
            else:
                for p in probs:
                    print(p)
                errs = S.errors(probs)
                print(f"{sp['name']}: {'INVALID' if errs else 'valid'} ({len(errs)} errors, "
                      f"{len(probs) - len(errs)} warnings); contract {S.fingerprint(sp)}")
            return 1 if S.errors(probs) else 0
        if o.cmd == "view":
            sys.stdout.write(S.approval_view(sp))
            snap, _ = S.snapshot_paths(sp)
            os.makedirs(os.path.dirname(snap), exist_ok=True)
            L.append_jsonl(_events_path(sp), {"event": "view", "t": time.time(), "fingerprint": S.fingerprint(sp)})
            return 0
        if o.cmd == "approve":
            instr = None
            if o.unattended:
                if not o.instruction:
                    raise S.SpecError("--unattended needs --instruction FILE")
                with open(o.instruction, encoding="utf-8") as fh:
                    instr = fh.read()
            ap_ = S.approve(o.spec, by=o.by, write_ids=o.approve_write, unattended_instruction=instr,
                            assumptions=o.assume, explore_dir=o.explore)
            L.append_jsonl(_events_path(sp), {"event": "approve", "t": time.time(), "fingerprint": ap_["fingerprint"],
                                              "approver": ap_["approver"]})
            print(f"approved {sp['name']} {ap_['fingerprint']} by {ap_['approver']} at {ap_['approved_at']}; "
                  f"writes approved: {[x['id'] for x in ap_['writes']] or 'none'}")
            return 0
        if o.cmd == "fingerprint":
            print(S.fingerprint(sp))
            return 0
        if o.cmd == "contract":
            print(json.dumps(S.contract(sp), indent=1, ensure_ascii=False))
            return 0
        if o.cmd == "diff":
            changed, text = S.diff(o.spec)
            sys.stdout.write(text)
            return 1 if changed else 0
    except S.SpecError as e:
        print(f"vc-spec: {e}", file=sys.stderr)
        return 2
    return 1


def loop_main(argv=None):
    ap = argparse.ArgumentParser(prog="vc-loop", description="The deterministic retake loop (F-11, F-14)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start a job for an approved spec")
    r.add_argument("spec")
    r.add_argument("--components", required=True, help="components config (JSON)")
    r.add_argument("--knob", action="append", default=[], help="name=value (F-23), overrides the spec's [knobs]")
    r.add_argument("--job", help="job id (default <spec>-<stamp>)")
    rs = sub.add_parser("resume", help="continue a job from its saved state")
    rs.add_argument("job_dir")
    rs.add_argument("--answer", help="answer to the waiting stop point, e.g. raise-cap=6, accept, logged-in")
    rs.add_argument("--attended", action="store_true", help="resume an unattended job with a human reachable")
    st = sub.add_parser("status")
    st.add_argument("job_dir")
    b = sub.add_parser("best", help="choose the best take from a take log (F-32)")
    b.add_argument("take_log")
    b.add_argument("--length", default="30,60")
    sm = sub.add_parser("summary", help="per-phase timing summary (F-28)")
    sm.add_argument("job_dir")
    o = ap.parse_args(argv)
    try:
        if o.cmd == "run":
            job = LP.Job.create(o.spec, o.components, K.parse_overrides(o.knob), o.job)
            print(f"job {job.st['job']} at {job.dir}")
            return job.run()
        if o.cmd == "resume":
            job = LP.Job(o.job_dir)
            if o.attended:
                job.st["knobs"]["human_reachable"] = True
                job.st["knob_sources"]["human_reachable"] = "resume --attended"
            if o.answer:
                job.answer(o.answer)
            elif job.st["status"] == "handoff":
                job.st["status"] = "running"
            elif job.st["status"] in ("waiting", "ended", "done", "refused"):
                print(f"job is {job.st['status']}; nothing to do" + (" (give --answer)" if job.st["status"] == "waiting" else ""))
                return LP.EXIT.get(job.st["status"], 1)
            job.save()
            return job.run()
        if o.cmd == "status":
            job = LP.Job(o.job_dir)
            s = job.st
            print(json.dumps({k: s.get(k) for k in ("job", "status", "next", "takes_used", "dry_runs_used",
                                                    "last_verdict", "contract_fingerprint", "approved_fingerprint",
                                                    "stop", "hit_take", "phase_seq")}, indent=1, ensure_ascii=False))
            return 0
        if o.cmd == "best":
            lo, hi = (float(x) for x in o.length.split(","))
            best, hit = L.best_take(L.read_jsonl(o.take_log), (lo, hi))
            print(json.dumps({"take": best["take"] if best else None, "label": "HIT" if hit else "NOT A HIT"}))
            return 0
        if o.cmd == "summary":
            job = LP.Job(o.job_dir)
            print("\n".join(L.summary_lines(L.summarize(job.timing.records()))))
            return 0
    except (LP.Refused, S.SpecError, K.KnobError) as e:
        print(f"vc-loop: {e}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(loop_main())
