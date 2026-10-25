"""Offline regressions for state after a confirmation attempt."""
import unittest
from unittest.mock import Mock, patch

from upi_zero_link import cli, protocol


def response(body):
    value = Mock(status_code=200)
    value.json.return_value = body
    return value


def initial_responses():
    return [
        response({"checkout_session_id": "cs_test", "publishable_key": "pk_test", "processor_entity": "test"}),
        response({"total_summary": {"due": 0}, "payment_method_types": ["upi"], "stripe_hosted_url": "https://checkout.test"}),
        response({"total_summary": {"due": 0}, "payment_method_types": ["upi"]}),
        response({"id": "pm_test"}),
    ]


class ConfirmationStateTests(unittest.TestCase):
    def run_protocol(self, replies):
        with patch.object(protocol, "chatgpt_session"), \
                patch.object(protocol, "make_session"), \
                patch.object(protocol.oaics, "warmup_chatgpt_page"), \
                patch.object(protocol, "checkout_risk_headers", return_value={}), \
                patch.object(protocol.time, "sleep"), \
                patch.object(protocol, "request_http", side_effect=replies):
            return protocol.extract({"access_token": "account-token"}, "http://proxy.test:8080", tries=1, emit=lambda _: None)

    def test_latest_valid_state_replaces_earlier_decline(self):
        declined = {"status": "requires_payment_method", "last_setup_error": {"decline_code": "generic_decline"}}
        pages = [response({"setup_intent": declined})] + [
            response({"setup_intent": {"status": "requires_action"}})
            for _ in range(protocol.INSTRUCTIONS_ATTEMPTS - 1)
        ]
        result = self.run_protocol(initial_responses() + [response({}), response({"result": "approved"})] + pages)
        self.assertEqual(result["si"], "requires_action/-")
        self.assertEqual(result["error"], "no_link")
        self.assertEqual(result["error_detail"], "")
        self.assertTrue(result.get("confirm_attempted"))

    def test_requires_action_still_delivers_verified_link_and_qr(self):
        intent = {"status": "requires_action", "next_action": {
            "hosted_instructions_url": "https://payments.test/upi",
            "qr_code": {"image_url_png": "https://qr.test/upi.png", "expires_at": 1234567890}}}
        replies = initial_responses() + [response({}), response({"result": "approved"}), response({"setup_intent": intent})]
        with patch.object(protocol.verify, "verify_url", return_value=(True, "state=requires_action fam=1.00")):
            result = self.run_protocol(replies)
        self.assertTrue(result["ok"])
        self.assertEqual(result["url"], "https://payments.test/upi")
        self.assertEqual(result["qr_png"], "https://qr.test/upi.png")

    def test_non_decline_setup_error_is_not_labeled_declined(self):
        failed = {"status": "requires_payment_method", "last_setup_error": {
            "type": "invalid_request_error", "code": "parameter_invalid_empty",
            "param": "payment_method", "message": "Missing required parameter"}}
        pages = [response({"setup_intent": failed}) for _ in range(protocol.INSTRUCTIONS_ATTEMPTS)]
        result = self.run_protocol(initial_responses() + [response({}), response({"result": "approved"})] + pages)
        self.assertEqual(result["error"], "setup_failed")
        self.assertIn("parameter_invalid_empty", result["error_detail"])
        self.assertTrue(result["confirm_attempted"])

    def test_confirm_timeout_does_not_restart_cli_extraction(self):
        replies = initial_responses() + [TimeoutError("confirm response lost")]
        with patch.object(protocol, "chatgpt_session"), \
                patch.object(protocol, "make_session"), \
                patch.object(protocol.oaics, "warmup_chatgpt_page"), \
                patch.object(protocol, "checkout_risk_headers", return_value={}), \
                patch.object(protocol.time, "sleep"), \
                patch.object(protocol, "request_http", side_effect=replies * 2) as request:
            result = cli.run_account({"access_token": "account-token"}, ["http://proxy.test:8080"], rounds=2, tries=1, quiet=True)
        self.assertIsNone(result)
        self.assertEqual(sum(call.args[2] == protocol.CHECKOUT_URL for call in request.call_args_list), 1)
        self.assertEqual(sum(call.args[2].endswith("/confirm") for call in request.call_args_list), 1)

    def test_postconfirm_result_does_not_restart_cli_extraction(self):
        with patch.object(cli, "extract", return_value={"ok": False, "error": "no_link", "confirm_attempted": True}) as extract:
            result = cli.run_account({"access_token": "account-token"}, ["http://proxy.test:8080"], rounds=2, tries=1, quiet=True)
        self.assertIsNone(result)
        self.assertEqual(extract.call_count, 1)

    def test_preconfirm_network_failure_still_recovers(self):
        with patch.object(cli, "extract", side_effect=[protocol.NetworkDown("checkout: timeout"), {"ok": True, "url": "https://payments.test/upi"}]) as extract:
            result = cli.run_account({"access_token": "account-token"}, ["http://proxy.test:8080"], rounds=2, tries=1, quiet=True)
        self.assertTrue(result["ok"])
        self.assertEqual(extract.call_count, 2)


if __name__ == "__main__":
    unittest.main()
