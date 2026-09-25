# make-demo-video

A Claude Code plugin that turns a feature of your web app into a short, checked, silent MP4 demo video.

## Install

Prerequisites:
- Linux x86_64 with Docker and Docker Compose ≥ 2.24, and about 5 GB free disk
- on Ubuntu ≥ 23.10: `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` (persist it in `/etc/sysctl.d/`)
- Claude Code, and your own OpenRouter API key

1. Add the plugin in Claude Code:
   ```
   /plugin marketplace add felixt-teclead/make-demo-video
   /plugin install make-demo-video@make-demo-video
   ```
   `/plugin` lists `make-demo-video` as installed and shows its folder. Run the commands below in that folder (or in
   a clone of this repo, started with `claude --plugin-dir .`).
2. Create the env file `~/.config/vc-v1/env` with mode 600:
   ```sh
   OPENROUTER_API_KEY=...
   APP_USER=...
   APP_PASS=...
   ```
   The user and password names are the ones your login profile lists (`profiles/example/login.json`).
3. `bin/vc-env init` creates `settings.env`; change ports or `VC_ENV_FILE` there if needed.
4. `bin/vc-env build`, then `bin/vc-env up` prints `healthy` and the remote-view address.
5. `bin/vc-env check` prints `"verdict": "PASS"`.
6. Log in to your app, scripted:
   `bin/vc-lock login bin/vc-env exec python3 /opt/vc/env/login/scripted_login.py --profile /opt/vc/profiles/<app>/login.json`
   prints `"status": "logged in"`. Or log in by hand in the remote view from `bin/vc-env url` (password in
   `state/vnc-password`).

## Use

In Claude Code: `/make-demo-video <what to show>`. The plugin agrees a spec with you, asks for approval, then films,
cuts and checks until a take passes. You get `runs/jobs/<job>/deliver/<spec>.mp4` and `runs/jobs/<job>/report.md`.

## More

- Add your app and the golden example cases: `docs/add-your-app.md`
- Variant fixes: `docs/variants.md`
- Unattended runs: `docs/unattended.md`
- Tests: `docs/testing.md`
- Changes: `CHANGELOG.md`

## License

MIT, see `LICENSE`.
