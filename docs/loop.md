# The deterministic loop (M5): interfaces

Owner: milestone M5. Code: `loop/vcloop/` (host python3, stdlib only), CLIs `bin/vc-loop`, `bin/vc-spec`.
Requirement IDs refer to `vc-v1-spec`.

## Running

```sh
bin/vc-loop run SPEC --components config/components.json [--knob name=value]... [--job ID]
bin/vc-loop resume JOB_DIR [--answer OPTION] [--attended]
bin/vc-loop status JOB_DIR | summary JOB_DIR | best TAKE_LOG [--length 30,60]
```

Exit codes: **0** done (a hit delivered, or a NOT A HIT the human accepted), **10** handoff (a model result is needed:
viewer review or fixer), **20** waiting at an attended stop point, **21** ended at a stop point (unattended, or aborted),
**2** refused (spec invalid or not approved), **137** only from the test crash hook.

Phase order (F-11): preflight (validate, approval + fingerprint, plan F-21) → login check → dry run → login check →
take k → cut (+join) → QA → viewer review (only on a gate PASS) → judge → hit: deliver → cleanup; not hit and k < cap:
fix → full dry run → take k+1; cap: fixer proposal → deliver NOT A HIT → stop 3. A login check runs before every
browser phase. An unchanged "flake" retake (once per job) skips the dry run.

Hit (F-12): gate PASS **and** no viewer-review blocker **and** the take completed **and** the current contract fingerprint
equals the approved one (F-06). The viewer review can only turn a PASS into a failure.

## The one knob table (F-23)

`loop/vcloop/knobs.py` `TABLE`: max_takes 4, dry_runs 1, dry_run_cap 6, lock_timeout_s 600, run_timeout_s 900, flake_retakes 1,
decisions_per_step 6, transient_retries 2, settle_s 3.0, ready_timeout_s 10.0, wait_poll_s 0.4, check_poll_s 0.05,
point_tolerance_px 2, point_rechecks 3, the Q-62 holds (landing 1, step 1, dialog 3, reading 1.5–6 at 3.3 words/s,
final 12), action_time_s 1.0, speedup_factor 4, crossfade_s 0.2, writes_allowed off, length 30–60,
login_mode auto, jev/text-helper models = models config, fixer/viewer-review model = orchestrator (review_privacy off),
human_reachable yes, app_profile auto, job_budget_usd 1.0. Spec `[knobs]` < `--knob`. The plan lists every knob
with its value and origin. The runner group is passed to M2's `Settings` unchanged.

## Components config (JSON; real example `config/components.example.json`)

Commands with placeholders `{run_dir} {run_id_c} {request} {request_c} {events} {root} {label}` and every knob.
`"lock"`: a command prefix for browser phases (M1 `bin/vc-lock {label}`) or `null` (the loop's own flock on
`$VC_STATE_DIR/browser.lock`, same lock.log format, timeout = `lock_timeout_s`). `"self_locking"`: components whose
command takes the lock itself; the real `run` does, INSIDE the container (`vc-env exec env VC_LOCK_TIMEOUT=..
/opt/vc/bin/vc-lock {label} python3 -m vcloop.take_runner ..`), so the lock lives exactly as long as the runner that
uses the browser: a killed loop or a timed-out host call frees nothing while the runner still films (C-18).
`"review"` (old key `"framecheck"`)/`"fixer"` may be
`"handoff"`. `"protected"`: glob list of QA-definition files whose hashes must not change during a fix (F-19 backstop).

### run (dry run / take / cleanup) — request and result

