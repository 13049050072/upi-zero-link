"""Offline contract tests for the JSON/SSE compatibility server."""
from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from upi_zero_link.server import (
    ServerState,
    load_server_config,
    make_server,
)
from upi_zero_link.proxy import normalize_proxy


class CompatibilityServerTests(unittest.TestCase):
    def setUp(self):
        def fake_extract(account, proxy, *, tries, emit):
            self.seen = (account, proxy, tries)
            emit("stage checkout")
            time.sleep(0.01)
            return {
                "ok": True,
                "url": "https://payments.example/upi/job",
                "qr_png": "https://qr.example/job.png",
                "expires_at": "1234567890",
                "due": 0,
            }

        self.state = ServerState(
            cdk="cdk-placeholder",
            proxy="socks5h://proxy.example:1080",
            workers=2,
            extractor=fake_extract,
        )
        self.httpd = make_server("127.0.0.1", 0, self.state)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.state.executor.shutdown(wait=True, cancel_futures=True)
        self.thread.join(timeout=2)

    def request(self, path, *, method="GET", body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        return urlopen(req, timeout=5)

    def test_extract_job_and_sse_result_match_target_contract(self):
        with self.request("/api/extract", method="POST", body={
            "token": "token-placeholder",
            "link_type": "upi",
            "cdk": "cdk-placeholder",
            "tries": 1,
        }) as response:
            self.assertEqual(response.status, 202)
            created = json.loads(response.read())

        self.assertTrue(created["ok"])
        self.assertEqual(created["link_type"], "upi")
        self.assertEqual(self.seen[0]["access_token"], "token-placeholder")
        self.assertEqual(self.seen[1], "socks5h://proxy.example:1080")
        self.assertEqual(self.seen[2], 1)

        with self.request("/api/jobs/%s/events?cdk=cdk-placeholder" % created["job_id"]) as response:
            stream = response.read().decode("utf-8")

        self.assertIn("event: log\n", stream)
        self.assertIn('"message":"stage checkout"', stream)
        self.assertIn("event: result\n", stream)
        self.assertIn('"long_url":"https://payments.example/upi/job"', stream)
        self.assertIn('"copy_paste":"https://payments.example/upi/job"', stream)
        self.assertIn('"image_url_png":"https://qr.example/job.png"', stream)
        self.assertIn('"payment_method":"upi"', stream)
        self.assertIn("event: done\n", stream)

        with self.request("/api/cdk?code=cdk-placeholder") as response:
            quota = json.loads(response.read())
        self.assertEqual(quota["used"], 1)
        self.assertEqual(quota["remaining"], None)

    def test_auth_and_payment_method_are_enforced(self):
        for body, expected in (
            ({"token": "x", "cdk": "wrong", "link_type": "upi"}, 401),
            ({"token": "x", "cdk": "cdk-placeholder", "link_type": "pix"}, 400),
        ):
            with self.assertRaises(HTTPError) as raised:
                self.request("/api/extract", method="POST", body=body)
            self.assertEqual(raised.exception.code, expected)

    def test_configured_proxy_rotation_and_unlimited_cdk(self):
        seen = []

        def fake_extract(account, proxy, *, tries, emit):
            seen.append(proxy)
            return {"ok": True, "url": "https://example.test/upi", "qr_png": ""}

        state = ServerState(
            cdk="self-use-cdk",
            proxies=[
                "socks5h://india-1.example:1080",
                "socks5h://india-2.example:1080",
            ],
            workers=1,
            extractor=fake_extract,
        )
        try:
            first = state.submit(token="token-1")
            second = state.submit(token="token-2")
            list(state.events(first, timeout=1))
            list(state.events(second, timeout=1))
            self.assertEqual(first.proxy, "socks5h://india-1.example:1080")
            self.assertEqual(second.proxy, "socks5h://india-2.example:1080")
            snapshot = state.quota.snapshot()
            self.assertTrue(snapshot["unlimited"])
            self.assertIsNone(snapshot["remaining"])
            self.assertEqual(len(state.proxies), 2)
        finally:
            state.executor.shutdown(wait=True, cancel_futures=True)

    def test_provider_proxy_format_is_normalized_without_leaking_credentials(self):
        raw = "proxy.example:3000:user@example:pass@word"
        normalized = normalize_proxy(raw)
        self.assertEqual(normalized, "http://user%40example:pass%40word@proxy.example:3000")
        self.assertNotEqual(normalized, raw)
        self.assertEqual(
            normalize_proxy("socks5h://user:pass@proxy.example:1080"),
            "socks5h://user:pass@proxy.example:1080",
        )

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".ini") as handle:
            handle.write(
                "[server]\n"
                "cdk = self-use\n"
                "max_uses = 0\n"
                "proxies =\n"
                "    socks5h://india-1:1080\n"
                "    socks5h://india-2:1080\n"
            )
            handle.flush()
            config = load_server_config(handle.name)
        self.assertEqual(config["cdk"], "self-use")
        self.assertEqual(config["max_uses"], "0")
        self.assertIn("india-2:1080", config["proxies"])



if __name__ == "__main__":
    unittest.main()
