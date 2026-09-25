# Step: run the loop (phase 2)

The loop (`bin/vc-loop`, reference `docs/loop.md`) owns the take counter, the phase order, the logs and the best take.
You only start it, answer its handoffs and relay its stops.

## Start

1. Components config: `config/components.json` (create it from `config/components.example.json` if missing).
2. Knobs: take them from the request and the spec's `[knobs]`; pass each as `--knob name=value`.
3. Run `bin/vc-loop run SPEC --components config/components.json [--knob …]`. Its first output is the plan
   (`<job>/plan.txt`: spec, every knob with its value, stop points, login mode, writes, estimated time and cost).
   Print that plan block to the user unchanged, then go on without waiting.

## Exit codes

| Code | Meaning | Do |
|---|---|---|
| 0 | done | `docs/steps/deliver.md` |
| 10 | handoff | see below, then `bin/vc-loop resume JOB_DIR` |
| 20 | attended stop | show `<job>/question.md`, wait for the answer, `bin/vc-loop resume JOB_DIR --answer <option>` |
| 21 | ended at a stop point | `docs/steps/deliver.md` (the report is a stop report) |
| 2 | spec refused | back to `docs/steps/align.md` |

## Handoffs (exit 10)

The loop's `HANDOFF` line (also `handoff` in `<job>/state.json`) names the request file and the result file it
waits for.

- **Viewer review** (after every gate PASS; part of every run): `docs/steps/review.md`.
- **Fixer** (a take or dry run failed, or the cap was reached): start the `fixer` agent of this plugin with the
  request path. It reads `docs/steps/on-fail.md` itself; you need not. Model: the `fixer_model` knob; `orchestrator` means the default (inherit), any
  other value is passed as the agent's model. The fixer edits and writes the fix note; it never films. Do not edit
  the spec yourself in its place.

Then resume. Never start a retake, dry run, cut or gate run yourself; the loop does, after it has checked the fix.

## Resume

A stopped or restarted session: `bin/vc-loop status JOB_DIR`, then `resume` as above. The count continues from disk.
