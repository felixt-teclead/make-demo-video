# Add your app

Everything app-specific lives in an app profile and its quirk record; the plugin core knows no app.
`profiles/example/` is a template for a web app at `https://app.example.com`.

1. Copy `profiles/example/` to `profiles/<your-app>/` and edit `profile.json`:
   - `name` and `hosts`: your app's host names (`"app.your-company.com"`, `"*.your-company.com"`); a spec's start URL
     picks the profile by its host;
   - `deny_extra`: labels of controls that write, share, delete or show other people's data; jev is never offered them;
   - `login.url` and `login.check`: the app's start page and the login profile file;
   - `quirk_record`: `specs/quirks/<your-host>.md`;
   - `view_start` (optional): start-position rules for views that open at an awkward scroll position, each citing its
     quirk record entry; delete the example rule if your app has no such view.
2. Edit `login.json` for the optional scripted login: `start_url` (a page that redirects to the login form when logged
   out), the CSS selectors `form.user`, `form.password`, `form.submit` (`two_step: true` when the password field
   appears only after the user name), `logged_in.url_prefix`, and the env file variable names in `credentials`.
   Without it, log in once by hand in the remote view.
3. Copy `specs/quirks/app.example.com.md` to `specs/quirks/<your-host>.md` and record your app's quirks there (id,
   views, symptom, detection, mitigation), only from measurements.
4. Check: `bin/vc-spec validate <spec>` prints `<name>: valid (0 errors, …)`.

## Golden cases

`specs/golden/*.toml` are three example cases written against the example app (a process-modelling app with a German
UI). They use placeholders: `https://app.example.com/...` and `<process-id>` in `start.warm`. To film them, point them
at your app, replace `<process-id>` with your own record ids, explore them with jev and approve them again. Their
exploration records under `specs/golden/explore/` are synthetic; replace them with real ones before filming (the
validator warns until you do). `specs/examples/` holds specs for the local fixture page, which needs no account.

`bin/vc-golive --approve-as <you>` builds, starts and checks the recorder, logs in, films golden case 1 and prints the
QA report.
