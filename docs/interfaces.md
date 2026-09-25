# Interfaces of the environment (M1)

Status: v0.2 (M1). Owner of this file: milestone M1 (environment and recorder). Other milestones read it; changes
to anything below are announced here first. Requirement IDs refer to `vc-v1-spec`.

## 1. Settings file (C-21)

One settings file holds every host-specific value. It is a plain `KEY=VALUE` file (no quotes, no spaces around `=`,
no shell expansion), read by the host scripts (POSIX sh), by `docker compose --env-file`, and by Python.

- Committed example: `settings.example.env`. Live file: `settings.env` in the repo root (git-ignored), or the path
  in the environment variable `VC_SETTINGS`. `bin/vc-env init` copies the example if the live file is missing.
- Relative paths in the file are relative to the repo root.

| Key | Example | Meaning |
|---|---|---|
| `VC_PROJECT` | `vc-v1` | compose project name; prefix for volumes |
| `VC_CONTAINER_NAME` | `vc-v1-recorder` | the one recorder container (see section 2) |
| `VC_IMAGE` | `vc-v1-recorder:latest` | image tag built from `env/Dockerfile` |
| `VC_BIND` | `127.0.0.1` | host address every published port binds to (loopback only, C-17) |
| `VC_CONTROL_PORT` | `7811` | host port of the recorder control API |
| `VC_VNC_PORT` | `6811` | host port of the noVNC remote view (password protected) |
| `VC_CPUSET` | `8-15` | cpuset of the container |
| `VC_UID` / `VC_GID` | `1000` | user and group ID the container runs as (owns run files on the host) |
| `VC_LOCALE` | `de_DE.UTF-8` | OS locale in the container |
| `VC_LANG` | `de-DE` | browser UI language and Accept-Language |
| `VC_TZ` | `Europe/Berlin` | timezone |
| `VC_SCREEN` | `1920x1080` | virtual screen and recording size (Q-80: must stay 1920x1080) |
| `VC_FPS` | `30` | recording frame rate (Q-81: must stay 30) |
| `VC_CRF` | `18` | x264 CRF of the raw recording (C-16: at most 18) |
| `VC_ENV_FILE` | `~/.config/<you>/env` | path of the secrets env file (C-05), mode 600, outside every repo |
| `VC_RUNS_DIR` | `runs` | host directory of run folders (mounted at `/runs`) |
| `VC_STATE_DIR` | `state` | host directory for the lock, lock log and remote-view password (mounted at `/state`) |

## 2. Container

- Exactly one container, named `${VC_CONTAINER_NAME}` (default `vc-v1-recorder`), compose project `${VC_PROJECT}`,
  service name `recorder`. Built from `env/Dockerfile` (public base `debian:bookworm-slim` + Google Chrome stable).
- Volumes: `${VC_PROJECT}-profile` (named volume, the browser profile, mode 700, C-32; never mount it anywhere
  else), `${VC_RUNS_DIR}` → `/runs` (rw), `${VC_STATE_DIR}` → `/state` (rw), the repo root → `/opt/vc` (read-only,
  so plugin code runs in the container without a rebuild), `${VC_ENV_FILE}` → `/run/secrets/vc.env` (read-only).
- The secrets are **not** in the container environment. Only code that needs them (jev client: the key; scripted
  login: the credentials) opens `/run/secrets/vc.env`. Never print its values.
- Inside the container: user `vc` (`VC_UID:VC_GID`), `HOME=/home/vc`, display `:99` (Xvfb, 1920x1080x24),
  Python venv `/opt/venv` (packages from the one requirements file `env/requirements.txt`: jev wrapper + numpy),
  `PYTHONPATH=/opt/vc:/opt/vc/jev:/opt/vc/loop:/opt/vc/gate`, `VC_HOOKS=vc.overlay.vc_hooks`,
  `ffmpeg`/`ffprobe` on PATH (QA and cutting run inside the container, C-27).
