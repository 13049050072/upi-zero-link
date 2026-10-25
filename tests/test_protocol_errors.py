"""Regression tests for upstream errors without making external requests."""
import unittest
from unittest.mock import Mock, patch

from upi_zero_link import protocol
from upi_zero_link.server import ServerState


class ProtocolErrorTests(unittest.TestCase):
    def checkout_failure(self, body):
        response = Mock(status_code=400)
        response.json.return_value = body
        with patch.object(protocol, "chatgpt_session"), \
                patch.object(protocol.oaics, "warmup_chatgpt_page"), \
                patch.object(protocol, "checkout_risk_headers", return_value={}), \
                patch.object(protocol, "request_http", return_value=response):
            return protocol.extract({"access_token": "account-token"}, "http://proxy.test:8080", emit=lambda _: None)

    def test_checkout_failure_preserves_upstream_reason_without_credentials(self):
        result = self.checkout_failure({"detail": "Our systems have detected unusual activity. account-token user@example.test https://example.test/?secret=x"})
        self.assertEqual(result["error"], "checkout_http_400")
        self.assertIn("Our systems have detected unusual activity", result.get("error_detail", ""))
        for secret in ("account-token", "user@example.test", "secret=x"):
            self.assertNotIn(secret, result.get("error_detail", ""))

    def test_checkout_non_json_error_keeps_status(self):
        response = Mock(status_code=400)
        response.json.side_effect = ValueError("not JSON")
        with patch.object(protocol, "chatgpt_session"), \
                patch.object(protocol.oaics, "warmup_chatgpt_page"), \
                patch.object(protocol, "checkout_risk_headers", return_value={}), \
                patch.object(protocol, "request_http", return_value=response):
            result = protocol.extract({"access_token": "account-token"}, "http://proxy.test:8080", emit=lambda _: None)
        self.assertEqual(result["error"], "checkout_http_400")
        self.assertEqual(result["error_detail"], "")

    def test_decline_preserves_stripe_reason_without_returning_invalid_link(self):
        def response(body):
            value = Mock(status_code=200)
            value.json.return_value = body
            return value
        declined = {
            "status": "requires_payment_method",
            "last_setup_error": {"type": "card_error", "code": "setup_intent_authentication_failure",
                                 "decline_code": "generic_decline", "message": "The latest attempt to set up the payment method has failed."},
            "next_action": {"hosted_instructions_url": "https://payments.test/expired"},
        }
        replies = [
            response({"checkout_session_id": "cs_test", "publishable_key": "pk_test", "processor_entity": "test"}),
            response({"total_summary": {"due": 0}, "payment_method_types": ["upi"], "stripe_hosted_url": "https://checkout.test"}),
            response({"total_summary": {"due": 0}, "payment_method_types": ["upi"]}),
            response({"id": "pm_test"}), response({}), response({"result": "approved"}),
            response({"setup_intent": declined}),
        ]
        with patch.object(protocol, "chatgpt_session"), \
                patch.object(protocol, "make_session"), \
                patch.object(protocol.oaics, "warmup_chatgpt_page"), \
                patch.object(protocol, "checkout_risk_headers", return_value={}), \
                patch.object(protocol.verify, "verify_url", return_value=(False, "state=requires_payment_method fam=1.00")), \
                patch.object(protocol, "request_http", side_effect=replies) as request:
            result = protocol.extract({"access_token": "account-token"}, "http://proxy.test:8080", emit=lambda _: None)
        self.assertEqual(result["error"], "declined")
        self.assertIn("generic_decline", result.get("error_detail", ""))
        self.assertIn("latest attempt", result.get("error_detail", ""))
        self.assertFalse(result["ok"])
        self.assertNotIn("url", result)
        self.assertEqual(request.call_count, 7)

    def test_check_mode_returns_methods_before_payment_method_creation(self):
        def response(body):
            value = Mock(status_code=200)
            value.json.return_value = body
            return value
        replies = [
            response({"checkout_session_id": "cs_test", "publishable_key": "pk_test"}),
            response({"total_summary": {"due": 0}, "payment_method_types": ["card", "upi"]}),
            response({"total_summary": {"due": 0}, "payment_method_types": ["card", "upi"]}),
        ]
        with patch.object(protocol, "chatgpt_session"), \
                patch.object(protocol, "make_session"), \
                patch.object(protocol.oaics, "warmup_chatgpt_page"), \
                patch.object(protocol, "checkout_risk_headers", return_value={}), \
                patch.object(protocol, "request_http", side_effect=replies) as request:
            result = protocol.extract({"access_token": "account-token", "check_only": True}, "http://proxy.test:8080", emit=lambda _: None)
        self.assertTrue(result.get("check_only"))
        self.assertEqual(result["payment_methods"], ["card", "upi"])
        self.assertEqual(request.call_count, 3)

    def test_check_mode_releases_quota_without_consuming_success(self):
        def extractor(account, *args, **kwargs):
            self.assertTrue(account["check_only"])
            return {"ok": True, "check_only": True, "due": 0, "payment_methods": ["upi"]}
        state = ServerState(cdk="test", max_uses=1, proxy="http://proxy.test:8080", extractor=extractor)
        try:
            job = state.submit(token="account-token", check_only=True)
            events = list(state.events(job, timeout=1))
            result = next(data["result"] for event, data in events if event == "result")
            self.assertTrue(result["check_only"])
            self.assertNotIn("long_url", result)
            self.assertEqual(state.quota.snapshot()["used"], 0)
            self.assertEqual(state.quota.snapshot()["reserved"], 0)
        finally:
            state.executor.shutdown(wait=True, cancel_futures=True)

    def test_server_publishes_error_detail_to_consumer(self):
        def extractor(*args, **kwargs):
            return {"ok": False, "error": "checkout_http_400", "error_detail": "Our systems have detected unusual activity."}
        state = ServerState(cdk="test", proxy="http://proxy.test:8080", extractor=extractor)
        try:
            job = state.submit(token="account-token")
            events = list(state.events(job, timeout=1))
            error = next(data["error"] for event, data in events if event == "error")
            self.assertIn("checkout_http_400", error)
            self.assertIn("unusual activity", error)
        finally:
            state.executor.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
