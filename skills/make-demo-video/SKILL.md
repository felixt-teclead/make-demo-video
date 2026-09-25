---
name: make-demo-video
description: >-
  Makes a checked demo video of a web-app feature, end to end: align a use-case spec, then drive the deterministic
  filming loop to a delivered MP4 and QA report. Use when the user says "make/film/record a demo video of <feature>",
  "turn this merge request / diff into a demo video", "click around X and show Y on video", "run / resume / retake the
  demo spec <name>", or "run it unattended". Do NOT use for: requests where only the spec should change, with no
  filming ("write / adjust / approve / diff the spec": use write-demo-spec), claude.ai or other chat-app scenes,
  editing QA checks, thresholds or cutter rules, a single screenshot or image, a slideshow or animated image, general
  browser automation or scraping, or debugging the gate or cutter code itself.
---

# Make a demo video

Plugin root: `${CLAUDE_PLUGIN_ROOT}` (the folder that holds `bin/vc-loop`). Run commands from there. Every path below
is relative to it.

1. Read `docs/house-rules.md` once. It applies to everything below.
2. Pick the path and read only that step doc when you reach it:

| Request | Read |
|---|---|
| a feature text, diff or merge-request text, a description, or "click around X and show Y" | `docs/steps/align.md`, then `docs/steps/run.md` |
| an existing approved spec | `docs/steps/run.md` (back to `align.md` only if validation fails) |
| only write, change, approve or diff the spec (no filming) | not this skill: `write-demo-spec` (same `align.md`) |
| resume a stopped or handed-off job | `docs/steps/run.md`, section Resume |

3. Every run includes a viewer review after each gate PASS (the loop hands it off). While the loop runs, read on
   demand: `docs/steps/review.md` at a viewer-review handoff,
   `docs/steps/deliver.md` when the loop delivers or ends, `docs/steps/promotion.md` only if a fix looks worth keeping
   for every app. `docs/steps/on-fail.md` is for the fixer agent, only after a FAIL.

Filming, cutting, QA and joining are the loop's job (`bin/vc-loop`, described in `docs/loop.md`). Do not do them by
hand and do not re-read a take that passed.
