# Build notes: the recorder image (C-22, C-29)

Base `python:3.12-slim-bookworm` (public; jev-ultrafast needs Python >= 3.12, bookworm's python3 is 3.11).
Measured 2026-09-24: 2.14 GB (limit 5 GB), build about 2 min with network.

| Package / file | Run phase that uses it |
|---|---|
| google-chrome-stable | every browser phase (explore, login, dry run, take, cleanup) |
| xvfb | the virtual screen for Chrome and the recording |
| ffmpeg | recording (x11grab), cut and join, QA decoding |
| x11vnc, novnc, python3-websockify | remote view for the human login (F-26) |
| fonts-noto-core, fonts-noto-mono, fonts-noto-color-emoji, fontconfig | page rendering (C-15 font check; fonts-noto-mono holds Noto Sans Mono for `monospace`) |
| locales, tzdata | de-DE locale and Europe/Berlin (C-15) |
| ca-certificates, curl | HTTPS for Chrome and pip; the health check |
| util-linux, procps | `flock` for the browser lock; process control in the supervisor |
| venv: httpx, websocket-client, jev-ultrafast (--no-deps, demo app removed) | the jev wrapper in dry runs, takes, cleanup |
| venv: numpy | the QA gate |

The one requirements file is `env/requirements.txt`. Integration finding: the M1 base (debian:bookworm-slim) could
not install jev-ultrafast (Python 3.11); the base moved to the official Python 3.12 image on the same Debian release.
