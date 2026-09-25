"""The jev HTTP transport (vcjev.upstream._send): retries, timing record, keep-alive, warm-up. No network.

Needs httpx and jev-ultrafast (the recorder image / jev/tests/env). Live latency: bench_latency.py.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import httpx

    from vcjev import upstream as U
except ModuleNotFoundError:  # host run without the jev deps: skip, as the integration tests do
    httpx = U = None

OK = {"answers": {}, "model": "m", "usage": {"input_tokens": 1}}


@unittest.skipIf(U is None, "needs httpx and jev-ultrafast (recorder image)")
class Transport(unittest.TestCase):
    def setUp(self):
        self.saved = U.um.CLIENT
        self.seen = []
        self.slept = []

    def tearDown(self):
        U.um.CLIENT = self.saved

    def use(self, *replies):
        replies = list(replies)

        def handler(request):
            self.seen.append(request.method)
            r = replies.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        U.um.CLIENT = httpx.Client(transport=httpx.MockTransport(handler))

    def send(self):
        return U._send("http://p/v1/systemone", "k", {"model": "m"}, sleep=self.slept.append)

    def test_plain_success_records_one_attempt(self):
        self.use(httpx.Response(200, json=OK, headers={"x-provider-name": "TypeSafe"}))
        body, http = self.send()
        self.assertEqual(body, OK)
        self.assertEqual([a["status"] for a in http["attempts"]], [200])
        self.assertEqual((http["backoff_ms"], http["provider"]), (0, "TypeSafe"))
        self.assertEqual(self.slept, [])

    def test_rate_limit_retries_are_visible_and_honour_retry_after(self):
        self.use(httpx.Response(429, headers={"retry-after": "0.2"}), httpx.Response(503), httpx.Response(200, json=OK))
        _, http = self.send()
        self.assertEqual([a["status"] for a in http["attempts"]], [429, 503, 200])
        self.assertEqual(self.slept, [0.2, 1.0])  # Retry-After, then upstream's schedule (0.5 * 2**1)
        self.assertEqual(http["backoff_ms"], 1200)

    def test_retry_after_is_capped(self):
        self.use(httpx.Response(429, headers={"retry-after": "30"}), httpx.Response(200, json=OK))
        self.send()
        self.assertEqual(self.slept, [U.RETRY_AFTER_CAP_S])

    def test_three_overloads_fail_without_action(self):
        self.use(httpx.Response(529), httpx.Response(529), httpx.Response(529))
        with self.assertRaisesRegex(RuntimeError, "HTTP 529; no action executed"):
            self.send()

    def test_one_dropped_connection_is_retried_once(self):
        self.use(httpx.RemoteProtocolError("connection closed"), httpx.Response(200, json=OK))
        _, http = self.send()
        self.assertEqual([a["status"] for a in http["attempts"]], [None, 200])
        self.use(httpx.ConnectError("x"), httpx.ConnectError("y"))
        with self.assertRaisesRegex(RuntimeError, "connection failed"):
            self.send()

    def test_client_keeps_the_connection_between_decisions(self):
        # httpx's default keep-alive (5 s) is shorter than the gap between decisions in a take.
        pool = U.make_client()._transport._pool
        self.assertGreaterEqual(pool._keepalive_expiry, 60)
        self.assertTrue(pool._http2)
        self.assertIs(type(self.saved), httpx.Client)
        self.assertEqual(self.saved._transport._pool._keepalive_expiry, U.KEEPALIVE_S)

    def test_warm_opens_without_a_model_call_and_ignores_errors(self):
        self.use(httpx.Response(405))
        self.assertIsInstance(U.warm("http://p/v1/systemone"), int)
        self.assertEqual(self.seen, ["HEAD"])
        self.use(httpx.ConnectError("offline"))
        self.assertIsInstance(U.warm("http://p/v1/systemone"), int)

    def test_patched_post_json_logs_the_breakdown(self):
        self.use(httpx.Response(200, json=OK))
        saved = U.ROUTE.jev
        U.ROUTE.jev = {"url": "http://p/v1/systemone", "key": "k", "model": "m"}
        try:
            U.um.post_json(U.UPSTREAM_JEV_URL, "ignored", {"model": "x"})
        finally:
            U.ROUTE.jev = saved
        self.assertEqual(U.ROUTE.last["http"]["attempts"][0]["status"], 200)


if __name__ == "__main__":
    unittest.main()
