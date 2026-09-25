# Changelog

## v1.1.1 (2026-09-25)

- Long loading after a click (`docs/cut-interfaces.md`): when the view settles more than 2.0 s after the first
  skeleton frame, the cutter holds the pre-click frame (with the drawn ripple) for 2.0 s, cuts the rest of the wait
  out and cross-fades into the settled view in the site-switch fade's shape (crossfade_s frames, linear). A load
  still running when the 2.4 s window ends is followed to the next action. The cut record logs `status "bridged"`
  with a summary such as "loading 3.4 s: cut to 2.0 s + fade"; the QA report prints it. Logged holds are never
  trimmed. Still defects: a black or blank with no click before it, and a view that never settles. The gate is
  unchanged. Tests: `tests/cut/test_bridge.py`.
- Test fix: the loop end-to-end test plants its synthetic black away from a click, where the cutter may not cover
  it; the full `bin/vc-selftest` passes again.

## v1.1 (2026-09-25)

- Shorter README: install first; longer topics moved to `docs/`.
- Installable as a Claude Code plugin: `.claude-plugin/marketplace.json`; the plugin is now named `make-demo-video`
  (was `vc-v1`), so its agents and skills are `make-demo-video:*`. The guard accepts both names.
- Variant fixes (`docs/variants.md`): when the fixer sees real alternatives it returns 2-3 candidate spec copies (fix kind
  `variants`). The loop dry-runs each one and ranks it only from the jev log (verified steps, first-decision hits,
  re-decisions, retries, timeouts, jev decision confidence). It films the top candidate plus every candidate within
  `variant_margin`, then keeps the best by gate, frame check, viewer review and closeness to the approved spec.
  With a human reachable, the loop shows the ranking and waits before filming. New knobs: `max_variants` (3), `variant_dry_runs` (1),
  `variant_margin` (0.05), `variant_score_weights`. Tests: `tests/loop/test_variants.py`.

## v1.0 (2026-09-25)

First release.
