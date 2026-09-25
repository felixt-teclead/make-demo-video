"""load_start_url: Page.navigate's errorText fails the load, and readyState is read from the NEW document only."""
import unittest

from vcjev.startload import StartLoadError, load_start_url


class Stale(Exception):
    pass


class ScriptedPage:
    """The old document says "complete" until the new one commits after `commit_after` evaluations."""

    def __init__(self, nav_result, commit_after=0):
        self.nav_result = nav_result
        self.commit_after = commit_after
        self.evals = 0
        self.old_marked = False
        self.committed = False

    def call(self, method, **kw):
        assert method == "Page.navigate"
        return dict(self.nav_result)

    def evaluate(self, expr):
        self.evals += 1
        if not self.committed and self.evals > self.commit_after:
            self.committed = True
            self.old_marked = False
            state = "loading"
        else:
            state = "complete"
        if "__vcOldDoc = true" in expr:
            self.old_marked = True
            return True
        if "__vcOldDoc" in expr:
            return {"old": self.old_marked, "state": state}
        return state


class LoadStartUrl(unittest.TestCase):
    def test_error_text_fails(self):
        p = ScriptedPage({"frameId": "f", "loaderId": "l", "errorText": "net::ERR_NAME_NOT_RESOLVED"})
        with self.assertRaises(StartLoadError):
            load_start_url(p.call, p.evaluate, "http://nope.invalid/", timeout=1, stale=(Stale,))

    def test_old_document_complete_is_not_enough(self):
        p = ScriptedPage({"frameId": "f", "loaderId": "l"}, commit_after=4)
        load_start_url(p.call, p.evaluate, "http://x/", timeout=2, stale=(Stale,), poll=0.001)
        self.assertTrue(p.committed, "returned while the old document was still showing")


if __name__ == "__main__":
    unittest.main()
