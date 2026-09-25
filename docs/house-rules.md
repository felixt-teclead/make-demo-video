# House rules

These rules apply to every skill, step doc and agent of this plugin. They live only here; other files point to them
by number. Hooks (`hooks/guard.py`) enforce H2, H5 and H6 mechanically.

**H1 · Read-only by default.** Never press a control that creates, changes, deletes, shares or sends anything,
unless the spec names that write and it carries an approval (attended: the owner's OK; unattended: the instruction
says `approve write <id>`). Items a take creates are cleaned up by the loop, and only those. Anything else that
looks like a write is a stop point, not a judgement call.

**H2 · No credentials in any model context.** Never read, print, copy or archive the secrets env file (the path in
`VC_ENV_FILE`, mounted as `/run/secrets/vc.env`), the browser profile, cookies or API keys, and never paste them into a
prompt, spec, log or commit. Login is the human over the remote view, or the scripted login that reads the env file
itself. Refer to the env file by path only.

**H3 · jev for all browser work.** Exploration, login checks, dry runs, takes and cleanup act on the page only
through jev (the `vcjev` wrapper, via the loop or `bin/vc-env exec`). Direct DevTools access only reads state
(URL, DOM, geometry, screenshots) or hands over a file. No other browser-automation tool.

**H4 · One browser, one lock.** There is exactly one recorder browser. Every browser use runs under the browser lock
(`bin/vc-lock <label> …`, or the loop, which takes it itself). Never run two browser jobs at once, and never run cut,
QA or join while a take is being filmed.

**H5 · Never touch the QA definition.** The QA checks and thresholds, the gate fixtures and the cutter's quality
rules (`gate/`, `vc/cut/`, `bin/vc-gate*`, `bin/vc-cut`) and these hooks are the judge. A failing take is fixed in its
inputs: the spec's *how*, waits, holds, marks, or a plain runner bug. Patch the spec, not the judge. Only the owner can
lift this, by starting the session with `VC_OWNER_QA_UNLOCK=1`; an agent never sets it.

**H6 · Approval belongs to the approver.** The spec's `[approval]` table and `specs/**/.approved/` are written only by
`bin/vc-spec approve`, run by the orchestrator after the owner's OK (or its own recorded self-approval in unattended
mode). A change to the contract (steps and their intent, expected states, writes, final hold, length range) needs a
new approval.

**H7 · Fixed style defaults.** Holds follow Q-62: landing 1.0 s, step end 1.0 s, dialog about 3 s, reading 1.5 to
6 s, final hold 12 s (shorter only with a written reason in the spec). Typing is flat and fast. No human mimicry: no
random delays, no typos, no curved or wobbly mouse paths. Readability yes, realism no. Output is the MP4 and its clips.
