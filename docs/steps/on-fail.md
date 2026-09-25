# On a FAIL: what the violations mean and the usual fixes

Read the failing run's `qa/report.txt` (or the dry run's `result.json`) and the take log. One violation = one defect;
warnings never fail a take. Pick the first real cause, not the last symptom.

| Check | Means | Usual fix in the spec (the *how*) |
|---|---|---|
| runner: step not verified, `failed_step` | the control was not found or the done state never came | describe the control closer to its visible label and place; add `wait_before` a readiness check; widen a `done` check to the visible state actually reached |
| `login_required`, precondition failed | outside the take's control | stop (`login`) or report the precondition; never a retake |
| Q-02 ABORT | a clip could not be measured | not a spec problem: stop `recorder_down` if the raw video is missing |
| Q-10 / Q-11 | a step clip is missing, or a step missed its expected state | as runner above; never drop the step or its expected state |
| Q-13 length | too long or short | adjust mid-video holds within the style defaults, or speed of camera moves; never the final hold or the range |
| Q-14 clip collapses | a step shows nothing moving | the step has no visible action: add a reveal or a hold on real content |
| Q-20 / Q-21 / Q-22 solid, blank or half-painted frames | the page painted white/black or partly during a navigation | warm the route off camera (`[start] warm`); wait for the loaded state before the next action |
| Q-30 unexplained jump | content changed with no click, mark or pan nearby | the page updates late: wait for it before acting, or warm it |
| Q-31 / Q-34 / Q-37 pan problems | uneven, reversing or stuttering motion; no still beat after it | slower camera `seconds`; reveal a nearer target; add a hold after the pan |
| Q-40 to Q-45 cursor and ripple | a second cursor, a cursor jump, a missing or stuck ripple | usually a runner or overlay bug: smallest runner fix, else stop `qa` |
| Q-51 / Q-62 holds | a hold is off by more than 0.25 s, or the final hold is not still for 12 s | remove what moves during the hold (an animation, a tooltip: park or wait); keep the hold values |

| viewer review blocker | a viewer sees a defect the gate passed (stray UI, hidden content, a hole, a missing state) | read `ledger.candidates` in the fixer request (one candidate per blocker: time, region, what, sheets); fix the first blocker's cause the same way as the matching gate row above |

Viewer-review blockers arrive as FIXES-LEDGER candidates: the request's `ledger.candidates` (the text of
`<run_dir>/review/ledger-candidates.md`). Take the first candidate as the symptom, name its root cause, and fix it. Fast
typing and the ~12 s final hold are house style: a candidate about them is no defect; answer `stop` with reason `qa`.

Every accepted fix (and a flake retake) becomes one FIXES-LEDGER entry, same format as the existing FX entries. Fill
the note's `ledger` object: `title` (short name of the fix), `root_cause`, `scope` (COMMON if every app or case would
hit it, else SPECIFIC) and `lives_in` (spec, quirk, profile, core code (<part>)). The loop writes the entry with the
next free FX id into `ledger.file` (date, case, symptom from the candidates or the gate, the applied change, evidence
"pending") and fills in the evidence from the retake's gate and viewer review. Do not edit the ledger yourself.

Scope (house rules H1, H5, H6): if the cause is the app, the login, the recorder, a needed write, a change of what
the video shows, or the check itself looking wrong, stop with that reason instead of fixing. An unchanged retake is
allowed once, only for a clear flake (a network blip) with evidence.