- Useful env vars set in the container: `VC_CDP_URL=http://127.0.0.1:9222`, `VC_RECORDER_URL=http://127.0.0.1:7777`,
  `VC_RUNS=/runs`, `VC_STATE=/state`, `VC_SECRETS=/run/secrets/vc.env`, `DISPLAY=:99`.

## 3. Reaching the browser over the DevTools protocol

- Chrome listens on **127.0.0.1:9222 inside the container only**. The port is not published and does not answer from
  the host (C-17). Everything that talks CDP (jev wrapper M2, cursor overlay M3, checks) therefore runs **inside the
  container**:

  ```sh
  bin/vc-env exec python3 /opt/vc/<path/to/script.py> [args]        # runs as user vc, cwd /opt/vc
  bin/vc-env exec -i sh -c '...'                                   # interactive / stdin
  ```

  `vc-env exec` is `docker exec -u vc -w /opt/vc ${VC_CONTAINER_NAME} ...` with the settings applied. Scripts use
  `/opt/venv/bin/python` implicitly (`python3` on PATH inside the container is the venv).
- Endpoints: `http://127.0.0.1:9222/json/version` (browser websocket URL), `/json/list` (page targets). There is
  normally exactly one page target (kiosk tab, C-07).
- A tiny stdlib CDP client is available: `env/lib/vccdp.py` (`CDP.page()` connects to the first page target;
  `.call(method, **params)`, `.evaluate(expr)`, `.wait_event(name)`). Use it or your own library.
- The browser is restarted automatically by the supervisor if it exits (C-15). After a restart the websocket URL
  changes; re-read `/json/version`.
- A local static server inside the container serves `/opt/vc/env/www` at `http://127.0.0.1:8099/` (test pages:
  `/fontcheck.html`). Other milestones may add their own pages under their own dirs; ask M1 to add a mount if needed.

## 4. Recorder control API

HTTP + JSON, served by `env/recorder/recorder.py` on port 7777 in the container, published on
`${VC_BIND}:${VC_CONTROL_PORT}` on the host. Inside the container use `$VC_RECORDER_URL`. All times are epoch seconds
(float) from the one host clock; video times are seconds on the raw recording's clock (frame n shows at n/30).

| Method + path | Request body | Response |
|---|---|---|
| `GET /health` | – | `{"ok":true,"xvfb":true,"chrome":true,"cdp":true,"recording":null or run_id,"browser":{...settings summary}}` |
| `POST /record/start` | `{"run_id":"optional","label":"optional"}` | `{"run_id","dir","t0"}`; returns after the first frame is captured; `t0` = epoch of frame 0. 409 if already recording, or if the run folder already holds a recording (raw, events, manifest; Q-58). A folder that holds only the loop's `request.json` is used as is. |
| `POST /mark` | `{"name":"step-1","step":1,"t":optional epoch}` | `{"name","step","t","video_t","frame"}` — a step boundary on the video clock |
| `POST /hold` | `{"kind":"landing|step|dialog|reading|final","seconds":1.0,"step":1,"t":optional,"wait":false}` | `{"kind","seconds","t","video_t","frame"}`; with `"wait":true` the call returns after the hold elapsed |
| `POST /event` | `{"type":"click","step":1,"label":"...","x":..,"y":..,"t":click epoch,"glide_t":glide start epoch, ...}` | the stored event with `video_t`/`frame` (any `type`; OD-19 event log: clicks, typing, waits) |
| `POST /record/stop` | `{}` | the run manifest (below). ffmpeg gets `VC_REC_STOP_WAIT_S` (20 s) after `q`, then SIGINT, then it is killed (error 500, raw.mkv kept); the recorder never forgets an ffmpeg that still runs. |
| `GET /record/status` | – | `{"recording":run_id or null,"t0","elapsed","events":n}` |
| `GET /runs/<run_id>` | – | the run manifest |

