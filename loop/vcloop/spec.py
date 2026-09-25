"""The use-case spec (F-07, F-08): a TOML file people can diff and a program can check.

This module loads, resolves (parameters), validates (F-10), fingerprints (F-06), renders the approval view (F-05),
records approval (F-06, C-34, F-37) and diffs a spec against what was approved. The format is documented in
docs/spec-format.md; the example specs are in specs/.
"""
import copy
import datetime
import difflib
import glob
import hashlib
import json
import os
import re
import shutil
import tomllib

from . import knobs as K
from . import match
from . import profiles as P

FORMAT = "vc-spec/1"
JEV_OPS = ("click", "type", "select")
CAMERA_OPS = ("pan", "reveal", "hold", "wait")
CHECK_TYPES = ("url_contains", "url_regex", "text_visible", "text_absent", "dialog_open", "dialog_closed",
               "element_visible", "count", "title_contains")
HOLD_KINDS = ("step", "dialog", "reading", "final")
RUNTIME_PLACEHOLDERS = ("unique", "item")  # filled by the loop: per-take write name, cleanup item (C-34)
PH = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
TYPE_MS_PER_CHAR = 0.037                    # Q-79 (not a knob)
PAN_DEFAULT_S = 2.5
REQUIRED = ("format", "name", "title", "purpose", "context", "length", "params", "preconditions", "start", "steps",
            "writes", "deny", "privacy", "site_switches", "approval")


class SpecError(ValueError):
    pass


class Problem:
    def __init__(self, rule, message, step=None, severity="error"):
        self.rule, self.message, self.step, self.severity = rule, message, step, severity

    def __str__(self):
        where = f"step {self.step!r}: " if self.step else ""
        return f"{self.severity.upper()} [{self.rule}] {where}{self.message}"

    def as_dict(self):
        return {"rule": self.rule, "step": self.step, "severity": self.severity, "message": self.message}


# ---------------------------------------------------------------------------------------------------------- load

