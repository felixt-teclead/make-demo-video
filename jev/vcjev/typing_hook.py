"""Typing is not done by the library (the spec's exact text is never generated).

When jev chooses TYPE_TEXT on the one offered field, the runner calls
`Hooks.type_text(tab, action, point, text, submit)` instead of the upstream
text helper + insertText. The overlay milestone replaces this default with its
Q-79 typist; this default already follows Q-79's plan (flat 37 ms per
character, whitespace runs typed as one space, Enter 200 ms after the last
character only if the step submits, whole-value fallback if the result differs).
"""

import re
import time

from . import page_js

CHAR_MS = 37
ENTER_DELAY_MS = 200

_VALUE = """(node => { const e = window.__jevFast?.nodes.get(node);
  return e ? ('value' in e ? String(e.value) : e.innerText) : null; })"""


def plan_text(text):
    return re.sub(r"\s+", " ", text)


def _mod():
    return 2  # Ctrl; the filming browser runs on Linux


def default_type_text(tab, action, point, text, submit):
    text = plan_text(text)
    x, y = point["x"], point["y"]
    for event in ("mousePressed", "mouseReleased"):
        tab.call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
    tab.call("Input.dispatchKeyEvent", type="keyDown", key="a", code="KeyA", modifiers=_mod(), commands=["selectAll"])
    tab.call("Input.dispatchKeyEvent", type="keyUp", key="a", code="KeyA", modifiers=_mod())
    start = time.perf_counter()
    t_first = time.time()
    for i, ch in enumerate(text):
        delay = start + i * CHAR_MS / 1000 - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        tab.call("Input.insertText", text=ch)
    t_last = time.time()
    value = tab.js(page_js.call(_VALUE, action["node"]))
    fallback = False
    if value is not None and value != text:
        fallback = True
        tab.call("Input.dispatchKeyEvent", type="keyDown", key="a", code="KeyA", modifiers=_mod(),
                 commands=["selectAll"])
        tab.call("Input.dispatchKeyEvent", type="keyUp", key="a", code="KeyA", modifiers=_mod())
        tab.call("Input.insertText", text=text)
    if submit:
        time.sleep(ENTER_DELAY_MS / 1000)
        for kind in ("keyDown", "keyUp"):
            tab.call("Input.dispatchKeyEvent", type=kind, key="Enter", code="Enter", windowsVirtualKeyCode=13,
                     nativeVirtualKeyCode=13, **({"text": "\r"} if kind == "keyDown" else {}))
    return {"chars": len(text), "t_first": t_first, "t_last": t_last, "fallback_whole_value": fallback}
