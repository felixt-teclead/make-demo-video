# The event stream of a take (one file, two readers)

Owner: integration. Schema and checker: `vc/events.py` (`SCHEMA`, `problems`, `check_stream`). Contract test:
`tests/integration/test_events_contract.py`.

**The file:** the recorder's `<run>/events.jsonl` (the same list is in `manifest.json` "events"). Nothing else is an
event file of a take. The cutter (`vc/cut/timeline.py`) reads it; the gate reads it directly in raw mode
(`vcgate.inputs.raw_events`) and, in cut mode, through the cut record's per-clip events (clip time), which the loop
turns into `clips/events.json` (`loop/vcloop/gatefeed.py`). `take.log.jsonl` (jev decisions) is a log, not an event
file; the cutter still accepts its `readiness_wait` records as a fallback and never counts a wait twice.

**Times:** epoch seconds on the one host clock. The recorder stamps `video_t` and `frame` from `t`, and
`video_end_t` from `end`.

| type | posted by | required | optional |
|---|---|---|---|
| `start` | recorder | `t` | `label`, `run_id` |
| `mark` | take runner (`POST /mark`) | `t`, `name`, `step` | `url` (the page URL: the clip's site for Q-72) |
| `hold` | take runner (`POST /hold`) | `t`, `kind`, `seconds`, `step` | |
| `click` | overlay (`OnCamera.after_input`) | `t` (press + ripple start = the logged click, Q-76), `glide_t` (glide start), `click_t` (real input), `x`, `y`, `step` | `label`, `kind`, `navigates` |
| `typing` | overlay (`type_text`) | `t` (first char), `end` (last char or Enter), `step` | `chars`, `submit`, `value_set_whole` |
| `paste` | overlay (`type_text`, Q-55) | `t`, `end`, `step` | `chars` |
| `readiness_wait` | take runner (from the jev wrapper's log) | `t` (start), `end`, `step` | `ok`, `jev_step` |
| `pan`, `reveal` | camera (`vc/overlay/camera.py`) | `t`, `end`, `step` | `distance`, `axis`, `frames`, `ms` |
| `orphan_ripple` | overlay (`input_aborted`) | `t`, `x`, `y`, `step` | `reason`, `label` |

`step` is the 1-based spec step; the take runner adds it to everything its hooks post (`StepEvents`). Order within a
click: `glide_t <= t <= click_t` (the real input follows the ring's first frame by 120 ms).
