# jev wrapper (M2): interface

Owner: milestone M2. Code: `jev/vcjev/` (Python package `vcjev`), config: `config/models.json`, tests:
`jev/tests/`. Requirement IDs refer to `vc-v1-spec`.

## 1. Upstream and what is patched

- Upstream: `github.com/browser-use/jev-ultrafast`, pinned commit `1231850a0bf1a0c0341fe408ef1668dbbfdfac46` (MIT).
- Upstream hook check (2026-09-24): upstream has **no** hook for an offered-set filter, a provider/endpoint/model
  setting (URL `api.typesafe.ai` and `TYPESAFE_API_KEY` are hard-coded), a click-point callback, or attaching to an
  existing tab (it creates a background target and forces a 1120x780 viewport). So the wrapper uses the allowed
  thin patch/subclass. jev's decision logic (`choose`, `validate_choice`, `action_space`, the question texts) is
  used unchanged.
- Patches (all in `vcjev/upstream.py`, `vcjev/browser.py`):
  1. `browser_harness` (upstream's transport dependency: a daemon with cloud and telemetry features) is **not
     installed**; a module shim routes upstream's `cdp()` to `vcjev.cdp` (one websocket, flat sessions).
  2. `jev_ultrafast.model.post_json` is wrapped: jev and text-helper requests go to the configured provider, with
     the configured model and the key from the env file (C-04).
  3. `FilmedTab(Browser)` attaches to the one existing page tab, clears any device-metrics override, never closes the
     tab (C-07).
- Filtering (C-01/C-08) happens before `choose()`: the runner passes jev a page whose `actions` are exactly the one
  intended control plus `wait`; upstream adds DONE/BLOCKED itself.

### Install in the environment image

Done by `env/Dockerfile` from the one requirements file `env/requirements.txt` (the jev-ultrafast line is installed
with `--no-deps`, and upstream's demo app is removed, C-29). The image sets `PYTHONPATH=/opt/vc:/opt/vc/jev:/opt/vc/loop:/opt/vc/gate`
(the repo is mounted at `/opt/vc`). `jev/tests/env/Dockerfile` is a throwaway test rig, not the filming image.

## 2. Python API

```python
from vcjev.session import open_session
from vcjev.runner import Hooks, Settings, StepFailed, LoginRequired

runner = open_session(
    cdp_url=os.environ["VC_CDP_URL"], env_file=os.environ["VC_SECRETS"],   # env file by path only
    log_path=f"/runs/{run_id}/take.log.jsonl", job=job_id, run_id=run_id, phase="take", take=k,
    expect_viewport=(1920, 1080),        # C-07: raises TabError if not exactly one page tab of this size
    deny_extra=profile_deny + spec_deny,  # C-08 additions (never removals)
    settings=Settings(decisions_per_step=6, transient_retries=2),   # F-23 knobs
    hooks=Hooks(before_input=glide, before_click=press, after_input=clicked, input_aborted=orphan,
                type_text=typist, on_decision=cb),
    budget_usd=1.0)                       # C-12 job budget, checked before every decision
runner.tab.load_start_url(url)            # off camera, at a step boundary only (C-14 exception)
result = runner.run_step(step)            # StepResult; raises StepFailed / LoginRequired
```

### Step (what the runner needs from the spec)

```json
{"name": "open-details", "op": "click|type|select",
 "target": {"label": "Details anzeigen", "role": "button", "within": "dialog"},
 "text": "exact text (type only)", "submit": false,
 "check": [{"type": "dialog_open", "value": "Schritt 1"}],
 "approved_write_labels": []}
```

- `target`: `label` (exact own label, case/whitespace-insensitive) or `label_contains` or `label_regex`; optional
  `role` (upstream role names: button, link, tab, textbox, searchbox, combobox, …); optional `within` (substring of
  an ancestor's role + accessible name, e.g. `dialog`, `navigation Hauptmenü`, `row`); `option` for select.
- The **own label** is the control's accessible name without the text of nested controls (OD-17): a row link that
  contains a "Löschen" button has own label "Erster Eintrag"; the button's own label is "Löschen".
- `check` (C-02, one object or a list, all must hold): `url_contains`, `url_regex`, `text_visible`, `text_absent`,
  `dialog_open` (optional `value` = text inside), `dialog_closed`, `element_visible` (`selector`/`role`/`label`,
  optional `count`), `count` (`selector`, `value`), `title_contains`.
- `approved_write_labels`: exact own labels the owner approved for this step (C-34 writes and cleanup); they bypass
  the never-offer list for this step only.

### Contract (what run_step guarantees)

1. Before every decision: login form visible → `LoginRequired`, no decision (C-31).
2. The target must resolve to exactly one allowed control. 2+ matches or a denied match → `StepFailed` before any
   decision ("before acting: …", names the step). 0 matches → readiness wait (re-observe every `wait_poll_s` for up
   to `ready_timeout_s`, logged as a `readiness_wait` record with start/end for the speed-up), then `StepFailed`.
3. jev is offered exactly that control + WAIT (+ DONE/BLOCKED). Every decision is logged with its offered set.
4. CLICK/SELECT/TYPE: the click point (element centre, hit-tested, same formula as upstream's executor) is computed,
   `before_input(action, point)` is called (glide), the point is re-read and `before_input` is called again if it
   moved more than 2 px (C-09). At the stable point the runner sends one hover `mouseMoved` (`FilmedTab.hover`),
   then calls `before_click(action, point)` right before the real input (the overlay's press + ripple; it may raise
   `StalePage`, then nothing is sent). Then upstream's executor clicks (or `type_text` types).
   `after_input(action, point, info)` follows; if the input fails after `before_click`,
   `input_aborted(action, point, reason)` is called instead and the attempt counts as a transient retry.
5. TYPE_TEXT: the library's text helper is **not** used. `type_text(tab, action, point, text, submit)` is called
   with the spec's exact text (default: `vcjev.typing_hook.default_type_text`, Q-79 plan; M3 replaces it).
6. The step passes only when an input was executed in this step and the independent check holds (polled for
   `settle_s` after the input). DONE with a false check → `StepFailed`; DONE before any input → `StepFailed`;
   BLOCKED → `StepFailed`.
7. `decisions_per_step` cap (default 6) → `StepFailed` naming the step. Stale page / provider errors are retried at
   most `transient_retries` (default 2) times. Three inputs in a row with no page change → `StepFailed`.

## 3. Logs (F-28)

`log_path` is append-only JSONL. Every line has `ts, job, run_id, phase, take, type`. Types:

- `decision`: `step, n, offered[{id,kind,role,own_label,node}], choice, operation, target, probability,
  confidence, model, response_model, latency_ms, tokens_in, tokens_out, cost_usd, cost_source ("provider" |
  "computed"), usage, t_start, t_end, url, executed, click_point{x,y}, t_input, label`, `http{attempts[{status,
  ms, http}], backoff_ms, provider}` (every HTTP attempt of the decision: a slow decision shows whether it was
  provider time or 429/503 retries)
- `text_call` (only if the text helper is ever called): `latency_ms, tokens, cost_usd, cost_source`
- `step_start`, `step_end` (`ok, reason, decisions, check`), `readiness_wait` (`start, end, ok`), `transient`,
  `resolve_failed`, `login_required`, `models` (resolved models config, no key), `provider_warm` (`ms` spent
  opening the provider connection at session start).

`vcjev.accounting.summarize(records, group_by="phase"|"take"|"run_id")` gives decisions, jev seconds, mean ms per
decision, tokens and $; `phase_record(...)` gives the jev columns of one F-28 phase record. CLI:
`python -m vcjev summary LOG --by phase`.

The loop posts clicks to the recorder (`POST /event`, OD-19) from `on_decision`/`after_input` using `t_input` and
`click_point`.

## 4. Models config (C-04)

`config/models.json` (path override: `VC_MODELS_CONFIG`): `provider` + `providers{name: jev_url, jev_format,
text_base_url, text_format, key_var}`; per role (`jev`, `text_helper`): `default`, `candidate`, `versions{id:
verified, price_per_mtok{input, output}}`, `history`. CLI:

```sh
python -m vcjev models show
python -m vcjev models candidate --role jev --version typesafe/jev-1.14 --price-in 0.042 --price-out 0
python -m vcjev models promote --role jev --acceptance acceptance.json   # {"version","passed":true,"job_cost_usd"<=1}
python -m vcjev models rollback --role jev
```

A run can pin a version (`jev_version=`) or use the candidate (`use_candidate=True`) without touching the default.
Switching `provider` to `stub` (or any listed provider) redirects all jev and text-helper calls.

## 5. Measured (live, 2026-09-24, OpenRouter, typesafe/jev-1.13)

5 decisions on the local test page: latency 2177–3255 ms per decision (mean ≈ 2.8 s), 1186–1217 input and 69–72
output tokens, provider-reported cost ≈ $0.00005 per decision (exactly 4.2e-8 $ per input token, output free; this is
the configured fallback price). One decision per step on the click, type and select steps.

Re-measured 2026-09-25 (`jev/tests/live/latency-2026-09-25.json`), without a browser, from the recorder image:
median 265–285 ms, p90 288–356 ms per decision (n=15–20 per variant), for warm, 8 s idle gaps, a new client per
call, HTTP/1.1, the full element table and 6000 chars of page text alike. The committed code before
`cp-B-jev-latency` measured the same in the same time window, so the 2.2–3.3 s above was transient provider-side
latency (or hidden 429/503 retries, which upstream's `post_json` sleeps 0.5 s + 1 s for and did not record), not
the wrapper's endpoint, format, payload or client. Since `cp-B-jev-latency` the transport (`vcjev.upstream._send`)
records every attempt (`http` in the decision record), honours Retry-After (capped 2 s), retries a dropped pooled
connection once instead of failing the decision (seen once after a 30 s idle gap), keeps the HTTP/2 connection 120 s
(httpx default 5 s) and opens it at session start (`warm`). Re-measure:

```sh
python jev/tests/bench_latency.py --env-file "$VC_SECRETS" --n 20 --variants warm,gap --out /tmp/latency.json
```

Reproduce (inside a container with Chrome on 9222 and the test page served on 8000):

```sh
python -m vcjev step --cdp http://127.0.0.1:9222 --env-file "$VC_SECRETS" --log /tmp/live.jsonl \
  --step jev/tests/live/one-step.json --start-url http://127.0.0.1:8000/index.html --job live-roundtrip
```

## 6. Tests

- Unit (host, stdlib only): `cd jev && PYTHONPATH=.:tests python3 -m unittest discover -s tests` — filter (0/2+
  matches, nested "Löschen", deny list, profile additions, approved writes), step cap, DONE vs. independent check,
  transient retries, login stop, click point before click, typing hook, accounting, models upgrade/rollback.
  `test_transport` (HTTP retries, attempt record, keep-alive, warm-up; httpx mock, no network) needs the recorder
  image's deps and is skipped on a bare host.
- Integration (real Chrome + local test page `jev/tests/page/`, stub provider, no paid calls): same command inside
  the test container with `VCJEV_CDP=http://127.0.0.1:9222` (and `VCJEV_VIEWPORT=WxH` to assert the viewport).

## 7. Open points

- `settle_s` (3 s), `ready_timeout_s` (10 s), `wait_poll_s` (0.4 s) are internal timing values in `Settings`, not
  F-23 knobs; the loop owner decides whether they belong in the knob table.
- The OpenRouter jev price is derived from observed `usage.cost`; it is used only when a response has no cost.
- Not yet run against a live app or in the M1 container (needs the logged-in environment and the deps above installed).
