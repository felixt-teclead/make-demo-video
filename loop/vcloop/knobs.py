"""The ONE settings table (F-23). Every behaviour parameter of the loop and the step runner that has a default lives
here, and nowhere else. A knob can be set per run (`--knob name=value`) or in the spec (`[knobs]`); the run value wins.

QA thresholds are NOT knobs: they are the QA definition (F-19). Typing speed is not a knob (Q-79).

Decision on M2's open point (settle_s, ready_timeout_s, wait_poll_s): they ARE in this table, in the group "runner".
Reasons: (1) F-23's Verify line fails any behaviour parameter that has a default but is not listed; (2) they change
outcomes, not only speed: ready_timeout_s decides whether a slow page is a readiness wait (sped up at cut level, Q-56)
or a failed step, and settle_s bounds the independent check (C-02); (3) the fixer may change waits (F-15), so they
must be settable per spec without a code edit. M2's other internal values (check_poll_s, point_tolerance_px,
point_rechecks) are listed too for the same reason (1).
"""

from . import variants as VW

# name: (default, type, group, rule / source)
TABLE = {
    # loop (F-11, F-13, D-41, C-18)
    "max_takes": (4, int, "loop", "cap on filmed takes per spec per request (F-13, OD-14)"),
    "dry_runs": (1, int, "loop", "dry runs before the first take; another only after a fix or spec change, always full (F-11)"),
    "dry_run_cap": (6, int, "loop", "dry runs per spec; the next one is refused and the loop stops (D-41, F-22 #6)"),
    "lock_timeout_s": (600, int, "loop", "browser lock timeout, 10 min (C-18)"),
    "run_timeout_s": (900, int, "loop", "wall-clock cap on one dry run / take / cleanup; the runner then finalises the "
                      "recording and ends, and the loop stops (C-18: a hung run never holds the browser forever)"),
    "flake_retakes": (1, int, "loop", "unchanged retakes the fixer may ask for, per job (F-15)"),
    "max_variants": (3, int, "loop", "candidate fixes the fixer may return per fix when it sees real alternatives; "
                     "1 = one fix only (owner 2026-09-25)"),
    "variant_dry_runs": (1, int, "loop", "scoring dry runs per candidate; they count against dry_run_cap"),
    "variant_margin": (0.05, float, "loop", "every candidate whose jev score is within this of the top score is "
                       "filmed as its own take (within max_takes)"),
    "variant_score_weights": (VW.DEFAULT_WEIGHTS, str, "loop", "weights of the jev signals in a candidate's score "
                              "(loop/vcloop/variants.py)"),
    # jev runner (C-03 and M2 internal timings)
    "decisions_per_step": (6, int, "runner", "jev decisions per step before the step fails (C-03)"),
    "transient_retries": (2, int, "runner", "retries of stale-page / provider refusals (C-03)"),
    "settle_s": (3.0, float, "runner", "how long the step's independent check is polled after an input (C-02; M2)"),
    "ready_timeout_s": (10.0, float, "runner", "readiness wait for an absent target before the step fails (M2)"),
    "wait_poll_s": (0.4, float, "runner", "pause after a jev WAIT before observing again; each WAIT is a decision (M2)"),
    "check_poll_s": (0.05, float, "runner", "poll interval of the independent check (M2)"),
    "point_tolerance_px": (2.0, float, "runner", "click point re-read tolerance during the glide (C-09; M2)"),
    "point_rechecks": (3, int, "runner", "click point re-reads during the glide (C-09; M2)"),
    # holds (Q-62, canonical) and cut
    "hold_landing_s": (1.0, float, "holds", "landing view hold (Q-62)"),
    "hold_step_s": (1.0, float, "holds", "step end hold; never below 1 s mid-video (Q-62)"),
    "hold_dialog_s": (3.0, float, "holds", "dialog hold (Q-62)"),
    "hold_reading_min_s": (1.5, float, "holds", "reading hold floor (Q-60/Q-62)"),
    "hold_reading_max_s": (6.0, float, "holds", "reading hold cap (Q-60/Q-62)"),
    "reading_wps": (3.3, float, "holds", "reading speed, words per second (Q-60)"),
    "hold_final_s": (12.0, float, "holds", "final hold; another value needs a written reason in the spec (Q-62)"),
    "action_time_s": (1.0, float, "holds", "estimated on-screen time per action (glide+click+paint) for the length estimate (F-05)"),
    "speedup_factor": (4.0, float, "cut", "speed-up only where there is nothing to read (Q-56)"),
    "crossfade_s": (0.2, float, "cut", "cross-fade only at a site switch (Q-72)"),
    # scope, login, models, mode
    "writes_allowed": (False, bool, "scope", "off = read-only; on only for the listed, approved writes (C-34)"),
    "length_min_s": (30.0, float, "scope", "target length range, low end (Q-13)"),
    "length_max_s": (60.0, float, "scope", "target length range, high end (Q-13)"),
    "login_mode": ("auto", str, "login", "auto = scripted when the env file holds credentials, else human (F-26)"),
    "jev_model": ("models-config", str, "models", "jev version from the models config (C-04)"),
    "text_helper_model": ("models-config", str, "models", "text-helper version from the models config (C-04)"),
    "fixer_model": ("orchestrator", str, "models", "fixer model; a cheaper model is a setting (F-16, OD-14)"),
    "review_model": ("orchestrator", str, "models", "viewer-review model (owner 2026-09-25: the orchestrating frontier "
                     "model; another value = a subagent on that model, or `claude -p --model` headless)"),
    "review_privacy": (False, bool, "models", "viewer review also flags private data beyond the spec's [privacy] "
                       "shown list (off = OD-05: not judged)"),
    "human_reachable": (True, bool, "mode", "no = unattended (F-37): no questions, every stop ends the run with a report"),
    "app_profile": ("auto", str, "mode", "auto = chosen by the spec's site (F-36)"),
    "job_budget_usd": (1.0, float, "loop", "jev cost cap per job, all dry runs and takes (C-12)"),
}