Stored events never keep credentials in URLs (C-31): in `url`, `href`, `page_url` and `location` the userinfo is
dropped and credential-like query and fragment parameters (`code`, `state`, `*token`, `id_token`, `key`, `sig`, ...)
become `REDACTED`, in `events.jsonl` and `manifest.json`.

Shutdown (`docker stop`, compose `stop_grace_period: 30s`): the supervisor first finalises a running recording through
`/record/stop` (Xvfb still up), waits for the recorder, then stops Chrome, then the rest. So `raw.mp4` and
`manifest.json` exist after a stop mid-take. Test with the real image: `python3 -m unittest
tests.env.test_recorder_container` (needs docker; not part of `bin/vc-selftest`).

Errors: `{"ok":false,"error":"..."}` with status 400/404/409/500. The recorder does not take the lock; the caller does
(section 6). `t` lets the caller stamp an event with the exact epoch it happened (for example the moment the click was
dispatched); without it the server's receive time is used.

## 5. Run directory layout

One folder per run (take, dry run, recut): `${VC_RUNS_DIR}/<run_id>/` on the host = `/runs/<run_id>/` in the
container. `run_id` default: `YYYYmmdd-HHMMSS-<label>`. A recut always gets a new run folder (Q-58).

```
<run_id>/
  raw.mp4          raw recording: 1920x1080, 30 fps CFR, H.264 High, yuv420p, CRF 18, no audio, faststart
  events.jsonl     one JSON per line: {"type":"start|mark|hold|click|...","t","video_t","frame",...}
  manifest.json    written at stop (schema below)
  recorder.log     ffmpeg stderr
  browser.json     browser settings snapshot at start (version, flags, policies hash, window, DPR, locale)
  # written by later phases (owners: other milestones)
  take.log.jsonl   jev decisions etc. (M2)
  cut/             cutter work + cut record (cutter)
  clips/           delivered clips + clips/index.json (cutter)
  qa/              gate report (gate)
```

`manifest.json`:

```json
{"run_id":"...","raw":"raw.mp4","t0":1727200000.123,"fps":30,"width":1920,"height":1080,
 "duration":10.03,"frames":301,"started":"ISO","stopped":"ISO",
 "marks":[{"name":"step-1","step":1,"t":...,"video_t":0.53,"frame":16}],
 "holds":[{"kind":"step","step":1,"seconds":1.0,"video_t":3.2,"frame":96}],
 "events":[... all events ...],
 "segments":[{"index":0,"step":1,"name":"step-1","start_t":0.53,"end_t":4.9,"start_frame":16,"end_frame":147,
              "holds":[...holds inside...]}]}
```

`segments` are the raw per-step spans (from a step's mark to the next mark; the last one to the end of the
recording). Nothing before the first mark belongs to any segment (Q-15). The cutter turns segments into delivered clips.

Delivered-clip index (contract for the cutter and the gate): `clips/index.json` =
`{"run_id","source_run","clips":[{"index","step","name","file":"clips/01-<name>.mp4","duration","frames",
"holds":[{"kind","seconds","clip_t"}],"source":{"start_t","end_t"}}],"full":"full.mp4","full_duration"}`.

## 6. The browser lock (C-18)

- One exclusive `flock` on `${VC_STATE_DIR}/browser.lock` (`/state/browser.lock` in the container).
- Every browser use (explore, probe, login check, dry run, take, cleanup) runs under it:

  ```sh
  bin/vc-lock <label> <command> [args...]      # waits up to the lock timeout, exit 75 on timeout
  ```

  Timeout: `VC_LOCK_TIMEOUT` from the environment (the loop sets it from the knob `lock_timeout_s`, F-23), else an
  optional `VC_LOCK_TIMEOUT` line in the settings file, else the knob table default (600 s, `loop/vcloop/knobs.py`).
  In the container: `flock -w "$VC_LOCK_TIMEOUT" /state/browser.lock <cmd>` works too, but then write the lock log
  lines yourself; prefer `vc-lock` on the host (or `/opt/vc/bin/vc-lock` in the container).
