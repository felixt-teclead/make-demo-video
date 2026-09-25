"""Import boundary to the upstream jev-ultrafast library.

Two thin patches, neither touches jev's decision logic (upstream `choose`,
`validate_choice`, `action_space` and the question texts are used unchanged):

1. `browser_harness` is replaced by `vcjev.cdp` (a module shim installed before
   the upstream package is imported), so the library drives the pipeline's own
   Chrome over one websocket and never starts a daemon or sends telemetry.
2. `jev_ultrafast.model.post_json` is wrapped: the hard-coded TypeSafe URL and
   the text-helper URL are redirected to the provider in the models config
   (C-04), the model field is set to the configured version, the key comes from
   the env file, and the raw response is kept for accounting (F-28).

   The request itself is sent by vcjev's own transport (`_send`), with the same
   contract as upstream (up to 3 attempts on 429/529/503, any failure raises
   RuntimeError and no action is executed) plus what upstream hides:
   - every HTTP attempt is recorded (status, ms, backoff), so a slow decision can
     be attributed to provider time vs retries (ROUTE.last["http"]);
   - a Retry-After header is honoured (capped), otherwise upstream's 0.5 s / 1 s;
   - one retry on a connection error (a pooled HTTP/2 connection the server or a
     NAT dropped while the take was idle) instead of failing the decision;
   - the shared client keeps its connection alive for KEEPALIVE_S (httpx default
     5 s is shorter than the gap between decisions in a take: glide, settle,
     typing), and `warm()` opens it at session start so the first decision does
     not pay DNS + TCP + TLS.
"""

import sys
import time
import types

from . import cdp as _cdp

UPSTREAM_JEV_URL = "https://api.typesafe.ai/v1/systemone"
KEEPALIVE_S = 120.0
TIMEOUT_S = 25.0
RETRY_STATUS = {429, 529, 503}
MAX_ATTEMPTS = 3  # upstream: 3 attempts on RETRY_STATUS
RETRY_AFTER_CAP_S = 2.0


def _install_shim():
    if "browser_harness" in sys.modules and getattr(sys.modules["browser_harness"], "_vcjev_shim", False):
        return
    pkg = types.ModuleType("browser_harness")
    pkg._vcjev_shim = True
    pkg.__path__ = []
    admin = types.ModuleType("browser_harness.admin")
    admin.ensure_daemon = lambda *a, **k: None
    helpers = types.ModuleType("browser_harness.helpers")
    helpers.cdp = _cdp.cdp
    sys.modules.update(
        {"browser_harness": pkg, "browser_harness.admin": admin, "browser_harness.helpers": helpers}
    )


_install_shim()

import jev_ultrafast.browser as ub  # noqa: E402
import jev_ultrafast.model as um  # noqa: E402

StalePage = ub.StalePage
UpstreamBrowser = ub.Browser
browser_operation = ub.browser_operation
choose = um.choose
field_text = um.field_text
_original_post_json = um.post_json


class Route:
    """Where the (patched) upstream post_json sends its requests right now."""

    def __init__(self):
        self.jev = None  # dict(url, key, model)
        self.text = None  # dict(base_url, key, model)
        self.last = None  # dict(kind, url, latency_ms, response)
        self.calls = []  # every call: kind, url, latency_ms (no bodies, no keys)


ROUTE = Route()


def make_client():
    import httpx

    return httpx.Client(http2=True, timeout=TIMEOUT_S,
                        limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=KEEPALIVE_S))


def _backoff(response, attempt):
    try:
        after = float(response.headers.get("retry-after", ""))
        return max(0.0, min(after, RETRY_AFTER_CAP_S))
    except ValueError:
        return 0.5 * 2**attempt


def _send(url, key, body, sleep=time.sleep):
    """POST with upstream's retry contract; returns (json, http_record). Raises RuntimeError on failure."""
    import httpx

    attempts, backoff_ms, conn_retried = [], 0, False
    attempt = 0
    while True:
        t = time.perf_counter()
        try:
            response = um.CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as e:
            attempts.append({"status": None, "ms": round((time.perf_counter() - t) * 1000), "error": type(e).__name__})
            if not conn_retried:  # a dropped pooled connection: one fresh try, nothing was executed
                conn_retried = True
                continue
            raise RuntimeError("Model connection failed; no action executed.") from None
        attempts.append({"status": response.status_code, "ms": round((time.perf_counter() - t) * 1000),
                         "http": response.http_version})
        if response.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
            wait = _backoff(response, attempt)
            backoff_ms += round(wait * 1000)
            sleep(wait)
            attempt += 1
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json(), {"attempts": attempts, "backoff_ms": backoff_ms,
                                 "provider": response.headers.get("x-provider-name")}


def _patched_post_json(url, key, body):
    kind = "jev" if url == UPSTREAM_JEV_URL else "text"
    target = ROUTE.jev if kind == "jev" else ROUTE.text
    if target is None:
        raise RuntimeError(f"No {kind} provider configured (vcjev.models); no request sent.")
    body = dict(body)
    body["model"] = target["model"]
    if kind == "jev":
        real_url = target["url"]
    else:
        real_url = target["base_url"].rstrip("/") + "/chat/completions"
    started = time.perf_counter()
    try:
        response, http = _send(real_url, target["key"], body)
    except RuntimeError:
        ROUTE.calls.append({"kind": kind, "url": real_url, "latency_ms": round((time.perf_counter() - started) * 1000),
                            "failed": True})
        raise
    latency = round((time.perf_counter() - started) * 1000)
    ROUTE.last = {"kind": kind, "url": real_url, "latency_ms": latency, "response": response, "http": http}
    ROUTE.calls.append({"kind": kind, "url": real_url, "latency_ms": latency, "http": http})
    return response


def warm(url=None):
    """Open the shared HTTP/2 connection to the jev endpoint before the first decision (no model call, no cost).

    Any HTTP status is fine (the endpoint only accepts POST); errors are ignored: the first decision then simply
    connects itself. Returns the ms spent, or None when nothing was configured."""
    url = url or (ROUTE.jev or {}).get("url")
    if not url:
        return None
    t = time.perf_counter()
    try:
        um.CLIENT.request("HEAD", url, timeout=5)
    except Exception:
        pass
    return round((time.perf_counter() - t) * 1000)


um.CLIENT = make_client()
um.post_json = _patched_post_json
