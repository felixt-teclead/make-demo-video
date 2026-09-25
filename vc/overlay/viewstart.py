"""View start positions (D-33 item 3): a profile-declared view is set to its start position in the same frame it first
paints, so nothing jumps on camera and sticky elements sit where they will stay.

A profile (profiles/<app>/profile.json) declares rules; each cites its quirk record (D-31):

    "view_start": [{"id": "sticky-lane-headers",             # the quirk record id in specs/quirks/<host>.md
                    "url_regex": "/processes/[0-9a-f-]{36}",  # JS RegExp tested against location.href
                    "selector": ".react-flow__viewport",      # the element that holds the view's position
                    "when": ".react-flow__node-swimlaneLane", # optional: only while this selector exists too
                    "by": "wheel",                            # "wheel" (transform canvas) or "scroll" (scroll box)
                    "x": 0, "y": 0,                           # the start position (translation or scroll offset);
                                                              # omit an axis to leave it alone
                    "wheel_px_per_unit": 0.5,                 # optional first guess, refined in the page
                    "settle_ms": 1500}]                       # optional: re-apply window after the view appears

The take runner installs the rules of the spec's profiles right after the jev session opens, before the warm routes and
the start URL load (loop/vcloop/take_runner.py), in dry runs and takes alike, so a dry run proves the mitigation
(D-40). The page part is viewstart.js; its log (window.__vcViewStart.log) lands in the run's result.json.
"""
import json
import os
import re

_JS_PATH = os.path.join(os.path.dirname(__file__), "viewstart.js")
BY = ("wheel", "scroll")
_KEYS = {"id", "url_regex", "selector", "when", "by", "x", "y", "wheel_px_per_unit", "settle_ms", "why", "quirk"}


class RuleError(ValueError):
    pass


def validate(rule, where="view_start"):
    """Raise RuleError for a rule the page script could not apply; return a clean copy (unknown keys refused)."""
    if not isinstance(rule, dict):
        raise RuleError(f"{where}: a rule must be an object")
    extra = set(rule) - _KEYS
    if extra:
        raise RuleError(f"{where}: unknown keys {sorted(extra)}")
    for k in ("id", "url_regex", "selector"):
        if not isinstance(rule.get(k), str) or not rule[k].strip():
            raise RuleError(f"{where}: `{k}` is required (a non-empty string)")
    try:
        re.compile(rule["url_regex"])
    except re.error as e:
        raise RuleError(f"{where} {rule['id']}: url_regex does not compile: {e}") from None
    if rule.get("by", "scroll") not in BY:
        raise RuleError(f"{where} {rule['id']}: `by` must be one of {BY}")
    if rule.get("x") is None and rule.get("y") is None:
        raise RuleError(f"{where} {rule['id']}: give the start position `x` and/or `y`")
    for k in ("x", "y", "wheel_px_per_unit", "settle_ms"):
        v = rule.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
            raise RuleError(f"{where} {rule['id']}: `{k}` must be a number")
    if rule.get("wheel_px_per_unit") is not None and rule["wheel_px_per_unit"] <= 0:
        raise RuleError(f"{where} {rule['id']}: wheel_px_per_unit must be > 0")
    out = {k: v for k, v in rule.items() if k in _KEYS - {"why", "quirk"}}
    out.setdefault("by", "scroll")
    return out


def rules_from_profiles(profiles):
    """Every profile's `view_start` rules, validated, in profile order (ids unique across profiles)."""
    out, ids = [], set()
    for p in profiles or []:
        for i, r in enumerate(p.get("view_start") or []):
            r = validate(r, f"profile {p.get('name', '?')} view_start[{i}]")
            if r["id"] in ids:
                raise RuleError(f"view_start id {r['id']!r} is declared twice")
            ids.add(r["id"])
            out.append(r)
    return out


def source(rules):
    """The page script with the rules bound: `(function (rules) {...})([...]);`."""
    with open(_JS_PATH, encoding="utf-8") as f:
        fn = f.read().strip()
    return f"{fn}({json.dumps(list(rules), ensure_ascii=False)});"


def install(call, rules):
    """Install on the filmed tab: `call(method, **params)` is a CDP call on that tab's session.
    Page.enable is required for Page.addScriptToEvaluateOnNewDocument to run in new documents. Returns the script
    identifier, or None when there are no rules."""
    if not rules:
        return None
    src = source(rules)
    call("Page.enable")
    ident = call("Page.addScriptToEvaluateOnNewDocument", source=src).get("identifier")
    call("Runtime.evaluate", expression=src, returnByValue=True)      # the document already loaded
    return ident


LOG_JS = "(window.__vcViewStart && window.__vcViewStart.log.splice(0)) || []"


def read_log(js):
    """Take (and clear) the page's correction log; `js(expr)` evaluates in the filmed tab. [] on any failure."""
    try:
        return list(js(LOG_JS) or [])
    except Exception:  # noqa: BLE001 - a navigating page: the entries of that document are gone anyway
        return []


def summary(entries):
    """Per correction: the rule, from -> to, and whether it landed before the view's first painted frame."""
    out = []
    for e in entries:
        out.append({"id": e.get("id"), "from": e.get("from"), "got": e.get("got"), "rounds": e.get("rounds"),
                    "before_first_paint": e.get("frame") == e.get("seen_frame") == e.get("end_frame", e.get("frame")),
                    "stuck": bool(e.get("stuck"))})
    return out