- A command that runs inside the container through `docker exec` and may outlive its host client (the loop's
  dry run / take / cleanup) takes the lock INSIDE the container (`/opt/vc/bin/vc-lock`, same file through the
  `/state` mount): the lock then ends with the process that uses the browser, not with the host client (C-18).
- Lock log: `${VC_STATE_DIR}/lock.log`, one JSON per line:
  `{"t":epoch,"event":"wait|acquired|released|timeout","label","pid","host"}`. Two holders never overlap in it.

## 7. Host CLI (`bin/vc-env`)

`init`, `preflight`, `build`, `up`, `down`, `restart`, `status`, `health`, `url` (noVNC URL + where the password is),
`exec ...`, `check` (font/locale/dialog check, C-15), `ports` (loopback check, C-17), `size` (image size, C-22).
Preflight alone: `bin/vc-preflight`.

## 8. Remote view (login, C-17 / F-26)

noVNC at `http://${VC_BIND}:${VC_VNC_PORT}/vnc.html?autoconnect=1&resize=scale`. The VNC password is generated at
first start into `${VC_STATE_DIR}/vnc-password` (mode 600; x11vnc reads it with `-passwdfile`, so it is on no command
line); `bin/vc-env url` prints the URL and the path, not the
password. The login check itself is app-specific (profile, F-36); the environment only keeps the profile alive.

## 9. Login check and optional scripted login (F-26, C-31)

`bin/vc-lock login bin/vc-env exec python3 /opt/vc/env/login/scripted_login.py --profile /opt/vc/profiles/<app>/login.json [--check-only]`

Exit codes: 0 logged in, 20 human needed (2FA, captcha, form not understood, failed: use the remote view), 21 no
credentials configured (human path is the default), 22 refused because the recorder is recording or its status
cannot be read (fail closed), 1 error. Output is
one JSON line without any credential. App specifics (start URL, selectors, credential variable names) live in
`profiles/<app>/login.json` (C-40); the example is `profiles/example/login.json` (placeholder selectors).

## 10. Chrome's sandbox (owner decision 2026-09-25, option a)

Chrome's own sandbox needs user namespaces, which Docker's default seccomp profile blocks for a container without
`CAP_SYS_ADMIN`. The owner chose a custom seccomp profile that keeps the sandbox ON: `env/seccomp-chrome.json` is
moby's default profile at tag `docker-v29.8.1` (`vendor/github.com/moby/profiles/seccomp/default.json`, sha256
`536529b6…9864c74`) with exactly two changes: one added rule that allows `clone`, `clone3`, `setns` and `unshare`
unconditionally, and the removed rule that made `clone3` fail with `ENOSYS` without `CAP_SYS_ADMIN` (it contradicts
the allow). `env/compose.yaml` references it with `security_opt: seccomp=…`; the container stays non-root, unprivileged,
without added capabilities, ports on loopback. The pre-approved fallback (`--no-sandbox --test-type` in `vc-chrome`)
was not needed. Host requirement: unprivileged user namespaces allowed (Ubuntu: `kernel.apparmor_restrict_unprivileged_userns = 0`).
Evidence: `bin/vc-env check` → `chrome_sandbox_on` (no `--no-sandbox` on any Chrome process, every renderer in its own
user namespace); `chrome://sandbox` reports "Layer 1 Sandbox: Namespace, PID/Network namespaces: Yes, Seccomp-BPF: Yes,
You are adequately sandboxed".

Window (measured live, Chrome 154, no window manager): kiosk fullscreen comes up 1919×1079 and Chrome clamps any
window of the screen's size to that, while a normal window shows 87 px of tab strip and toolbar. `env/lib/window_fit.py`
therefore places a normal window with its bars above the screen (top −87, height 1167): the page area is exactly
1920×1080 at (0, 0) and no browser bar is on camera. `bin/vc-env check` verifies the page's top edge on screen
(`screenY + outerHeight − innerHeight == 0`).
