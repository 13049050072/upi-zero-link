"""Offline tests for the service configuration and batch runner."""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import Mock, patch

from upi_zero_link import cli, protocol
from upi_zero_link._vendor.http import make_session
from upi_zero_link.proxy import normalize_proxy
from upi_zero_link.service import extract_via_service, normalize_service_url


class Response:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body


class FeatureTests(unittest.TestCase):
    def test_parser_supports_service_url_cdk_and_upi_concurrency(self):
        args = cli.build_parser().parse_args([
            "--token", "token-placeholder", "--api-url", "https://service.test/extract",
            "--cdk", "cdk-placeholder", "--concurrency", "4",
        ])
        self.assertEqual(args.service_url, "https://service.test/extract")
        self.assertEqual(args.cdk, "cdk-placeholder")
        self.assertEqual(args.concurrency, 4)
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["--token", "x", "--concurrency", "5"])

    def test_service_client_sends_cdk_and_fixed_upi(self):
        response = Response({"data": {"ok": True, "url": "https://service.test/link"}})
        with patch("upi_zero_link.service.requests.post", return_value=response) as post:
            result = extract_via_service(
                {"email": "a@example.test", "access_token": "token-placeholder"},
                "proxy.test:1", service_url="https://service.test/extract",
                cdk="cdk-placeholder", tries=1, emit=Mock())

        self.assertTrue(result["ok"])
        self.assertEqual(post.call_args.kwargs["headers"]["X-CDK"], "cdk-placeholder")
        self.assertEqual(post.call_args.kwargs["headers"]["X-Payment-Method"], "upi")
        self.assertEqual(post.call_args.kwargs["json"]["payment_method"], "upi")
        self.assertEqual(post.call_args.kwargs["json"]["cdk"], "cdk-placeholder")
        self.assertEqual(post.call_args.kwargs["json"]["tries"], 1)

    def test_service_url_requires_http_scheme_and_host(self):
        self.assertEqual(normalize_service_url(" https://service.test/ "),
                         "https://service.test")
        with self.assertRaises(ValueError):
            normalize_service_url("service.test/extract")

    def test_upi_is_the_only_protocol_payment_method(self):
        self.assertEqual(protocol.PAYMENT_METHOD, "upi")

    def test_provider_proxy_is_normalized_and_redacted_in_cli_label(self):
        raw = "proxy.example:3000:user@example:pass@word"
        self.assertEqual(
            normalize_proxy(raw),
            "http://user%40example:pass%40word@proxy.example:3000",
        )
        args = cli.build_parser().parse_args(["--token", "token-placeholder", "--proxy", raw])
        proxies = cli.make_proxies(args)
        self.assertEqual(proxies, [normalize_proxy(raw)])
        self.assertEqual(cli._label(proxies[0]), "proxy.example:3000")
        self.assertNotIn("user", cli._label(proxies[0]))

    def test_http_session_uses_normalized_provider_proxy(self):
        session = Mock()
        with patch("upi_zero_link._vendor.http.curl_requests.Session", return_value=session):
            make_session("proxy.example:3000:user:password")
        self.assertEqual(
            session.proxies,
            {"http": "http://user:password@proxy.example:3000",
             "https": "http://user:password@proxy.example:3000"},
        )

    def test_run_accounts_limits_workers_and_preserves_order(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def fake_run_account(account, proxies, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return {"ok": True, "email": account["email"]}

        accounts = [{"email": "a"}, {"email": "b"}, {"email": "c"}, {"email": "d"}]
        with patch("upi_zero_link.cli.run_account", side_effect=fake_run_account):
            results = cli.run_accounts(accounts, ["proxy"], rounds=1, tries=1,
                                       quiet=True, concurrency=2)

        self.assertLessEqual(peak, 2)
        self.assertEqual([account["email"] for account, _ in results], ["a", "b", "c", "d"])
        self.assertEqual([result["email"] for _, result in results], ["a", "b", "c", "d"])

    def test_run_accounts_stops_after_success_in_serial_default_mode(self):
        calls = []

        def fake_run_account(account, proxies, **kwargs):
            calls.append(account["email"])
            return {"ok": True} if account["email"] == "b" else None

        accounts = [{"email": "a"}, {"email": "b"}, {"email": "c"}]
        with patch("upi_zero_link.cli.run_account", side_effect=fake_run_account):
            results = cli.run_accounts(accounts, ["proxy"], rounds=1, tries=1,
                                       quiet=True, concurrency=1,
                                       stop_after_success=True)

        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(len(results), 2)
        self.assertTrue(results[-1][1]["ok"])

        result = {"ok": True, "url": "https://service.test/link"}
        with patch("upi_zero_link.cli.extract_via_service", return_value=result) as service_call:
            actual = cli.run_account(
                {"email": "a@example.test"}, ["proxy"], rounds=1, tries=1,
                quiet=True, service_url="https://service.test/extract", cdk="cdk-placeholder")

        self.assertIs(actual, result)
        self.assertEqual(service_call.call_args.kwargs["service_url"],
                         "https://service.test/extract")
        self.assertEqual(service_call.call_args.kwargs["cdk"], "cdk-placeholder")


if __name__ == "__main__":
    unittest.main()
