# The use-case spec (F-07, F-08): format `vc-spec/1`

Owner: milestone M5. One TOML file per video: plain text, diffable, checked by `bin/vc-spec validate`. Examples:
`specs/golden/c1-four-angles.toml`, `c2-find-and-read.toml`, `c3-correction-loop.toml` (acceptance cases 1–3) and
`specs/examples/w-fixture-write.toml` (case W, the write case on the fixture page).

TOML rule to remember: top-level keys (`deny`, `site_switches`, `writes`, `preconditions`) must come before the first
`[table]`. `[approval]` must be the last table (the approve command rewrites it).

## Groups (all required; removing one fails validation)

| Key | F-07 group | Content |
|---|---|---|
| `format`, `name`, `title`, `purpose` | identity | `name` is a slug and the run-id prefix |
| `context` | context | one sentence: the task is intended, and whether anything changes |
| `length = [min, max]` | length | seconds; above 60 needs `length_reason` (Q-13) |
| `[params]` | parameters | every account-specific value; used as `{name}` anywhere in the spec |
| `preconditions = [...]` / `[[preconditions]]` | preconditions | `what`, `by` = `setup`/`human`, optional `check` |
| `[start]` | start | `url`, `ready` (checks), `warm` (routes warmed off camera), `hold` (landing hold), optional `[start.setup]` (below) |
| `[[steps]]` | steps | see below; 3–7 is the default (more gives a warning, not an error) |
| `writes = []` / `[[writes]]` | writes | `id`, `step`, `changes`, `labels` (approved own labels), `cleanup` (safe delete path) |
| `deny = [...]` | deny list | labels added to the built-in list and the profile's additions (C-08) |
| `[privacy]` | privacy | `account`, `shown`, `why_ok` (C-35, no blur in v1) |
| `site_switches = [...]` | site switches | `{ step, site }`; the only place a cross-fade is allowed |
| `[approval]` | approval | written by `vc-spec approve`: approver, time, mode, contract fingerprint, write approvals, assumptions |
| `[knobs]` (optional) | – | per-spec knob values (F-23), overridden by `vc-loop run --knob` |

## A step

```toml
[[steps]]
name = "step-dialog"                 # unique slug; names the clip, the QA lines and the take-log rows
does = 'click the step card "…", read its dialog, close it'   # human terms, shown in the approval view
hold = 1.0                           # the step-end hold (Q-62); the last step: 12.0 and hold_kind = "final"
hold_kind = "step"                   # step | dialog | reading | final
write = "create-item"                # only for an approved write step
expect = [{ type = "dialog_closed" }]            # expected state at the end of the step (checked in the page and by QA)
  [[steps.actions]]                  # one step may hold several actions
  op = "click"                       # jev: click | type | select; camera: pan | reveal | hold | wait
  control = { label_contains = "…", within = "diagram" }   # M2 target: label | label_contains | label_regex, role, within
  where = "the first step card in the swimlane diagram"    # where it sits, in human terms
  text = "exact text"                # type only; fixed, never generated; `{unique}` only in approved writes (C-34)
  done = [{ type = "dialog_open", value = "…" }]           # done condition of this action (default: the step's expect)
  wait_before = [{ type = "…" }]     # optional readiness condition before jev decides (a "how" the fixer may add)
  copies = "{value}"                 # optional: this action copies that value; typing it later is a paste (Q-55)
  pause_animations = true            # optional, pan/reveal only: pause the page's own animations during the move (Q-31)
  hold = 3.0                         # optional hold after this action (>= 1 s)
  hold_kind = "dialog"
```

