# Step: align the spec (phase 1)

Goal: one approved spec file. Format and commands: `docs/spec-format.md`. Shapes to copy: `specs/golden/*.toml`,
`specs/examples/*.toml`. App profiles: `profiles/<app>/profile.json`; quirk records: `specs/quirks/`.

1. **Start setup alongside.** In the background: `bin/vc-env preflight`, then `bin/vc-env up` if the recorder is not
   running. Drafting does not need the browser; do not wait for setup.
2. **Pin down the one idea.** The one thing the viewer learns, the app and start view, the account and data (fine to
   show? v1 has no blur), read-only or which writes. For a diff or merge-request text, film the user-visible change,
   not the code.
   - Attended: ask only when really ambiguous, about 3 questions at most.
   - Unattended (`human_reachable=no`): ask nothing. Take answers from the instruction and the README checklist
     defaults; note each assumption. If the app, the idea or the write decision is missing, end with a stop report.
3. **Draft** `specs/<dir>/<name>.toml`: 3 to 7 steps, 30 to 60 s, controls described as a human sees them, an
   expected state per step, holds per the style defaults. Put account-specific values in `[params]`.
4. **Explore read-only** once the recorder is up: find each control's exact visible label and each state's exact
   text and URL through jev, under the browser lock. Write the record to `specs/<dir>/explore/<name>/`. Never click a
   write control while exploring. Tool (inside the recorder, repo at `/opt/vc`):
   `bin/vc-lock explore bin/vc-env exec python3 -m vcloop.explore look [--url U] [--step JSON]... [--shot /runs/x.png]`
   lists the controls jev sees (own label, role, context) and the page text; `... explore spec /opt/vc/SPEC --out
   /runs/explore/rec/<name>` walks the spec through jev like a dry run and writes the record (copy it into
   `specs/<dir>/explore/<name>/`).
5. **Validate** `bin/vc-spec validate SPEC --explore specs/<dir>/explore/<name>`; fix every error (each names its rule
   and step). Warnings may stay.
6. **Approve.**
   - Attended: show `bin/vc-spec view SPEC` verbatim, wait for an explicit OK, then
     `bin/vc-spec approve SPEC --by <name> [--approve-write ID]...`.
   - Unattended: save the instruction text to a file and run
     `bin/vc-spec approve SPEC --unattended --instruction FILE --assume "<assumption>"...` once. Never re-approve
     yourself after a later change of substance; that ends the run.

**Adjust an approved spec:** edit it, validate (step 5), then `bin/vc-spec diff SPEC` against the approved snapshot.
Exit 1 means the contract changed: show the diff and the view and get a new approval (step 6) before any filming.

A "spec only" request (skill `write-demo-spec`) ends here: report the spec path and its approval view or diff.
Otherwise go to `docs/steps/run.md`.