def load(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        spec = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise SpecError(f"{path}: not valid TOML: {e}")
    spec["_path"] = os.path.abspath(path)
    return spec


def public(spec):
    return {k: v for k, v in spec.items() if not k.startswith("_")}


def _walk_strings(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk_strings(x, fn) for x in obj]
    if isinstance(obj, dict):
        return {k: (_walk_strings(v, fn) if k not in ("params", "approval") else v) for k, v in obj.items()}
    return obj


def resolve(spec, runtime=None):
    """Fill {param} placeholders from [params] and the runtime values (e.g. unique). Returns (resolved, missing)."""
    params = dict(spec.get("params") or {})
    rt = dict(runtime or {})
    missing = []

    def fill(s):
        def rep(m):
            k = m.group(1)
            if k in rt:
                return str(rt[k])
            if k in params and params[k] not in ("", None):
                return str(params[k])
            if k in RUNTIME_PLACEHOLDERS and runtime is None:
                return m.group(0)       # kept for the loop to fill per take
            missing.append(k)
            return m.group(0)
        return PH.sub(rep, s)

    out = _walk_strings(copy.deepcopy(public(spec)), fill)
    out["_path"] = spec.get("_path")
    return out, sorted(set(missing))


# ------------------------------------------------------------------------------------------------ contract (F-06)

def _typed(step):
    return [a.get("text") for a in step.get("actions", []) if a.get("op") == "type"]


def contract(spec):
    """The locked "what": steps and their intent, expected states, writes, final hold, length range (F-06)."""
    r, _ = resolve(spec)
    steps = r.get("steps", [])
    out = {
        "steps": [{"name": s.get("name"), "does": s.get("does"), "expect": s.get("expect", []),
                   "write": s.get("write") or None, "typed": _typed(s)} for s in steps],
        "writes": [{"id": w.get("id"), "changes": w.get("changes"), "step": w.get("step")}
                   for w in r.get("writes", [])],
        "final_hold": steps[-1].get("hold") if steps else None,
        "length": list(r.get("length") or []),
    }
    for su in setup_steps(r):          # only when present: specs without a setup keep their fingerprint
        out["setup"] = {"name": su.get("name"), "does": su.get("does"), "expect": su.get("expect", []),
                        "write": su.get("write") or None}
    return out


def setup_steps(spec):
    """The off-camera setup (`[start.setup]`, docs/spec-format.md) as a one-element step list, else []."""
    su = (spec.get("start") or {}).get("setup")
    return [su] if isinstance(su, dict) else []


def _validate_setup(spec, steps, write_ids, out):
    """start.setup: one step-shaped table (name, does, expect, actions, optional write) run after the start view is
    ready and before the recording starts. Clicks and selects only: nothing typed, nothing created off camera."""
    for su in setup_steps(spec):
        n = su.get("name")
        if not n or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", str(n)) or n in [s.get("name") for s in steps]:
            out.append(Problem("F-08.name", "start.setup needs a short slug name that no step uses", n))
        if not str(su.get("does", "")).strip():
            out.append(Problem("F-05.does", "start.setup has no human-readable `does`", n))
        _checks_ok(su.get("expect"), n, "F-08.expect", out, "start.setup expect")
        if su.get("write") and su["write"] not in write_ids:
            out.append(Problem("C-34", f"start.setup is flagged as write {su['write']!r}, which is not in [[writes]]", n))
        acts = su.get("actions") or []
        if not acts:
            out.append(Problem("F-08.actor", "start.setup has no actions", n))
        for i, a in enumerate(acts):
            if a.get("op") not in ("click", "select"):
                out.append(Problem("F-08.actor", f"setup action {i + 1}: only click/select run off camera", n))
                continue
            c = a.get("control") or {}
            if not any(k in c for k in ("label", "label_contains", "label_regex")) or not str(a.get("where", "")).strip():
                out.append(Problem("F-08.control", f"setup action {i + 1}: control needs a label and `where`", n))


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(spec):
    return "sha256:" + hashlib.sha256(_canon(contract(spec)).encode("utf-8")).hexdigest()[:16]


def text_fingerprint(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------------------------------- length (F-05)

def estimate_length(spec, knobs=None):
    kv = knobs or K.defaults()
    r, _ = resolve(spec)
    total = float(r.get("start", {}).get("hold", kv["hold_landing_s"]))
    parts = {"holds": total, "actions": 0.0, "typing": 0.0, "camera": 0.0}
    for s in r.get("steps", []):
        parts["holds"] += float(s.get("hold", kv["hold_step_s"]))
        for a in s.get("actions", []):
            op = a.get("op")
            if a.get("hold") is not None:
                parts["holds"] += float(a["hold"])
            if op in JEV_OPS:
                parts["actions"] += kv["action_time_s"]
            if op == "type":
                parts["typing"] += len(a.get("text", "")) * TYPE_MS_PER_CHAR
            if op in ("pan", "reveal"):
                parts["camera"] += float(a.get("seconds", PAN_DEFAULT_S))
            if op == "hold":
                parts["holds"] += float(a.get("seconds", 0))
    est = sum(parts.values())
    return round(est, 1), {k: round(v, 2) for k, v in parts.items()}


# -------------------------------------------------------------------------------------------- exploration (D-10)

def explore_dir_for(spec, explore_dir=None):
    if explore_dir:
        return explore_dir
    base = os.path.dirname(spec.get("_path") or ".")
    return os.path.join(base, "explore", spec.get("name", ""))


def latest_exploration(spec, explore_dir=None):
    d = explore_dir_for(spec, explore_dir)
    files = sorted(glob.glob(os.path.join(d, "*.json")))
    if not files:
        return None, None
    with open(files[-1]) as f:
        return json.load(f), files[-1]


# ------------------------------------------------------------------------------------------ validation (F-10)

def _checks_ok(checks, step, rule, out, what):
    if not isinstance(checks, list) or not checks:
        out.append(Problem(rule, f"{what} is empty", step))
        return
    for c in checks:
        if not isinstance(c, dict) or c.get("type") not in CHECK_TYPES:
            out.append(Problem(rule, f"{what}: unknown check {c!r} (allowed: {', '.join(CHECK_TYPES)})", step))


def validate(spec, *, explore_dir=None, pdir=None, require_explore=True, knobs=None):
    """Return a list of Problems (errors block approval and filming; warnings do not)."""
    out = []
    for g in REQUIRED:
        if g not in spec:
            out.append(Problem("F-07.groups", f"required group {g!r} is missing"))
    if any(p.rule == "F-07.groups" for p in out):
        return out
    if spec["format"] != FORMAT:
        out.append(Problem("F-07.format", f"format must be {FORMAT!r}"))
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,31}", str(spec["name"])):
        out.append(Problem("F-07.identity", "name must be a short slug (a-z, 0-9, -; used as run-id prefix)"))
    for k in ("title", "purpose", "context"):
        if not str(spec.get(k, "")).strip():
            out.append(Problem("F-07.identity", f"{k} is empty"))
    try:
        kv, _ = K.resolve(spec.get("knobs"), None) if knobs is None else (knobs, None)
    except K.KnobError as e:
        out.append(Problem("F-23.knobs", str(e)))
        kv = K.defaults()
    ln = spec["length"]
    if not (isinstance(ln, list) and len(ln) == 2 and all(isinstance(x, (int, float)) for x in ln) and ln[0] < ln[1]):
        out.append(Problem("F-07.length", "length must be [min, max] seconds"))
        ln = [kv["length_min_s"], kv["length_max_s"]]
    elif ln[1] > 60 and not str(spec.get("length_reason", "")).strip():
        out.append(Problem("Q-13", "a length range above 60 s needs a written length_reason"))

    resolved, missing = resolve(spec)
    if missing:
        out.append(Problem("F-10.params", f"unfilled parameters: {', '.join(missing)}"))
    # preconditions
    if not isinstance(spec["preconditions"], list):
        out.append(Problem("F-07.preconditions", "preconditions must be a list (may be empty)"))
    else:
        for i, p in enumerate(spec["preconditions"]):
            if not str(p.get("what", "")).strip() or p.get("by") not in ("setup", "human"):
                out.append(Problem("F-07.preconditions", f"precondition {i + 1} needs `what` and `by` = setup|human"))
            if "check" in p:
                _checks_ok(p["check"], None, "F-07.preconditions", out, f"precondition {i + 1} check")
    # start
    st = spec["start"]
    if not st.get("url"):
        out.append(Problem("F-07.start", "start.url is missing"))
    _checks_ok(st.get("ready"), None, "F-07.start", out, "start.ready")
    if float(st.get("hold", 0)) < 1.0:
        out.append(Problem("Q-62", "start.hold (landing hold) is below 1 s"))
    # privacy (C-35)
    pv = spec["privacy"]
    for k in ("account", "shown", "why_ok"):
        if not str(pv.get(k, "")).strip():
            out.append(Problem("C-35", f"privacy.{k} is empty"))
    # profiles (F-36)
    try:
        profs = P.for_spec(resolved, pdir, kv.get("app_profile"))
    except P.ProfileError as e:
        out.append(Problem("F-36", str(e)))
        profs = []
    deny_extra = P.deny_extra(profs) + list(spec.get("deny") or [])

    # writes (C-34)
    steps = spec["steps"]
    names = [s.get("name") for s in steps] + [s.get("name") for s in setup_steps(spec)]
    approvals = {w.get("id"): w for w in (spec.get("approval") or {}).get("writes", [])}
    write_ids = []
    for w in spec["writes"]:
        wid = w.get("id")
        write_ids.append(wid)
        if not wid or not str(w.get("changes", "")).strip() or w.get("step") not in names:
            out.append(Problem("C-34", f"write {wid!r} needs id, changes and an existing step"))
        if wid not in approvals:
            out.append(Problem("C-34", f"write {wid!r} has no recorded owner approval (who and when); "
                                       "it is refused before any browser work", w.get("step")))
    _validate_setup(spec, steps if isinstance(steps, list) else [], write_ids, out)
    # steps (F-08)
    if not isinstance(steps, list) or not steps:
        out.append(Problem("F-07.steps", "no steps"))
        return out
    if not 3 <= len(steps) <= 7:
        out.append(Problem("F-04.steps", f"{len(steps)} steps; the default is 3 to 7 per video", severity="warning"))
    seen = set()
    for s in steps:
        n = s.get("name")
        if not n or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", str(n)):
            out.append(Problem("F-08.name", "step name must be a short slug", n))
        if n in seen:
            out.append(Problem("F-08.name", "duplicate step name", n))
        seen.add(n)
        if not str(s.get("does", "")).strip():
            out.append(Problem("F-05.does", "step has no human-readable `does`", n))
        if not s.get("expect"):
            out.append(Problem("F-08.expect", "step has no expected state", n))
        else:
            _checks_ok(s["expect"], n, "F-08.expect", out, "expect")
        if s.get("write") and s["write"] not in write_ids:
            out.append(Problem("C-34", f"step is flagged as write {s['write']!r}, which is not in [[writes]]", n))
        if s.get("hold_kind", "step") not in HOLD_KINDS:
            out.append(Problem("Q-62", f"hold_kind must be one of {HOLD_KINDS}", n))
        acts = s.get("actions") or []
        if not acts:
            out.append(Problem("F-08.actor", "step has no actions", n))
        for i, a in enumerate(acts):
            op = a.get("op")
            if op not in JEV_OPS + CAMERA_OPS:
                out.append(Problem("F-08.actor", f"action {i + 1}: unknown op {op!r}", n))
                continue
            if op in JEV_OPS:
                c = a.get("control") or {}
                if not any(k in c for k in ("label", "label_contains", "label_regex")):
                    out.append(Problem("F-08.control", f"action {i + 1}: control needs label/label_contains/label_regex", n))
                if not str(a.get("where", "")).strip():
                    out.append(Problem("F-08.control", f"action {i + 1}: `where` (where the control sits, in human "
                                                       "terms) is missing", n))
                if a.get("done"):
                    _checks_ok(a["done"], n, "F-08.done", out, f"action {i + 1} done")
            if op == "type":
                if not isinstance(a.get("text"), str) or not a["text"]:
                    out.append(Problem("F-08.text", f"action {i + 1}: typing needs the exact text", n))
                elif "{unique}" in a["text"] and not s.get("write"):
                    out.append(Problem("C-34", f"action {i + 1}: {{unique}} names are only for approved writes", n))
            if op in ("pan", "reveal") and not a.get("to"):
                out.append(Problem("F-08.camera", f"action {i + 1}: {op} needs `to` (what must come into view)", n))
            if a.get("hold") is not None and float(a["hold"]) < 1.0:
                out.append(Problem("Q-62", f"action {i + 1}: hold below 1 s mid-video", n))
        if s is not steps[-1] and float(s.get("hold", kv["hold_step_s"])) < 1.0:
            out.append(Problem("Q-62", "hold below 1 s mid-video", n))
    # unique names for approved writes that create things (C-34)
    for w in spec["writes"]:
        st_ = next((s for s in steps if s.get("name") == w.get("step")), None)
        if st_ and w.get("creates", True) and not any("{unique}" in (t or "") for t in _typed(st_)):
            out.append(Problem("C-34", f"write {w.get('id')!r} creates an item but its typed name has no "
                                       "{unique} placeholder (each take needs a new, unique name)", st_.get("name")))
    # final hold (Q-62)
    last = steps[-1]
    fh = float(last.get("hold", 0))
    if last.get("hold_kind") != "final":
        out.append(Problem("Q-62", "the last step must have hold_kind = \"final\"", last.get("name")))
    if abs(fh - kv["hold_final_s"]) > 1e-6 and not str(spec.get("final_hold_reason", "")).strip():
        out.append(Problem("F-08.final", f"final hold is {fh} s, not {kv['hold_final_s']} s, without a written "
                                         "final_hold_reason", last.get("name")))
    # site switches (F-07)
    for sw in spec["site_switches"]:
        if sw.get("step") not in names or not sw.get("site"):
            out.append(Problem("F-07.sites", f"site switch {sw!r} needs an existing step and a site"))
    # write-looking controls / typed text, runtime never-offer collisions (F-10, C-08, C-34)
    approved_steps = {w.get("step"): w for w in spec["writes"] if w.get("id") in approvals}
    rsteps = setup_steps(resolved) + resolved.get("steps", [])     # the off-camera setup is checked like a step
    for s in rsteps:
        n = s.get("name")
        is_write = bool(s.get("write")) and n in approved_steps
        allow = list(approved_steps[n].get("labels", [])) if is_write else []
        deny = match.DenyList(extra=deny_extra, allow=allow)
        for i, a in enumerate(s.get("actions") or []):
            if a.get("op") not in JEV_OPS:
                continue
            c = a.get("control") or {}
            label = c.get("label") or c.get("label_contains") or ""
            w = match.looks_like_write(label)
            if w and not is_write:
                out.append(Problem("F-10.write", f"action {i + 1}: control {label!r} looks like a write ({w!r}) "
                                                 "but the step is not a flagged, approved write", n))
            d = deny.denied({"own_label": label}) if label else None
            if d:
                out.append(Problem("C-08", f"action {i + 1}: control {label!r} hits the never-offer list ({d!r}); "
                                           "jev will never be offered it", n))
            if a.get("op") == "type":
                w = match.looks_like_write(a.get("text", "").replace("{unique}", ""))
                if w and not is_write:
                    out.append(Problem("F-10.write", f"action {i + 1}: typed text looks like a write ({w!r})", n))
    # controls resolve to exactly one element in the latest exploration (F-08, D-11)
    rec, rec_path = latest_exploration(spec, explore_dir)
    if rec is None:
        if require_explore:
            out.append(Problem("D-11", "no exploration record for this spec: explore every view with jev first "
                                       f"(expected in {explore_dir_for(spec, explore_dir)})"))
    else:
        if rec.get("synthetic"):
            out.append(Problem("D-10", f"the exploration record {os.path.basename(rec_path)} is synthetic (not from "
                                       "jev on the live app); explore for real before filming", severity="warning"))
        obs = {(o.get("step"), int(o.get("action", 0))): o for o in rec.get("observations", [])}
        for s in rsteps:
            n = s.get("name")
            is_write = bool(s.get("write")) and n in approved_steps
            allow = list(approved_steps[n].get("labels", [])) if is_write else []
            deny = match.DenyList(extra=deny_extra, allow=allow)
            for i, a in enumerate(s.get("actions") or []):
                if a.get("op") not in JEV_OPS:
                    continue
                if any(k in RUNTIME_PLACEHOLDERS for k in PH.findall(json.dumps(a.get("control") or {}))):
                    out.append(Problem("D-11", f"action {i + 1}: the control names an item this job creates; it is "
                                               "resolved at take time (exactly one match or the step fails)", n,
                                       severity="warning"))
                    continue
                o = obs.get((n, i))
                if o is None:
                    out.append(Problem("D-11", f"action {i + 1}: not in the exploration record {os.path.basename(rec_path)}", n))
                    continue
                target = {k: v for k, v in (a.get("control") or {}).items() if k != "option" or a.get("op") == "select"}
                if a.get("op") == "select" and a.get("option"):
                    target["option"] = a["option"]
                try:
                    match.offered_set(o.get("actions", []), n, target, a["op"], deny)
                except match.ResolveError as e:
                    cands = "; ".join(str(c.get("own_label") or c.get("label")) for c in e.candidates[:5])
                    out.append(Problem("D-11", f"action {i + 1}: control matches {e.kind}: {e}"
                                               + (f" (candidates: {cands})" if cands else ""), n))
    # length (F-05, F-10)
    est, _parts = estimate_length(spec, kv)
    if isinstance(ln, list) and len(ln) == 2 and not (ln[0] <= est <= ln[1]):
        out.append(Problem("F-10.length", f"estimated length {est} s is outside the range {ln[0]}-{ln[1]} s"))
    return out


def errors(problems):
    return [p for p in problems if p.severity == "error"]


# --------------------------------------------------------------------------------------- approval view (F-05)

def human_check(c):
    t, v = c.get("type"), c.get("value")
    if t == "text_visible":
        return f'shows "{v}"'
    if t == "text_absent":
        return f'"{v}" is gone'
    if t == "url_contains":
        return f'URL contains "{v}"'
    if t == "url_regex":
        return f"URL matches /{v}/"
    if t == "dialog_open":
        return f'dialog open with "{v}"' if v else "a dialog is open"
    if t == "dialog_closed":
        return "no dialog open"
    if t == "title_contains":
        return f'title contains "{v}"'
    if t == "count":
        return f"{v} × {c.get('what') or c.get('selector')}"
    if t == "element_visible":
        what = c.get("label") or c.get("label_contains") or c.get("selector") or c.get("role")
        n = f"{c['count']} × " if c.get("count") is not None else ""
        return f'{n}"{what}" visible'
    return json.dumps(c, ensure_ascii=False)


def approval_view(spec, knobs=None, width=110):
    kv = knobs or K.resolve(spec.get("knobs"), None)[0]
    r, _ = resolve(spec)
    est, parts = estimate_length(spec, kv)
    ln = r.get("length", [kv["length_min_s"], kv["length_max_s"]])
    lines = [f"# {r.get('title')}", "", f"Purpose: {r.get('purpose')}", f"Spec: {r.get('name')}  ·  "
             f"contract {fingerprint(spec)}", "", f"Start view: {r['start'].get('url')} "
             f"(ready: {'; '.join(human_check(c) for c in r['start'].get('ready', []))}; hold {r['start'].get('hold')} s)", ""]
    for su in setup_steps(r):
        lines[-1:-1] = [f"Off camera, before recording: {su.get('does')}"
                        + (f"  [WRITE {su['write']}]" if su.get("write") else "")]
    rows = [("#", "Step", "What happens", "Expected state", "Hold")]
    for i, s in enumerate(r.get("steps", []), 1):
        hold = f"{float(s.get('hold', kv['hold_step_s'])):g} s" + (" final" if s.get("hold_kind") == "final" else "")
        ex = "; ".join(human_check(c) for c in s.get("expect", []))
        does = s.get("does", "") + (f"  [WRITE {s['write']}]" if s.get("write") else "")
        rows.append((str(i), s.get("name", ""), does, ex, hold))
    import textwrap
    cap = [3, 14, 40, 44, 10]
    w = [min(max(len(row[k]) for row in rows), cap[k]) for k in range(5)]
    for j, row in enumerate(rows):
        cells = [textwrap.wrap(row[k], w[k]) or [""] for k in range(5)]
        for ln_ in range(max(len(c) for c in cells)):
            lines.append("| " + " | ".join((cells[k][ln_] if ln_ < len(cells[k]) else "").ljust(w[k])
                                           for k in range(5)) + " |")
        lines.append("|" + "|".join(("-" if j == 0 else " ") * (w[k] + 2) for k in range(5)) + "|"
                     if j == 0 else "|" + "|".join("-" * (w[k] + 2) for k in range(5)) + "|")
    lines.append("")
    appr = {x.get("id"): x for x in (spec.get("approval") or {}).get("writes", [])}
    if r.get("writes"):
        lines.append("Writes:")
        for wr in r["writes"]:
            a = appr.get(wr.get("id"))
            who = f"approved by {a.get('by')} at {a.get('at')}" if a else "NOT APPROVED"
            cl = wr.get("cleanup")
            lines.append(f"  - {wr.get('id')}: {wr.get('changes')} (step {wr.get('step')}; {who}; cleanup: "
                         f"{'delete via ' + repr(cl.get('control', {}).get('label')) if cl else 'none, items are listed'})")
    else:
        lines.append("Writes: read-only (nothing is changed)")
    pv = r.get("privacy", {})
    lines += [f"Privacy: account {pv.get('account')}; on screen: {pv.get('shown')}; fine because {pv.get('why_ok')} "
              "(v1 has no blur)"]
    if r.get("site_switches"):
        lines.append("Site switches: " + ", ".join(f"{x['step']} → {x['site']}" for x in r["site_switches"]))
    if r.get("preconditions"):
        lines.append("Preconditions: " + "; ".join(f"{p.get('what')} ({p.get('by')})" for p in r["preconditions"]))
    ok = ln[0] <= est <= ln[1]
    lines.append(f"Estimated length: {est:g} s (holds {parts['holds']:g} + actions {parts['actions']:g} + typing "
                 f"{parts['typing']:g} + camera {parts['camera']:g}) against the range {ln[0]}-{ln[1]} s: "
                 f"{'inside' if ok else 'OUTSIDE'}")
    ap = spec.get("approval") or {}
    if ap.get("fingerprint"):
        state = "current" if ap["fingerprint"] == fingerprint(spec) else "STALE (the contract changed since)"
        lines.append(f"Approval: {ap.get('approver')} at {ap.get('approved_at')} ({ap.get('fingerprint')}, {state})")
    else:
        lines.append("Approval: not yet approved")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------ approval (F-06)

def _toml_str(s):
    return json.dumps(str(s), ensure_ascii=False)


def _approval_block(ap):
    out = ["[approval]", f"approver = {_toml_str(ap['approver'])}", f"approved_at = {_toml_str(ap['approved_at'])}",
           f"mode = {_toml_str(ap['mode'])}", f"fingerprint = {_toml_str(ap['fingerprint'])}"]
    if ap.get("instruction"):
        out.append(f"instruction = {_toml_str(ap['instruction'])}")
    ws = ", ".join("{ id = %s, by = %s, at = %s }" % (_toml_str(w["id"]), _toml_str(w["by"]), _toml_str(w["at"]))
                   for w in ap.get("writes", []))
    out.append(f"writes = [{ws}]")
    out.append("assumptions = [" + ", ".join(_toml_str(a) for a in ap.get("assumptions", [])) + "]")
    return "\n".join(out) + "\n"


def _rewrite_approval(path, ap):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"(?m)^\[approval\]\s*$", text)
    if not m:
        raise SpecError("the spec has no [approval] table (it must be the last table of the file)")
    rest = text[m.end():]
    if re.search(r"(?m)^\s*\[", rest):
        raise SpecError("[approval] must be the last table of the spec file")
    new = text[: m.start()] + _approval_block(ap)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, path)