RUNNER_KNOBS = ("decisions_per_step", "transient_retries", "settle_s", "ready_timeout_s", "wait_poll_s",
                "check_poll_s", "point_tolerance_px", "point_rechecks")


ALIASES = {"framecheck_model": "review_model"}   # old name, still accepted


class KnobError(ValueError):
    pass


def _coerce(name, value):
    typ = TABLE[name][1]
    if typ is bool:
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("1", "true", "yes", "on", "y"):
            return True
        if s in ("0", "false", "no", "off", "n"):
            return False
        raise KnobError(f"knob {name}: not a yes/no value: {value!r}")
    try:
        return typ(value)
    except (TypeError, ValueError):
        raise KnobError(f"knob {name}: expected {typ.__name__}, got {value!r}")


def defaults():
    return {k: v[0] for k, v in TABLE.items()}


def resolve(spec_knobs=None, run_knobs=None):
    """Defaults <- spec [knobs] <- run overrides. Returns (values, sources)."""
    vals, src = defaults(), {k: "default" for k in TABLE}
    for origin, over in (("spec", spec_knobs or {}), ("run", run_knobs or {})):
        for k, v in over.items():
            k = ALIASES.get(k, k)
            if k not in TABLE:
                raise KnobError(f"unknown knob {k!r} (the knob table is loop/vcloop/knobs.py)")
            vals[k] = _coerce(k, v)
            src[k] = origin
    if vals["length_min_s"] > vals["length_max_s"]:
        raise KnobError("length_min_s is above length_max_s")
    for k in ("max_takes", "dry_run_cap", "decisions_per_step", "max_variants", "variant_dry_runs"):
        if vals[k] < 1:
            raise KnobError(f"knob {k} must be >= 1")
    try:
        VW.parse_weights(vals["variant_score_weights"])
    except ValueError as e:
        raise KnobError(str(e))
    return vals, src


def parse_overrides(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise KnobError(f"--knob expects name=value, got {p!r}")
        k, v = p.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def table_lines(vals, src):
    lines = []
    for k, (dflt, _t, group, rule) in TABLE.items():
        mark = "" if src.get(k) == "default" else f"   <- {src.get(k)} (default {dflt})"
        lines.append(f"  {group:7} {k:20} = {vals[k]!s:14}{mark}")
    return lines
