"""Fixture test page (Q-91): static checks without Chrome.

Run from the repo root: python3 -m unittest discover -s tests/fixture -t .
"""

import glob
import html.parser
import http.client
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WWW = os.path.join(ROOT, "env", "www")
PAGE = os.path.join(WWW, "fixture")
SPECS = [os.path.join(ROOT, "specs", "examples", n)
         for n in ("w-fixture-write.toml", "q55-fixture-paste.toml", "q30-fixture-late.toml")]
PORT = 7818

CONTROL_TAGS = {"a", "button", "input", "textarea", "select", "summary"}
CONTROL_ROLES = {"button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "option", "combobox",
                 "textbox", "searchbox", "spinbutton", "gridcell"}
LANDMARK = {"nav": "navigation", "aside": "complementary", "header": "banner", "footer": "contentinfo",
            "main": "main", "form": "form", "dialog": "dialog", "section": "region", "table": "table",
            "tr": "row", "li": "listitem", "ul": "list", "ol": "list"}
VOID = {"meta", "link", "input", "br", "img", "hr", "path", "rect", "source", "wbr"}


def read(name):
    with open(os.path.join(PAGE, name), encoding="utf-8") as f:
        return f.read()


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def is_control(self):
        if self.tag == "a":
            return "href" in self.attrs
        return self.tag in CONTROL_TAGS or self.attrs.get("role") in CONTROL_ROLES

    def text(self, skip_controls=False):
        out = []
        for c in self.children:
            if isinstance(c, str):
                out.append(c)
            elif c.tag not in ("script", "style") and not (skip_controls and c.is_control()):
                out.append(c.text(skip_controls))
        return clean(" ".join(out))

    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.walk()


class Doc(html.parser.HTMLParser):
    def __init__(self, src):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root
        self.feed(src)
        self.nodes = list(self.root.walk())
        self.by_id = {n.attrs["id"]: n for n in self.nodes if "id" in n.attrs}

    def handle_starttag(self, tag, attrs):
        n = Node(tag, attrs, self.cur)
        self.cur.children.append(n)
        if tag not in VOID:
            self.cur = n

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)

    def ids_text(self, ids):
        return clean(" ".join(self.by_id[i].text() for i in (ids or "").split() if i in self.by_id))

    def own_label(self, n):
        """Same order as jev's own label (docs/jev-interface.md): labelledby, aria-label, <label for>, own text."""
        lab = self.ids_text(n.attrs.get("aria-labelledby")) or n.attrs.get("aria-label", "")
        if not lab and "id" in n.attrs:
            lab = " ".join(l.text() for l in self.nodes if l.tag == "label" and l.attrs.get("for") == n.attrs["id"])
        if not lab and n.tag != "input":
            lab = n.text(skip_controls=True)
        return clean(lab or n.attrs.get("title") or n.attrs.get("placeholder"))

    def role(self, n):
        if "role" in n.attrs:
            return n.attrs["role"]
        if n.tag == "a":
            return "link"
        if n.tag == "input":
            return {"checkbox": "checkbox", "radio": "radio", "number": "spinbutton"}.get(n.attrs.get("type"), "textbox")
        return n.tag

    def context(self, n):
        out, a = [], n.parent
        while a is not None and a is not self.root:
            role = a.attrs.get("role") or LANDMARK.get(a.tag, "")
            name = self.ids_text(a.attrs.get("aria-labelledby")) or a.attrs.get("aria-label", "")
            if role or name:
                out.append(clean(role + (" " + name if name else "")))
            a = a.parent
        return out

    def controls(self):
        return [n for n in self.nodes if n.is_control()]

    def resolve(self, target):
        """Controls matching a spec control (label | label_contains, role, within), case-insensitive."""
        hits = []
        for n in self.controls():
            own = self.own_label(n).casefold()
            if "label" in target and own != clean(target["label"]).casefold():
                continue
            if "label_contains" in target and clean(target["label_contains"]).casefold() not in own:
                continue
            if target.get("role") and self.role(n) != target["role"]:
                continue
            if target.get("within") and not any(target["within"].casefold() in c.casefold() for c in self.context(n)):
                continue
            hits.append(n)
        return hits


def spec_actions():
    for path in SPECS:
        with open(path, "rb") as f:
            spec = tomllib.load(f)
        for step in spec["steps"]:
            for act in step.get("actions", []):
                yield os.path.basename(path), step, act, spec


def fill(s, params):
    return re.sub(r"\{(\w+)\}", lambda m: str(params.get(m.group(1), m.group(0))), s)


class FixturePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read("index.html")
        cls.js = read("app.js")
        cls.css = read("style.css")
        cls.page2 = read("page2.html")
        cls.readme = read("README.md")
        cls.doc = Doc(cls.html)

    def test_spec_controls_resolve_exactly_once(self):
        n = 0
        for spec, step, act, full in spec_actions():
            target = {k: fill(v, full.get("params", {})) for k, v in act.get("control", {}).items()}
            if not target or any("{" in v for v in target.values()):
                continue  # runtime names ({unique}, {item}): rows are built by app.js, checked below
            hits = self.doc.resolve(target)
            self.assertEqual(len(hits), 1, f"{spec} step {step['name']!r}: {target} matches {len(hits)} controls")
            n += 1
        self.assertGreaterEqual(n, 8)

    def test_case_w_bindings(self):
        d = self.doc
        (name,) = d.resolve({"label": "Name des Eintrags"})
        self.assertEqual(d.role(name), "textbox")
        self.assertIn("form Neuer Eintrag", d.context(name))
        (create,) = d.resolve({"label": "Erstellen"})
        self.assertIn("form Neuer Eintrag", d.context(create))
        (close,) = d.resolve({"label": "Schließen", "within": "dialog"})
        dlg = d.by_id["dlg"]
        self.assertEqual(dlg.attrs.get("role"), "dialog")
        self.assertEqual(dlg.attrs.get("aria-labelledby"), "dlg-h")
        self.assertIn("hidden", dlg.attrs)
        (tab,) = d.resolve({"label": "Details", "role": "tab"})
        panel = d.by_id[tab.attrs["aria-controls"]]
        self.assertEqual(panel.attrs.get("role"), "tabpanel")
        self.assertIn("hidden", panel.attrs, "the Details panel is hidden until the tab is clicked")
        self.assertIn("Anzahl Einträge", panel.text())
        # "Anzahl Einträge" only in that panel; "Neuer Eintrag" only on the Einträge route
        self.assertEqual(self.html.count("Anzahl Einträge"), 1)
        self.assertEqual(self.html.count("Neuer Eintrag"), 1)

    def test_rows_are_built_for_case_w(self):
        js = self.js
        self.assertIn("row.setAttribute('role', 'row')", js)
        self.assertIn("row.setAttribute('aria-label', 'Eintrag ' + it.name)", js)  # within = "{item}"
        self.assertIn("open.textContent = it.name", js)                             # item name is its own button
        self.assertIn("del.textContent = 'Löschen'", js)                             # one per row
        self.assertIn("openDialog(it)", js)
        self.assertIn("$('dlg-h').textContent = it.name", js)                        # dialog named by the item
        self.assertIn("localStorage", js)
        # status texts must not repeat an item name (text_absent {item} after cleanup)
        for msg in re.findall(r"st\.textContent = '([^']*)'|'create-status'\)\.textContent = '([^']*)'", js):
            self.assertNotIn("+", "".join(msg))

    def test_expected_texts_exist(self):
        src = self.html + self.js
        for spec, step, act, full in spec_actions():
            checks = list(step.get("expect", [])) + list(act.get("done", []))
            for c in checks:
                v = fill(c.get("value", ""), full.get("params", {}))
                if c["type"] == "text_visible" and "{" not in v:
                    self.assertIn(v, src, f"{spec} step {step['name']!r}: text {v!r} not on the page")

    def switches(self):
        return re.findall(r"^\| `(\w+)=[^`]*` \|", self.readme, re.M)

    def test_every_switch_documented_and_wired(self):
        documented = set(self.switches())
        wired = set(re.findall(r"\b(?:on|num)\('(\w+)'", self.js))
        self.assertEqual(documented, wired, "README switch table and app.js params differ")
        for k in ("whiteout", "black", "orphan", "nesteddim", "skeleton", "sticky", "dash", "jitter",
                  "halfpaint", "pointer", "banner", "late", "panel"):
            self.assertIn(k, documented)
        for k in wired - {"late", "panel"}:
            self.assertRegex(self.js, rf"D\.{k}\b", f"switch {k} is parsed but never used")
        self.assertIn("whiteout", self.page2)

    def test_no_network_no_gif_no_input_from_script(self):
        for name, src in (("index.html", self.html), ("app.js", self.js), ("style.css", self.css),
                          ("page2.html", self.page2)):
            urls = [u for u in re.findall(r"(?:https?:)?//[\w.-]+\.\w+[^\s'\"<>)]*", src)
                    if not u.startswith("http://www.w3.org/2000/svg")]
            self.assertEqual(urls, [], f"{name}: external URL")
            for bad in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "sendBeacon", "import(",
                        ".gif", "@import", "dispatchEvent", ".click(", "MouseEvent", "KeyboardEvent",
                        "execCommand", "<iframe", "<img"):
                self.assertNotIn(bad, src, f"{name}: {bad}")
        files = [p for p in glob.glob(os.path.join(PAGE, "**"), recursive=True) if os.path.isfile(p)]
        self.assertFalse([p for p in files if p.lower().endswith(".gif")])
        self.assertLess(sum(os.path.getsize(p) for p in files), 40 * 1024)

    def test_js_syntax(self):
        node = shutil.which("node") or os.path.expanduser("~/.local/bin/node")
        if not os.path.exists(node):
            self.skipTest("node not found")
        scripts = {"app.js": self.js}
        for name, src in (("index.html", self.html), ("page2.html", self.page2)):
            for i, s in enumerate(re.findall(r"<script>(.*?)</script>", src, re.S)):
                scripts[f"{name}#{i}"] = s
        with tempfile.TemporaryDirectory() as tmp:
            for name, src in scripts.items():
                p = os.path.join(tmp, re.sub(r"\W", "_", name) + ".js")
                with open(p, "w", encoding="utf-8") as f:
                    f.write(src)
                r = subprocess.run([node, "--check", p], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, f"{name}: {r.stderr}")

    def test_served_with_200(self):
        srv = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1",
                                "--directory", WWW], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            paths = ["/fixture/index.html", "/fixture/page2.html", "/fixture/app.js", "/fixture/style.css"]
            deadline = time.time() + 10
            for p in paths:
                while True:
                    try:
                        c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=2)
                        c.request("GET", p)
                        r = c.getresponse()
                        r.read()
                        c.close()
                        break
                    except OSError:
                        if time.time() > deadline:
                            raise
                        time.sleep(0.1)
                self.assertEqual(r.status, 200, p)
        finally:
            srv.terminate()
            srv.wait(5)


if __name__ == "__main__":
    unittest.main()