The loop writes `<runs>/<run_id>/request.json`:
`{job, run_id, mode: dry|take|cleanup, n, run_dir, spec_path, spec (parameters and {unique} filled), unique,
deny_extra (profile + spec), approved_write_labels {step: [labels]}, knobs {runner knobs, speedup_factor,
hold_final_s, job_budget_usd}, profiles, job_cost_usd, [cleanup_items, cleanup]}`.
The component writes `<run_dir>/result.json`:
`{ok, completed, steps: [{name, ok, verified, reason, decisions}], failed_step, reason, login_required?,
precondition_failed? {what, by}, error_class? (recorder_down | budget | run_timeout), created_items: [{id, name,
write, step, confirmed}], deleted_items: [{id, name}], stale_recording_stopped?}`.
A write step's item is tracked BEFORE its first input (`confirmed` turns true when the step's check passes), so a step
that fails after the create still leaves the item on the cleanup list (C-34). A start URL or a precondition check that
lands on a login page is `login_required` (stop 2, C-31/F-26), not a failed step. Before `/record/start` the runner
finalises a recording that is still running: it holds the lock, so that recording belongs to a take that died
(`stale_recording_stopped` = its run id). The runner ends itself after `run_timeout_s` (finalises its recording,
writes `error_class: run_timeout`, exit 124); the loop's host call times out after `lock_timeout_s + run_timeout_s +
min(60, run_timeout_s)`.
Stop causes (stop 7 carries `cause`): the run or login check exits 75 without a result (vc-lock timed out) =
`lock_timeout` (does not count as a dry run or take; never "recorder down" or a login stop); a hung run =
`run_timeout`; a runner that crashed or wrote no result = `recorder_down`; login check exit 22 = `recorder_busy`.
A take number that did not count comes back with a fresh run folder (`<job>-t<k>-r2`, Q-58). A take also leaves M1's `raw.mp4`, `manifest.json`, `events.jsonl` and M2's
`take.log.jsonl` in the run dir. The real component is `python3 -m vcloop.take_runner REQUEST` inside the container
(needs `PYTHONPATH=/opt/vc/loop:/opt/vc/jev`, `VC_CDP_URL`, `VC_SECRETS`, `VC_RECORDER_URL`; `VC_HOOKS` =
a module with `make_hooks(recorder, filming)`; the image sets `VC_HOOKS=vc.overlay.vc_hooks`, which returns the
overlay hooks `before_input`, `before_click`, `after_input`, `input_aborted`, `type_text` (takes only) plus `bind`,
`install`, `camera` and `mark_copied`). Events posted to the recorder follow `docs/events.md`.

### cut, gate

`cut`: M4 `vc-cut RUN_DIR --speed {speedup_factor}`; the loop reads `cut/record.json` `timing.join_s` for the join
record. Before the gate the loop writes `clips/events.json` (M6a's "event log in clip time": steps, length, dry_run,
final_hold, per clip clicks/actions/holds with `spec_seconds`, and `result` from the runner). `gate`: M6a
`vc-gate RUN_DIR --events {events}`; exit 0/1/3; the loop reads `qa/report.json`.

### viewer review and fixer (models; F-16)

Viewer review (docs/steps/review.md, `loop/vcloop/review.py`, after every gate PASS): the loop samples the
delivered video into contact sheets and writes `<run_dir>/review/request.json` `{steps, sheets, prompt, model,
privacy, out}`; the reviewer writes `out` = `<run_dir>/review/result.json` `{steps: [{step, expected_visible}],
findings: [{t, t_end, step, region, box, category, severity: blocker|minor, what}], model, tokens, seconds,
agent_cost_usd?}`. A blocker, a hidden expected state or a missing/invalid answer fails the take (like a gate FAIL);
minors go into the report; each blocker becomes a candidate in `<run_dir>/review/ledger-candidates.md`. Timing
phase `viewer review` records tokens, model seconds and cost. `bin/vc-review headless REQUEST` = the same with
`claude -p` (one turn, sheets inline).
Fixer request `<job>/fixes/request-<n>.json`: `{mode: fix|propose, spec_path, source (the failing take or dry run),
take_log, history, flake_available, may_change, never, answer (schema), ledger {file, candidates_path, candidates},
out}` → the fixer EDITS the spec (only the how) and writes `out` = `{kind: fix|flake|stop, hypothesis, change {what,
where, old, new, why}, stop {reason: login|write|mitigation|qa|app_bug|recorder_down|substance, detail}, next,
ledger {title, root_cause, scope, lives_in}, model, agent_cost_usd?}`. Each accepted fix or flake retake is appended
to FIXES-LEDGER.md (`VC_LEDGER` / components `"ledger"` override the path) under "Loop fixes" with the next FX id;
the symptom comes from the review's ledger candidates (else the gate), and the next judged take fills in the evidence
(take log `ledger_entries`). The loop, not the
fixer, starts every retake. It refuses (reverts, then stops) a fix that changes the contract (stop 1), makes the spec
invalid or changes a protected file (stop 7), a second flake (stop 7), or a note without an edit (stop 7).

## Stop points (F-22) and answers

| # | Attended answer (`resume --answer`) | Unattended |
|---|---|---|
| 1 contract changed / re-approval | `approved` after `vc-spec approve` | ends with a report |
| 2 login | `logged-in` after the remote-view login | ends (no human login path) |
| 3 cap | `raise-cap=N`, `change-spec`, `accept` | NOT A HIT delivered, cleanup, ends |
| 4 write not approved / writes_allowed off | `approved` | ends, never writes |
| 5 mitigation outside D-33 | `approved` | ends (S2) |
| 6 dry-run cap (D-41) | `raise-dry-cap=N`, `change-spec` | ends |
| 7 fixer out of scope, precondition, recorder down, budget | `continue` | ends |
| 8 promotion (F-35) | – | S2: listed as none |
| 9 cleanup without a safe path | – | items left and listed |

Any stop can be answered `abort`. An ended unattended job can be resumed attended: `resume --attended --answer …`.

## Job folder `<runs>/jobs/<job>/`

`state.json` (atomic, after every phase: counters, next phase, knobs, fingerprints, stop, created items, phase
sequence), `components.json`, `plan.txt`, `timing.jsonl` (F-28, one record per phase and per take), `take-log.jsonl`
(F-30), `fixes/` (requests, fix notes, spec backups), `logs/`, `question.md` (attended stop), `report.md` (delivery
and stop report, F-31), `deliver/<spec>[-NOT-A-HIT].mp4`. Run folders stay M1's `<runs>/<run_id>/` with
`run_id = <job>-d<n> | -t<k> | -c1`.

## Cleanup (C-34, OD-26)

Only items in the job's own tracked list (`created_items`, from the runner's results), only when the write has a
`cleanup` path and the app profile says `cleanup.safe_delete: true`; the delete control is scoped to the item's
unique name and is the only approved label for that cleanup step. Everything else is left and listed.

## Tests

`cd tests/loop && python3 -m unittest` (stubs, ~20 s). `taskset -c 8-15 python3 tests/loop/e2e_real.py` runs
scenarios (a)–(e) with the synthetic video and the real M4 cutter and M6a gate (~3 min).
