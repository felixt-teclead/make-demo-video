"""The offered set: exactly one intended control plus WAIT (C-01), never a
control on the never-offer list (C-08).

Pure Python. Works on the action list that jev-ultrafast observes, enriched
with each control's OWN label (its accessible name without the text of nested
controls, OD-17) and its container context (see page_js.ENRICH).
"""

import re
import unicodedata

# Built-in never-offer list: app-agnostic language data (C-08, C-40).
# It names write ACTIONS, so it matches the control's own label as an action, never a word inside a name:
# - VERBS match as the label's first words (English and German imperative: "Delete item", "Lösche alles") or its last
#   words (German infinitive: "Prozess löschen", "Jetzt teilen"), or the whole label. In a short label (at most
#   4 words, i.e. a button: "Yes, delete it", "Permanently delete this process") a verb matches anywhere.
# - NOUNS (billing, payment, archive ...) match only when they ARE the control: the whole label, or the label's first
#   word with at most one more word ("Billing", "Abrechnung öffnen", "Zahlung & Abo"). A longer label that merely
#   mentions the noun names something else ("Abrechnung zur Korrektur zurücksenden" is a process step), and a
#   compound noun ("Reisekostenabrechnung") never matches.
# Each entry is a regex that must match whole words (fullmatch on the joined words).
BUILTIN_DENY_VERBS = (       # verb forms only (infinitive / imperative), so participles in names do not match
    r"log\s*-?\s*out", r"log\s+off", r"sign\s*-?\s*out", r"logout", r"abmelden", r"melde\s+ab", r"ausloggen",
    r"delete", r"remove", r"löschen", r"lösche", r"loschen", r"entfernen", r"entferne", r"trash",
    r"share", r"teilen", r"teile", r"freigeben", r"gib\s+frei",
    r"archive", r"archivieren", r"archiviere",
    r"upgrade", r"subscribe", r"unsubscribe", r"abonnieren", r"abonniere", r"kündigen", r"kündige", r"pay",
    r"bezahlen", r"bezahle",
    r"always\s+allow", r"immer\s+(erlauben|zulassen)",
)
BUILTIN_DENY_NOUNS = (       # nouns that ARE a control (a menu entry or tab), only as a short label
    r"abmeldung", r"papierkorb", r"freigabe\w*", r"archiv",
    r"billing", r"abrechnung\w*", r"subscriptions?", r"abonnements?", r"abo", r"zahlung\w*", r"payments?",
)
BUILTIN_DENY = BUILTIN_DENY_VERBS + BUILTIN_DENY_NOUNS      # kept for callers that list the built-ins
_MAX_PHRASE = 4          # longest verb phrase in words ("melden sie sich ab")
_NOUN_MAX_WORDS = 2      # a noun control: the noun alone or the noun plus one word
_VERB_ANYWHERE_MAX_WORDS = 4  # a short label (a button) is denied by a verb anywhere in it; longer labels are names


def _words(label):
    return re.findall(r"\w+", label)


def _phrase_regex(patterns):
    return re.compile(r"(?:" + "|".join(patterns) + r")", re.IGNORECASE)


class ResolveError(ValueError):
    """The step's control matched zero or several elements, or only denied ones."""

    def __init__(self, step, message, candidates=(), kind="invalid"):
        super().__init__(f"step {step!r}: {message}")
        self.step = step
        self.kind = kind  # "none" | "many" | "denied" | "invalid"
        self.candidates = list(candidates)


def norm(text):
    text = unicodedata.normalize("NFC", text or "")
    return re.sub(r"\s+", " ", text).strip().casefold()