def snapshot_paths(spec):
    base = os.path.join(os.path.dirname(spec["_path"]), ".approved")
    return os.path.join(base, spec["name"] + ".toml"), os.path.join(base, spec["name"] + ".contract.json")


def approve(path, *, by=None, write_ids=(), unattended_instruction=None, assumptions=(), explore_dir=None,
            pdir=None, now=None):
    """Record an explicit approval. Refused when validation has errors (F-10). Returns the approval dict.

    attended: `by` is the human who said OK; writes are approved only when named in write_ids (C-34).
    unattended (F-37): approver = "unattended, from instruction <fp>"; a write counts as approved only when the
    instruction names and approves it ("approve write <id>").
    """
    spec = load(path)
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    ap = {"approved_at": now, "writes": [], "assumptions": list(assumptions)}
    if unattended_instruction is not None:
        text = unattended_instruction
        ap.update(approver=f"unattended, from instruction {text_fingerprint(text)}", mode="unattended",
                  instruction=text_fingerprint(text))
        for w in spec.get("writes", []):
            if re.search(r"(?i)\bapprove\s+write\s+" + re.escape(w.get("id", "")) + r"\b", text):
                ap["writes"].append({"id": w["id"], "by": ap["approver"], "at": now})
    else:
        if not by:
            raise SpecError("attended approval needs the approver's name (--by)")
        ap.update(approver=by, mode="attended")
        known = {w.get("id") for w in spec.get("writes", [])}
        for wid in write_ids:
            if wid not in known:
                raise SpecError(f"--approve-write {wid!r}: no such write in the spec")
            ap["writes"].append({"id": wid, "by": by, "at": now})
    # validate as it will be after approval (write approvals included), block on errors
    trial = dict(spec)
    trial["approval"] = ap
    errs = errors(validate(trial, explore_dir=explore_dir, pdir=pdir))
    if errs:
        raise SpecError("approval refused, the spec does not validate:\n  " + "\n  ".join(map(str, errs)))
    ap["fingerprint"] = fingerprint(spec)
    _rewrite_approval(path, ap)
    snap, cjson = snapshot_paths(spec)
    os.makedirs(os.path.dirname(snap), exist_ok=True)
    shutil.copyfile(path, snap)
    with open(cjson, "w", encoding="utf-8") as f:
        json.dump({"fingerprint": ap["fingerprint"], "approval": ap, "contract": contract(spec)}, f, indent=1,
                  ensure_ascii=False)
    return ap


