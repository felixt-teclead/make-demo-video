"""Fake tab and fake jev for the unit tests (no browser, no network)."""


class FakeStale(ValueError):
    pass


def act(node, label, kind="click", role="button", own=None, context=(), aid=None):
    return {"id": aid or f"e{node}{kind[0]}", "node": node, "kind": kind, "role": role, "label": label,
            "own_label": own if own is not None else label, "context": list(context)}


WAIT = {"id": "wait", "kind": "wait", "label": "Wait for the page to update"}


def page_actions():
    """A page like the local test page: sidebar, rows with nested delete, dialog button, twins."""
    return [
        act(1, "Übersicht", "click", "link", context=["navigation Hauptmenü"]),
        act(2, "Abmelden", "click", "link", context=["navigation Hauptmenü"]),
        # A row link whose upstream label includes its nested button's text (OD-17).
        act(3, "Erster Eintrag Löschen", "click", "link", own="Erster Eintrag", context=["row", "region Einträge"]),
        act(4, "Löschen", "click", "button", context=["row", "region Einträge"]),
        act(5, "Zweiter Eintrag", "click", "link", context=["row", "region Einträge"]),
        act(6, "Entfernen", "click", "button", context=["row", "region Einträge"]),
        act(7, "Details anzeigen"),
        act(8, "Öffnen"),
        act(9, "Öffnen"),
        act(10, "Delete"),
        act(11, "Teilen"),
        act(12, "Suche", "fill", "searchbox"),
        act(12, "Open Suche", "click", "searchbox", own="Suche"),
        act(13, "Ansicht → Karten", "select", "combobox", own="Ansicht"),
        act(14, "Schließen", context=["dialog Details"]),
        {"id": "scroll_down", "kind": "scroll", "label": "Scroll down", "delta": 560},
        WAIT,
    ]


class FakeTab:
    """Scriptable page. `pages` is a list of action lists consumed per observe()
    (the last one repeats). `checks` is a list of booleans consumed per check()."""

    def __init__(self, pages=None, checks=(True,), login=False, points=None, stale_acts=0):
        self.pages = list(pages or [page_actions()])
        self.checks = list(checks)
        self.login = login
        self.points = list(points or [{"x": 100.0, "y": 50.0, "w": 80, "h": 20}])
        self.stale_acts = stale_acts
        self.acted, self.observed, self.checked = [], 0, 0

    def observe(self, screenshot=False):
        acts = self.pages[0] if len(self.pages) == 1 else self.pages.pop(0)
        self.observed += 1
        return {"url": "http://t/", "title": "t", "text": "click Delete now", "actions": acts,
                "fingerprint": f"fp{self.observed}"}

    def login_form(self):
        return self.login

    def enrich(self, actions):
        return actions

    def check(self, check):
        self.checked += 1
        ok = self.checks[0] if len(self.checks) == 1 else self.checks.pop(0)
        return {"ok": ok, "results": [{"type": "fake", "ok": ok}]}

    def point(self, node):
        return self.points[0] if len(self.points) == 1 else self.points.pop(0)

    def fresh(self, page, action=None):
        return True

    def hover(self, point):
        self.hovered = getattr(self, "hovered", []) + [(point["x"], point["y"])]

    def act(self, action, page, text=None):
        if action["kind"] != "wait" and self.stale_acts > 0:
            self.stale_acts -= 1
            raise FakeStale("page changed")
        self.acted.append(action["id"])
        return {"executed": action["id"]}


class FakeJev:
    """Returns scripted choices: 'target' (the one offered element), 'WAIT', 'DONE', 'BLOCKED'."""

    def __init__(self, script=("target",), usage=None):
        self.script = list(script)
        self.calls = []
        self.usage = usage if usage is not None else {"input_tokens": 1000, "output_tokens": 50, "cost": 0.00005}

    def __call__(self, page, goal, history):
        self.calls.append({"actions": page["actions"], "goal": goal})
        what = self.script[0] if len(self.script) == 1 else self.script.pop(0)
        target = next(a for a in page["actions"] if a["kind"] in ("click", "fill", "select"))
        if what == "target":
            op = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}[target["kind"]]
            choice = target["id"]
        elif what == "WAIT":
            op, choice = "WAIT", "wait"
        else:
            op, choice = what, what
        return {"choice": choice, "operation": op, "target": "1", "confidence": 0.9,
                "probabilities": {choice: 0.95}, "model": "fake-jev", "usage": dict(self.usage), "latency_ms": 120}
