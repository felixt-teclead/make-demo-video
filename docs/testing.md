# Tests

`bin/vc-selftest --quick` runs every suite that needs neither Chrome nor media files (jev wrapper, overlay,
environment, loop, review, integration, fixture page, plugin hooks and layout, `claude plugin validate`). It creates
`.venv` with numpy on first use and ends with `9 of 9 suites passed`. Without `--quick` it also runs the media
suites: gate unit tests, the video-only gate checks, the gate fixture clips, the cutter's verify suite and the loop's
end-to-end scenarios on a synthetic video. `bin/vc-selftest --list` names the suites; `--only NAME` runs one.

## Private test data

Some regression tests use screen recordings of a private app that are not in this repo: the gate fixture clips
(`VC_FIXTURES`), the cutter's acceptance data (`VC_ACCEPTANCE`: `visual-baselines/`, `fixtures/`), and the recorded
clips and frames under `gate/tests/data/` and `tests/cut/data/`. Without them those tests and `bin/vc-gate-fixtures`
skip with a message; everything else runs.
