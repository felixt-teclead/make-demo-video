"""The one filmed tab (C-07), driven through jev-ultrafast's executor.

Subclass of the upstream Browser. Differences, all outside jev's decision logic:
- attaches to the existing visible page tab instead of creating a background
  target, and never sets a device-metrics override: the recording viewport
  (the kiosk window) governs;
- close() detaches and leaves the tab open;
- read-only helpers: click point, own labels, login form, independent checks.
"""

import time

from . import cdp as _cdp
from . import page_js
from . import startload as _startload
from .upstream import StalePage, UpstreamBrowser


class TabError(RuntimeError):
    pass


class FilmedTab(UpstreamBrowser):
    def __init__(self, cdp_url, *, expect_viewport=None):  # noqa: super().__init__ deliberately not called
        self.client = _cdp.CDP(cdp_url)
        _cdp.install(self.client)
        pages = self.client.page_targets()
        if len(pages) != 1:
            raise TabError(f"expected exactly one page tab to film, found {len(pages)} (C-07)")
        self.target = pages[0]["targetId"]
        self.session = self.client("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self.after_input = None
        # Undo any override someone else left behind; the recording viewport governs.
        self.call("Emulation.clearDeviceMetricsOverride")
        if expect_viewport:
            self.assert_filmed_tab(expect_viewport)

    # ---- C-07 ---------------------------------------------------------
    def viewport(self):
        return tuple(self.evaluate("[innerWidth, innerHeight]"))

    def assert_filmed_tab(self, expect_viewport):
        pages = self.client.page_targets()
        if len(pages) != 1 or pages[0]["targetId"] != self.target:
            raise TabError(f"the filmed tab is not the only page tab ({len(pages)} page tabs) (C-07)")
        vp = self.viewport()
        if tuple(expect_viewport) != vp:
            raise TabError(f"tab viewport {vp} differs from the recording viewport {tuple(expect_viewport)} (C-07)")
        return vp

    # ---- off-camera start URL (C-14 exception) --------------------------
    def load_start_url(self, url, timeout=20):
        try:
            _startload.load_start_url(self.call, self.evaluate, url, timeout=timeout, stale=(StalePage,))
        except _startload.StartLoadError as e:
            raise TabError(str(e)) from None

    # ---- read-only helpers ------------------------------------------------
    def js(self, expression, await_promise=False):
        r = self.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=await_promise)
        if r.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return r.get("result", {}).get("value")

    def enrich(self, actions):
        nodes = sorted({a["node"] for a in actions if "node" in a})
        info = self.js(page_js.call(page_js.ENRICH, nodes)) or {}
        out = []
        for a in actions:
            extra = info.get(str(a.get("node")), {}) if "node" in a else {}
            out.append({**a, **extra})
        return out

    def point(self, node):
        return self.js(page_js.call(page_js.POINT, node))

    def hover(self, point):
        """Part of executing the chosen click/fill: move the (invisible) input pointer onto the stable click point
        before the press, so the control's hover state is painted during the 120 ms rest and not in the ripple's
        first frame (Q-76). One mouseMoved, no path: the visible cursor is the overlay (Q-61, C-14)."""
        self.call("Input.dispatchMouseEvent", type="mouseMoved", x=point["x"], y=point["y"], button="none",
                  buttons=0, pointerType="mouse")

    def login_form(self):
        return bool(self.js(page_js.LOGIN_FORM))

    def check(self, check):
        return self.js(page_js.call(page_js.CHECK, check))

    def close(self):
        try:
            if self.session:
                self.client("Target.detachFromTarget", sessionId=self.session)
        except Exception:
            pass
        self.client.close()
        self.target = self.session = None
