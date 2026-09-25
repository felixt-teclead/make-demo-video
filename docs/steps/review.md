# Step: viewer review (handoff)

Part of every run. The loop runs it after each gate PASS, before delivery; it hands off with
`<run_dir>/review/request.json`. It can fail a PASS, never pass a FAIL.

Request: `{steps: [{step, start, end, expected}], sheets: [{path, kind, tiles}], prompt, model, privacy, out}`.
The loop has already sampled the delivered video: every click at +0/+0.2/+0.5/+1 s, each cover or speed-up window
(start, middle, end, +2 frames, +0.2 s), each pan (0/25/50/75/100 % and +0.5 s), each hold's start and end, the
joins, the first and last frame, and 1 fps in between. `timeline` sheets show the whole frame; `zoom` sheets show the
native pixels around each click.

1. Model: the request's `model`. `orchestrator` = do it yourself; any other value = a subagent on that model given
   only this doc and the request. Unattended, `bin/vc-review headless REQUEST` runs it with `claude -p`.
2. Follow the request's `prompt` (the checklist) and open every sheet. If `sample_error` is set, report one blocker
   saying so.
3. Write `out` as the JSON the prompt asks for, and add `model`, `seconds` and `tokens` (`{input, output}`, as the
   harness reports them). Add `agent_cost_usd` only if the harness reports it. Never guess.
4. Run `bin/vc-loop resume JOB_DIR`.

What the loop does with it: any blocker (or a step whose expected state is not visible) fails the take into the fix
loop, as a gate FAIL does; it writes one FIXES-LEDGER candidate per blocker to `<run_dir>/review/ledger-candidates.md`.
The fixer request carries them (`ledger.candidates`), and the loop turns the accepted fix into a `FIXES-LEDGER.md`
entry (docs/steps/on-fail.md). Minor findings go into the delivery report.

House style is never a finding: fast typing and the ~12 s final hold (docs/house-rules.md). The prompt says so, and
`judge()` also drops a `pace` finding about either (listed under `house_style`, not counted).

Knobs: `review_model` (default `orchestrator`), `review_privacy` (default off).
