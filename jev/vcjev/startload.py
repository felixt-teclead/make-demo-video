"""Off-camera start-URL load (C-14 exception), kept free of CDP/upstream imports so it is unit-testable.

- Page.navigate's errorText (DNS failure, refused connection, ...) fails the load: an error page is not "loaded".
- readyState is read from the NEW document only: before navigating, the old document gets a marker; "complete" counts
  only once the marker is gone (the new document has committed). A same-document navigation (no loaderId) keeps the
  document, so the marker is not required to vanish then.
"""
import time

_MARK = "window.__vcOldDoc = true"
_PROBE = "({old: window.__vcOldDoc === true, state: document.readyState})"


class StartLoadError(RuntimeError):
    pass


def load_start_url(call, evaluate, url, timeout=20, stale=(), poll=0.05):
    try:
        evaluate(_MARK)
    except stale:
        pass
    r = call("Page.navigate", url=url) or {}
    if r.get("errorText"):
        raise StartLoadError(f"start URL failed to load: {r['errorText']}")
    new_document = bool(r.get("loaderId"))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll)
        try:
            v = evaluate(_PROBE) or {}
        except stale:
            continue
        if v.get("state") == "complete" and not (new_document and v.get("old")):
            return
    raise StartLoadError(f"start URL did not finish loading within {timeout}s")