Off-camera setup (`[start.setup]`, optional): one step-shaped table (`name`, `does`, `expect`, `write`,
`[[start.setup.actions]]` with click/select only) that runs after the start view is ready and before the cursor is
installed and the recording starts, with the on-camera hooks off; then its `expect` and `start.ready` must hold. It is
how an approved write that only prepares the start view (e.g. c2's "Neues Gespräch" for an empty start page) stays off
camera: its `[[writes]]` entry names the setup's `name` as `step` (with `creates = false` when nothing is created).
It is part of the contract (only when present) and is checked like a step (F-10.write, C-08, D-11).

Checks use the jev wrapper's vocabulary (M2): `url_contains`, `url_regex`, `text_visible`, `text_absent`,
`dialog_open` (optional `value`), `dialog_closed`, `element_visible` (`selector`/`role`/`label`/`label_contains`,
optional `count`), `count`, `title_contains`. Camera actions: `pan`/`reveal` need `to` (a check that becomes true),
optional `direction`, `seconds` (the camera keeps Q-31's 40 px/frame limit and a 400 px/s mean by default;
`vc/overlay/camera.py`).

Runtime placeholders the loop fills: `{unique}` (a new name per dry run/take: `<spec>-<job stamp>-<d1|t2>`) and
`{item}` (in a write's `cleanup`, the created item's name).

## Contract and fingerprint (F-06)

The contract is: every step's `name`, `does`, `expect` (parameters filled), `write` flag and typed text; every write's
`id`, `changes`, `step`; the final hold; the length range. `vc-spec fingerprint` hashes it (`sha256:` + 16 hex).
Everything else is the *how* (controls, `where`, `done`, `wait_before`, mid-video holds, warm-ups, knobs) and may be
changed by the fixer. A take whose contract differs from the approved fingerprint is never a hit.

## Validation (F-10) — rules and their ids

`F-07.groups`, `F-07.*` (identity, length, preconditions, start, sites), `F-08.name` (slug, unique), `F-08.expect`,
`F-08.actor`, `F-08.control` (needs a label and `where`), `F-08.text`, `F-08.camera`, `F-08.final` (12 s or a
`final_hold_reason`), `Q-62` (holds >= 1 s mid-video), `F-10.params` (unfilled `{…}`), `F-10.write` (a write-looking
control or typed text on a step that is not an approved write), `C-08` (the control hits the never-offer list),
`C-34` (a write without recorded approval; a creating write without `{unique}`), `D-11` (the control matches 0 or 2+
elements, or only a denied one, in the latest exploration record `specs/<dir>/explore/<name>/*.json`), `F-10.length`
(estimate outside the range), `F-36` (no app profile for a site), `C-35` (privacy fields). Every message names the rule
and, where there is one, the step. Errors block approval and filming; warnings do not.

Length estimate (F-05): landing hold + every hold + `action_time_s` per jev action + 37 ms per typed character +
camera `seconds`.

Exploration record (input of D-11, output of D-10 exploration): `{"spec", "observations": [{"step", "action"
(0-based index in the step), "url", "actions": [M2-enriched actions: kind, role, label, own_label, context, node,
on_screen]}]}`. The committed records under `specs/*/explore/` are **synthetic** (`"synthetic": true`, the validator
warns) until the browser is available; real exploration must replace them before the golden cases are filmed.

## Commands

```sh
bin/vc-spec validate SPEC [--draft] [--explore DIR] [--json]   # exit 1 on any error
bin/vc-spec view SPEC                                           # the approval view (F-05), generated
bin/vc-spec approve SPEC --by NAME [--approve-write ID]...      # attended approval (F-06, C-34)
bin/vc-spec approve SPEC --unattended --instruction FILE [--assume TEXT]...   # F-37; a write counts only when the
                                                                # instruction says "approve write <id>"
bin/vc-spec fingerprint|contract SPEC
bin/vc-spec diff SPEC                                           # vs the approved snapshot; exit 1 if the contract changed
```

Approval writes the `[approval]` table and a snapshot under `<spec dir>/.approved/` (`<name>.toml`,
`<name>.contract.json`, and `<name>.events.jsonl` with the view/approve times for the F-28 "approval (human)" record).