def approval_status(spec):
    """(ok, reason). ok only if approved, the contract is unchanged since, and every write is approved."""
    ap = spec.get("approval") or {}
    if not ap.get("fingerprint") or not ap.get("approver"):
        return False, "the spec is not approved (F-06)"
    fp = fingerprint(spec)
    if ap["fingerprint"] != fp:
        return False, f"the contract changed since approval ({ap['fingerprint']} approved, now {fp}); re-approve (F-06)"
    appr = {w.get("id") for w in ap.get("writes", [])}
    for w in spec.get("writes", []):
        if w.get("id") not in appr:
            return False, f"write {w.get('id')!r} is not approved (C-34)"
    return True, "approved"


# ------------------------------------------------------------------------------------------------- diff

def _flat(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flat(v, f"{prefix}.{k}" if prefix else k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flat(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = obj
    return out


def diff(path):
    """Diff the spec against its approved snapshot. Returns (contract_changed, report_text)."""
    spec = load(path)
    snap, cjson = snapshot_paths(spec)
    if not os.path.exists(cjson):
        return True, "no approved snapshot: the spec was never approved\n"
    with open(cjson, encoding="utf-8") as f:
        appr = json.load(f)
    old, new = _flat(appr["contract"]), _flat(contract(spec))
    lines = [f"approved: {appr['fingerprint']} by {appr['approval'].get('approver')} at "
             f"{appr['approval'].get('approved_at')}", f"current:  {fingerprint(spec)}", ""]
    changed = False
    for k in sorted(set(old) | set(new)):
        if old.get(k) != new.get(k):
            changed = True
            lines.append(f"  CONTRACT {k}: {old.get(k)!r} -> {new.get(k)!r}")
    lines.append("contract: " + ("CHANGED - re-approval needed before a take can be a hit (F-06)" if changed
                                 else "unchanged (only the how differs, if anything)"))
    with open(snap, encoding="utf-8") as f:
        a = f.read().splitlines()
    with open(path, encoding="utf-8") as f:
        b = f.read().splitlines()

    def strip_appr(ls):
        out = []
        for x in ls:
            if x.startswith("[approval]"):
                break
            out.append(x)
        return out
    ud = list(difflib.unified_diff(strip_appr(a), strip_appr(b), "approved", "current", lineterm="", n=1))
    lines += ["", "text diff (approval block excluded):"] + (ud or ["  (none)"])
    return changed, "\n".join(lines) + "\n"
