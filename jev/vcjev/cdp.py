"""Minimal synchronous Chrome DevTools client.

Replaces jev-ultrafast's `browser_harness` transport (a daemon with cloud and
telemetry features the pipeline does not use). It speaks flat-session CDP over
one websocket to the browser endpoint. Events are dropped; only replies to our
own requests are returned.
"""

import itertools
import json
import threading
import urllib.request

import websocket  # websocket-client


class CDPError(RuntimeError):
    pass


class CDP:
    def __init__(self, http_url, timeout=30):
        self.http_url = http_url.rstrip("/")
        version = json.loads(urllib.request.urlopen(self.http_url + "/json/version", timeout=10).read())
        self.ws = websocket.create_connection(
            version["webSocketDebuggerUrl"], timeout=timeout, suppress_origin=True, enable_multithread=True
        )
        self.ids = itertools.count(1)
        self.lock = threading.Lock()

    def __call__(self, method, session_id=None, **params):
        with self.lock:
            msg_id = next(self.ids)
            msg = {"id": msg_id, "method": method, "params": params}
            if session_id:
                msg["sessionId"] = session_id
            self.ws.send(json.dumps(msg))
            while True:
                reply = json.loads(self.ws.recv())
                if reply.get("id") != msg_id:
                    continue  # an event or a late reply
                if "error" in reply:
                    raise CDPError(f"{method}: {reply['error'].get('message')}")
                return reply.get("result", {})

    def page_targets(self):
        return [t for t in self("Target.getTargets")["targetInfos"] if t["type"] == "page"]

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


_current = None


def install(client):
    """Make `client` the transport used by the jev-ultrafast modules."""
    global _current
    _current = client


def cdp(method, session_id=None, **params):
    """Signature-compatible stand-in for browser_harness.helpers.cdp."""
    if _current is None:
        raise CDPError("No browser connected (vcjev.cdp.install was not called)")
    return _current(method, session_id=session_id, **params)
