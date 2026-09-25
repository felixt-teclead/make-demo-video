# Variant fixes

When a take fails and the fixer sees more than one plausible fix, it returns 2-3 candidate copies of the spec instead
of betting on one. The loop:

1. drops candidates that change what the owner approved (the contract or the approval block), or that exceed
   `max_variants`;
2. dry-runs each remaining candidate (`variant_dry_runs` times, no recording) and scores it only from what jev did:
   verified steps, steps hit on the first decision, extra decisions, retries, timeouts and jev's decision confidence
   (weights in `variant_score_weights`);
3. films the best candidate plus every candidate within `variant_margin` of it, one take each, within the take cap;
4. keeps the best filmed take: a hit first, then fewest QA violations, warnings and review findings, then the smallest
   change to the approved spec.

With a human reachable, the loop stops after step 2, shows the ranking and waits for "film" or a chosen candidate.
The ranking lines appear in the take log and the run report.

Knobs (defaults): `max_variants` 3, `variant_dry_runs` 1, `variant_margin` 0.05,
`variant_score_weights` `verified=0.5,first_hit=0.2,confidence=0.3,probability=0,redecision=0.05,transient=0.05,timeout=0.1`.