class DenyList:
    def __init__(self, extra=(), allow=()):
        """extra: labels added by the app profile and the spec (plain text). An extra label matches the control's own
        label as a whole, or as its leading or trailing words ("Veröffentlichen" denies "Jetzt veröffentlichen"),
        never inside a longer word. allow: exact own labels of approved writes (C-34) that may be offered despite
        the list."""
        self.verbs = _phrase_regex(BUILTIN_DENY_VERBS)
        self.nouns = _phrase_regex(BUILTIN_DENY_NOUNS)
        self.extra = [tuple(norm(x).split(" ")) for x in extra if norm(x)]
        self.allow = {norm(a) for a in allow}

    def denied(self, action):
        """The matched word(s) if the control's own label is a denied write action, else None."""
        label = norm(action.get("own_label", action.get("label", "")))
        if label in self.allow:
            return None
        words = [w.casefold() for w in _words(label)]
        if not words:
            return None
        n = len(words)
        for k in range(1, min(_MAX_PHRASE, n) + 1):           # verbs: leading or trailing words
            for part in (words[:k], words[n - k:]):
                phrase = " ".join(part)
                if self.verbs.fullmatch(phrase):
                    return phrase
        if n <= _VERB_ANYWHERE_MAX_WORDS:                     # short label: a verb anywhere ("Yes, delete it")
            for i in range(n):
                for k in range(1, min(_MAX_PHRASE, n - i) + 1):
                    phrase = " ".join(words[i:i + k])
                    if self.verbs.fullmatch(phrase):
                        return phrase
        if n <= _NOUN_MAX_WORDS:                              # nouns: only when the noun is the control
            for w in words[:1] + words[-1:]:
                if self.nouns.fullmatch(w):
                    return w
        for ex in self.extra:                                 # profile/spec additions: whole, leading or trailing
            k = len(ex)
            if k <= n and (tuple(words[:k]) == ex or tuple(words[n - k:]) == ex):
                return " ".join(ex)
        return None


KIND_FOR = {"click": "click", "type": "fill", "select": "select"}


def _label_matches(target, action):
    own = norm(action.get("own_label", action.get("label", "")))
    if "label" in target and own != norm(target["label"]):
        return False
    if "label_contains" in target and norm(target["label_contains"]) not in own:
        return False
    if "label_regex" in target and not re.search(target["label_regex"], action.get("own_label", ""), re.I):
        return False
    return True


def _matches(target, action):
    if not _label_matches(target, action):
        return False
    if target.get("role") and action.get("role") != target["role"]:
        return False
    if target.get("within"):
        want = norm(target["within"])
        if not any(want in norm(c) for c in action.get("context", [])):
            return False
    return True


def describe(action):
    return {k: action.get(k) for k in ("id", "kind", "role", "own_label", "node")}


def offered_set(actions, step, target, op, deny):
    """Return the actions to offer jev for this decision.

    actions: enriched upstream actions (with own_label/context).
    target:  {label | label_contains | label_regex, role?, within?, option?}
    op:      "click" | "type" | "select"
    Raises ResolveError naming the step for 0 or 2+ matching controls.
    """
    if not any(k in target for k in ("label", "label_contains", "label_regex")):
        raise ResolveError(step, "target has no label, label_contains or label_regex")
    kind = KIND_FOR[op]
    candidates = [a for a in actions if a.get("kind") == kind and _matches(target, a)]
    if op == "select" and target.get("option") is not None:
        opt = norm(target["option"])
        candidates = [a for a in candidates if norm(a["label"].split(" → ", 1)[-1]) == opt]
    denied = [(a, deny.denied(a)) for a in candidates]
    allowed = [a for a, why in denied if not why]
    blocked = [(a, why) for a, why in denied if why]
    nodes = {a["node"] for a in allowed}
    if not nodes:
        if blocked:
            raise ResolveError(
                step,
                f"the only matching control is on the never-offer list ({blocked[0][1]!r}); it is never offered",
                [describe(a) for a, _ in blocked],
                kind="denied",
            )
        raise ResolveError(step, f"no {op} control matches {target}", [], kind="none")
    if len(nodes) > 1:
        raise ResolveError(
            step, f"{len(nodes)} controls match {target}; the step must name exactly one",
            [describe(a) for a in allowed], kind="many",
        )
    chosen = list(allowed)
    if op == "select" and len(chosen) != 1:
        raise ResolveError(step, "select step must name exactly one option", [describe(a) for a in chosen],
                           kind="many")
    wait = [a for a in actions if a.get("kind") == "wait"]
    return chosen + wait[:1]


def check_offer(offer):
    """Invariant logged per decision: exactly one actionable element + control ops."""
    actionable = {a["node"] for a in offer if a.get("kind") in ("click", "fill", "select")}
    others = {a["kind"] for a in offer if a.get("kind") not in ("click", "fill", "select")}
    return len(actionable) == 1 and others <= {"wait"}
