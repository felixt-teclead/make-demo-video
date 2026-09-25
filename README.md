# make-demo-video

A Claude Code plugin (`vc-v1`) that turns a feature into a short, checked, silent MP4 demo video of your web app.
Release v1.0, MIT license.

## Quickstart (v1.0)

### Prerequisites

- Linux x86_64 host with Docker and Docker Compose ≥ 2.24 (`docker compose version`)
- about 5 GB free disk for the recorder image (the build is refused below 5 GB)
- Ubuntu ≥ 23.10: allow unprivileged user namespaces for Chrome's sandbox:
  `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` (persist it in `/etc/sysctl.d/`)
- Claude Code (`claude` on PATH)
- your own OpenRouter API key (jev's page decisions run through it)
- an account on the web app you want to film, and an app profile for it (see [Add your app](#add-your-app))

### Setup (six steps)

1. **Env file** at `~/.config/demo-video/env`, mode 600, holding only these variables (the user and password
   variable names are the ones your login profile names, see `profiles/example/login.json`):
   ```sh
   OPENROUTER_API_KEY=...
   APP_USER=...
   APP_PASS=...
   ```
   `chmod 600 ~/.config/demo-video/env`. It is mounted read-only into the container; the plugin never reads or prints it.
2. **Settings:** `bin/vc-env init` creates `settings.env` (git-ignored); set `VC_ENV_FILE=~/.config/demo-video/env`
   there. Adjust ports (`VC_CONTROL_PORT`, `VC_VNC_PORT`) and `VC_CPUSET` if they clash on your host.
3. **Build:** `bin/vc-env build` (recorder image: Chrome, noVNC, jev, ffmpeg).
4. **Start and check:** `bin/vc-env up`, then `bin/vc-env check` (1920×1080 page area, fonts, locale,
   `chrome_sandbox_on`).
5. **Log in to your app**, either scripted from the env file:
   `bin/vc-lock login bin/vc-env exec python3 /opt/vc/env/login/scripted_login.py --profile /opt/vc/profiles/<app>/login.json`
   or by hand in the remote view: `bin/vc-env url` prints the noVNC address; the password is in `state/vnc-password`.
   The session is kept in the container's browser profile.
6. **Make a video:** `claude --plugin-dir .`, then `/make-demo-video <what to show>`
   (e.g. `/make-demo-video show how to open a process and switch between BPMN, RACI and swimlanes`). The skill
   aligns a spec with you, asks for approval, then films, cuts, checks and delivers.

`bin/vc-golive --approve-as <you>` runs steps 3–5 plus golden case 1 in one go (once the golden cases point at your
app, see [Golden cases](#golden-cases)).

### What a run delivers

Per job, under `runs/jobs/<spec>-<timestamp>/`:
- `deliver/<spec>.mp4`: the silent 1920×1080 video (`<spec>-NOT-A-HIT.mp4` when no take hit within the cap)
- `report.md`: verdict, takes used, full QA gate report, timing per phase, jev decisions and cost, writes and
  created items
- `take-log.jsonl`, `timing.jsonl`, `logs/`; each take's raw recording, clips and QA report in `runs/<job>-t<n>/`

### Typical timings (final v1.0 reruns of the three golden cases)

About 103–122 s machine time per job (dry run ~10 s, take ~43–46 s, cut ~15 s, QA ~12 s, frame check ~17 s),
1 take each, jev ~16 decisions for about $0.001. Human time (spec approval, viewer review answers) comes on top.

### Fixes ledger

`FIXES-LEDGER.md` records every fix the system made (FX-01 …): symptom, root cause, change and commit, evidence,
scope guess (COMMON vs SPECIFIC) and where it lives. When a take fails the gate or the viewer review finds a blocker,
the fixer subagent proposes a fix; the loop applies accepted fixes and appends the ledger entry itself (the fixer
never edits the ledger). The "owner decision" field stays blank until the owner reviews each entry and decides
whether it becomes a known fix in the main plugin or stays local to its app/case.

## Layout (one repo)

| Path | What |
|---|---|
| `.claude-plugin/plugin.json`, `skills/make-demo-video/`, `skills/write-demo-spec/`, `docs/steps/`, `docs/house-rules.md`, `agents/fixer.md`, `hooks/` | the plugin: two skills (end-to-end video; spec only), lean step docs read by path, the one shared reference, the fixer subagent, the F-19 guards |
| `env/` | the isolated recorder environment: `Dockerfile`, `compose.yaml`, **the one requirements file** `env/requirements.txt`, the recorder API, the Chrome launch, login, checks; `env/www/fixture/` is the Q-91 fixture page |
| `jev/vcjev/` | the jev wrapper (C-01 to C-14) |
| `vc/overlay/` | cursor, glide, hover, press, ripple, typing, paste; the camera (pans/reveals); `vc_hooks.py` = `VC_HOOKS` |
| `vc/cut/` | the cutter and joiner; `vc/events.py` the one event stream (`docs/events.md`) |
| `loop/vcloop/` | spec format, approval, knob table (`knobs.py`, the one behaviour table, F-23), the deterministic loop, the take runner |
| `gate/vcgate/` | the QA gate (S1 + the S2 video-only checks Q-23, Q-03) and its Q-90 fixture runner |
| `profiles/`, `specs/` | app profiles (the example app, the fixture page), golden and example specs, quirk records |
| `bin/` | `vc-env`, `vc-lock`, `vc-preflight`, `vc-spec`, `vc-loop`, `vc-cut`, `vc-gate`, `vc-gate-fixtures`, `vc-selftest`, `vc-golive` |
| `docs/` | interfaces: `interfaces.md` (environment), `jev-interface.md`, `overlay.md`, `cut-interfaces.md`, `gate.md`, `loop.md`, `spec-format.md`, `events.md` |

**One settings file:** `settings.env` (from `settings.example.env`, git-ignored) holds every host-specific value (C-21);
the host tools and the container read it. Behaviour knobs live only in `loop/vcloop/knobs.py`; models in
`config/models.json`. In the image: `PYTHONPATH=/opt/vc:/opt/vc/jev:/opt/vc/loop:/opt/vc/gate`,
`VC_HOOKS=vc.overlay.vc_hooks`, packages from `env/requirements.txt` (jev wrapper deps, pinned jev-ultrafast, numpy).

## Use as a plugin

`claude --plugin-dir .` (validated with `claude plugin validate .`). Say "make a demo video of …": the entry skill
`make-demo-video` routes to `docs/steps/*.md`. Say "write / adjust / approve / diff the spec …" (no filming): the
spec skill `write-demo-spec` follows the same `docs/steps/align.md` (the one source of the spec rules) and stops after
approval or the diff. The guards (`hooks/guard.py`) block edits of the QA definition and the
approvals, any read of the env file, profile or keys, and anything but read-only helpers for the fixer. Only the owner
can lift the QA block, by starting the session with `VC_OWNER_QA_UNLOCK=1`.

## Tests without a browser

`bin/vc-selftest` runs every suite that needs no Chrome: jev wrapper, overlay, environment (lock, Chrome launch
config), loop, integration (M2 x M3 hooks, camera, one event stream, specs incl. case W approval), fixture page,
plugin (hooks, layout) + `claude plugin validate`, gate unit tests, the S2 gate tests (Q-23 skeletons, Q-03 raw vs
cut), the Q-90 gate fixture suite (S1 + S2), the cutter's verify suite, and the loop's end-to-end scenarios a–e
(synthetic video, real cutter and gate). `--quick` skips the five media suites. It creates `.venv` with numpy on first use (`tests/tools/host_venv.py`).

## Go live

Chrome runs with its own sandbox ON (owner decision 2026-09-25, option a): `env/compose.yaml` starts the recorder
with `env/seccomp-chrome.json`, Docker's default seccomp profile plus an allow for `clone/clone3/unshare/setns`
(docs/interfaces.md section 10). The host must allow unprivileged user namespaces
(`kernel.apparmor_restrict_unprivileged_userns = 0` on Ubuntu 23.10+); `bin/vc-env check` reports `chrome_sandbox_on`.

```sh
bin/vc-env init                       # once: creates settings.env; set VC_ENV_FILE to the env file's PATH (mode 600)
bin/vc-golive --approve-as <owner>    # build, up, browser check, scripted login, film golden case 1, gate it
```

`bin/vc-golive` steps: preflight (settings, env file mode, loopback ports) → image build (refused below 5 GB free) →
`bin/vc-env up` → DevTools and `bin/vc-env check` (one tab whose page area is exactly the 1920×1080 screen, fonts,
locale, sandbox on; exit 30 when Chrome cannot start) → scripted login under the browser lock (`env/login/scripted_login.py`, credentials read inside
the container from the env file; exit 20 = log in once in the remote view from `bin/vc-env url`, then re-run with
`--skip-build`) → validate and (with `--approve-as`) approve `specs/golden/c1-four-angles.toml` → `bin/vc-loop run`
(dry run, take, cut, QA) → prints the gate report. It ends at the loop's viewer-review handoff (exit 10): the
orchestrator continues with `bin/vc-loop resume <job dir>` (`docs/steps/run.md`). Exit 0 = gate PASS.

Before filming the golden cases for real, replace the synthetic exploration records under `specs/golden/explore/`
with real ones (the validator warns, D-10).

## Add your app

Everything app-specific lives in an app profile and its quirk record; the plugin core knows no app (C-40).
`profiles/example/` is a template for a web app at `https://app.example.com`:

1. Copy `profiles/example/` to `profiles/<your-app>/` and edit `profile.json`:
   - `name` and `hosts`: your app's host names (`"app.your-company.com"`, `"*.your-company.com"`); a spec's start URL
     picks the profile by its host;
   - `deny_extra`: labels of controls that write, share, delete or show other people's data; jev is never offered them;
   - `login.url` and `login.check`: the app's start page and the login profile file;
   - `quirk_record`: `specs/quirks/<your-host>.md`;
   - `view_start` (optional): start-position rules for views that mount at an awkward position, each citing its quirk
     record entry; delete the example rule if your app has no such view.
2. Edit `login.json` for the optional scripted login: `start_url` (a page that redirects to the login form when logged
   out), the CSS selectors `form.user`, `form.password`, `form.submit` (`two_step: true` when the password field
   appears only after the user name), `logged_in.url_prefix`, and the env file variable names in `credentials`.
   Without it, log in once by hand in the remote view.
3. Copy `specs/quirks/app.example.com.md` to `specs/quirks/<your-host>.md` and record your app's quirks there (id,
   views, symptom, detection, mitigation), only from measurements.
4. `bin/vc-spec validate <spec>` resolves the profile from the spec's start URL.

## Golden cases

`specs/golden/*.toml` are three example cases written against the example app (a process-modelling app with a
German UI). They use placeholders: `https://app.example.com/...` and `<process-id>` in `start.warm`. To film them,
point them at your own app, replace `<process-id>` with your own process (record) ids, explore them with jev and
re-approve; their exploration records under `specs/golden/explore/` are synthetic. `specs/examples/` holds specs for
the local fixture page, which runs without any account.

## Private test data

Some regression tests use screen recordings of a private app that are not in this repo: the Q-90 gate fixture clips
(`VC_FIXTURES`), the cutter's acceptance data (`VC_ACCEPTANCE`: `visual-baselines/`, `fixtures/`), and the recorded
clips and frames under `gate/tests/data/` and `tests/cut/data/`. Without them those tests and `bin/vc-gate-fixtures`
skip with a message; everything else runs.

## License

MIT, see `LICENSE`.

## Unattended-run checklist (F-37)

An instruction for a run with `human_reachable=no` must answer up front:
1. the app and start URL, and that its profile exists (`profiles/<app>/profile.json`);
2. the one thing the viewer should learn, and the steps or the feature to show;
3. the account and data to use, and why they are fine to show (C-35);
4. read-only, or each write named and approved (`approve write <id>`), with its cleanup (C-34);
5. the login path: a live stored session or credentials in the env file (F-26);
6. the target length, the final hold and any knob away from its default (F-23), including the take cap;
7. where to deliver the video and the report.

A missing answer with a documented default takes the default and is recorded as an assumption
(`vc-spec approve --unattended --assume …`, listed in the delivery). A missing answer without a default (items 1, 2
and 4) ends the run at alignment with a stop report.
