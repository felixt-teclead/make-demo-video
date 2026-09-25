"""Minimal stdlib-only Chrome DevTools Protocol client (runs inside the recorder container).

Usage:
    from vccdp import CDP
    with CDP.page() as c:                 # first page target of the browser at $VC_CDP_URL
        c.call("Page.enable")
        print(c.evaluate("navigator.language"))

Direct browser access is for reading state, overlays and off-camera start URLs only (C-14); clicks and typing go
through jev.
"""
import base64
import json
import os
import socket
import struct
import time
import urllib.request
from urllib.parse import urlparse

CDP_URL = os.environ.get("VC_CDP_URL", "http://127.0.0.1:9222")


def http_json(path, base=None, timeout=5):
    with urllib.request.urlopen((base or CDP_URL) + path, timeout=timeout) as r:
        return json.loads(r.read().decode())


def page_targets(base=None):
    return [t for t in http_json("/json/list", base) if t.get("type") == "page"]


class CDPError(RuntimeError):
    pass


class CDP:
    def __init__(self, ws_url, timeout=30):
        u = urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("websocket handshake failed")
            resp += chunk
        head, self._buf = resp.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise CDPError("websocket handshake refused: " + head.split(b"\r\n")[0].decode())
        self._id = 0
        self.events = []

    @classmethod
    def page(cls, base=None, **kw):
        for _ in range(50):
            try:
                pages = page_targets(base)
                if pages:
                    return cls(pages[0]["webSocketDebuggerUrl"], **kw)
            except OSError:
                pass
            time.sleep(0.2)
        raise CDPError("no page target")

    @classmethod
    def browser(cls, base=None, **kw):
        return cls(http_json("/json/version", base)["webSocketDebuggerUrl"], **kw)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    # --- websocket framing -------------------------------------------------
    def _send(self, text):
        data = text.encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126); hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127); hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        self.sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise CDPError("connection closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _recv(self):
        msg = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload = self._read(n)
            op = b1 & 0x0F
            if op == 8:
                raise CDPError("connection closed by browser")
            if op in (9, 10):
                continue
            msg += payload
            if b1 & 0x80:
                return json.loads(msg.decode())

    # --- protocol ------------------------------------------------------------
    def call(self, method, session_id=None, **params):
        self._id += 1
        mid = self._id
        m = {"id": mid, "method": method, "params": params}
        if session_id:
            m["sessionId"] = session_id
        self._send(json.dumps(m))
        while True:
            r = self._recv()
            if r.get("id") == mid:
                if "error" in r:
                    raise CDPError(f"{method}: {r['error']}")
                return r.get("result", {})
            if "method" in r:
                self.events.append(r)

    def wait_event(self, name, timeout=15):
        end = time.time() + timeout
        while True:
            for i, e in enumerate(self.events):
                if e["method"] == name:
                    return self.events.pop(i)
            if time.time() > end:
                raise CDPError("timeout waiting for " + name)
            self.sock.settimeout(max(0.05, end - time.time()))
            try:
                r = self._recv()
            except socket.timeout:
                continue
            if "method" in r:
                self.events.append(r)

    def evaluate(self, expr, await_promise=True):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=await_promise)
        if "exceptionDetails" in r:
            raise CDPError("evaluate: " + json.dumps(r["exceptionDetails"])[:400])
        return r["result"].get("value")

    def navigate(self, url, timeout=20):
        """Off-camera start-URL load (C-14 allows it at a step boundary)."""
        self.call("Page.enable")
        self.events.clear()
        self.call("Page.navigate", url=url)
        self.wait_event("Page.loadEventFired", timeout)
