# -*- coding: utf-8 -*-
"""诊断：跑完整 UPI 建委托流程，打印 Stripe 返回的 decline 细节（不打印凭据）。

token 从 stdin 读。
"""
import json
import sys
import uuid

from upi_zero_link import identity, protocol
from upi_zero_link._vendor import oaics
from upi_zero_link._vendor.http import chatgpt_session, make_session, request as http_request
from upi_zero_link._vendor.risk import checkout_risk_headers
from upi_zero_link.addresses import next_address
from upi_zero_link.proxy import normalize_proxy
import configparser

token = sys.stdin.readline().strip()
cfg = configparser.ConfigParser()
cfg.read("/etc/upi-zero-link/server.ini")
raw = cfg.get("server", "proxies", fallback="")
proxy = normalize_proxy(next((line.strip() for line in raw.splitlines() if line.strip()), ""))
if not proxy:
    print(json.dumps({"error": "no_proxy"}))
    raise SystemExit(2)

out: dict = {"stage": "start"}
fp = identity.fingerprint(token, "IN")
device_id = fp["device_id"]
chatgpt = chatgpt_session(proxy, token, "", device_id=device_id, fingerprint=fp)
stripe = None
try:
    oaics.warmup_chatgpt_page(chatgpt, country="IN", device_id=device_id, timeout=20, fingerprint=fp)
    sentinel = checkout_risk_headers(
        chatgpt.get, proxy, device_id, fp["oai_session_id"], fp,
        protocol.PROMO_CAMPAIGN, "oai-did=" + device_id, 25, lambda *a: None,
    )
    headers = identity.chatgpt_headers(
        token, fp, referer="https://chatgpt.com/?promo_campaign=" + protocol.PROMO_CAMPAIGN,
        route="/backend-api/payments/checkout")
    headers.update(sentinel or {})
    out["sentinel"] = bool(headers.get("openai-sentinel-token"))
    out["stage"] = "checkout"
    r = http_request(chatgpt, "POST", protocol.CHECKOUT_URL, headers=headers, timeout=25, json={
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": "IN", "currency": "INR"},
        "promo_campaign": {"promo_campaign_id": protocol.PROMO_CAMPAIGN,
                           "is_coupon_from_query_param": False},
        "checkout_ui_mode": "custom",
    })
    out["checkout"] = int(r.status_code)
    if r.status_code >= 400:
        print(json.dumps(out))
        raise SystemExit(0)
    data = r.json() or {}
    cs = data.get("checkout_session_id")
    pk = data.get("publishable_key")
    processor = data.get("processor_entity")
    out["has_cs"] = bool(cs)
    out["has_pk"] = bool(pk)

    stripe = make_session(proxy, impersonate=fp["impersonate"], user_agent=fp["ua"],
                          accept_language=fp["accept_language"])
    sh = identity.stripe_headers(fp)
    stripe_js_id = str(uuid.uuid4())
    init = http_request(stripe, "POST", protocol.INIT_URL % cs, headers=sh, timeout=25, data={
        "browser_locale": "en-US",
        "browser_timezone": "Asia/Kolkata",
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
        "elements_session_client[stripe_js_id]": stripe_js_id,
        "elements_session_client[locale]": "en",
        "elements_session_client[is_aggregation_expected]": "false",
        "elements_options_client[saved_payment_method][enable_save]": "never",
        "elements_options_client[saved_payment_method][enable_redisplay]": "never",
        "key": pk,
        "_stripe_version": protocol.STRIPE_VERSION,
    })
    out["init"] = int(init.status_code)
    init_data = init.json() or {}
    address = next_address()
    tax = http_request(stripe, "POST", protocol.PAGE_URL % cs, headers=sh, timeout=25, data={
        "tax_region[country]": "IN",
        "tax_region[postal_code]": address["postal_code"],
        "tax_region[state]": address["state"],
        "tax_region[city]": address["city"],
        "tax_region[line1]": address["line1"],
        "key": pk,
        "_stripe_version": protocol.STRIPE_VERSION,
    })
    tax_data = tax.json() or {}
    out["tax"] = int(tax.status_code)
    out["due"] = (tax_data.get("total_summary") or {}).get("due")
    out["pmc"] = tax_data.get("payment_method_collection")
    out["methods"] = [str(m).lower() for m in (tax_data.get("payment_method_types") or [])][:6]

    pm = http_request(stripe, "POST", protocol.PAYMENT_METHOD_URL, headers=sh, timeout=25, data={
        "type": "upi",
        "key": pk,
        "billing_details[name]": address["name"],
        "billing_details[address][country]": "IN",
        "billing_details[address][line1]": address["line1"],
        "billing_details[address][city]": address["city"],
        "billing_details[address][state]": address["state"],
        "billing_details[address][postal_code]": address["postal_code"],
    })
    out["pm"] = int(pm.status_code)
    pm_data = pm.json() or {}
    pm_id = str(pm_data.get("id") or "")
    out["pm_type"] = str(pm_data.get("type") or "")
    if not pm_id:
        out["pm_error"] = str(pm_data.get("error") or "")[:160]
        print(json.dumps(out, ensure_ascii=False))
        raise SystemExit(0)

    base_url = str(init_data.get("stripe_hosted_url") or "").partition("#")[0]
    confirm_body = {
        "eid": "NA",
        "payment_method": pm_id,
        "expected_amount": "0",
        "tax_id_collection[purchasing_as_business]": "false",
        "expected_payment_method_type": "upi",
        "return_url": "%s?redirect_pm_type=upi&lid=%s&ui_mode=custom" % (base_url, uuid.uuid4()),
        "key": pk,
        "_stripe_version": protocol.STRIPE_VERSION,
        "version": "a34694b057",
        "client_attribution_metadata[client_session_id]": stripe_js_id,
        "client_attribution_metadata[checkout_session_id]": cs,
        "client_attribution_metadata[merchant_integration_source]": "checkout",
        "client_attribution_metadata[merchant_integration_version]": "custom_checkout",
        "client_attribution_metadata[payment_method_selection_flow]": "merchant_specified",
        "client_attribution_metadata[checkout_config_id]": str(tax_data.get("config_id") or uuid.uuid4()),
        "link_brand": "link",
    }
    exit_ip = ""
    for ip_url in ("https://api64.ipify.org?format=json", "https://chatgpt.com/cdn-cgi/trace"):
        try:
            ip_resp = http_request(chatgpt, "GET", ip_url, timeout=15)
            text = str(getattr(ip_resp, "text", "") or "")
            if "ip=" in text and not text.lstrip().startswith("{"):
                for line in text.splitlines():
                    if line.startswith("ip="):
                        exit_ip = line[3:].strip()
                        break
            else:
                exit_ip = str((ip_resp.json() or {}).get("ip") or "")
        except Exception:
            pass
        if exit_ip:
            break
    out["exit_ip_present"] = bool(exit_ip)
    if exit_ip:
        confirm_body["mandate_data[customer_acceptance][type]"] = "online"
        confirm_body["mandate_data[customer_acceptance][online][ip_address]"] = exit_ip
        confirm_body["mandate_data[customer_acceptance][online][user_agent]"] = fp["ua"]
    if init_data.get("init_checksum"):
        confirm_body["init_checksum"] = str(init_data["init_checksum"])
    out["had_init_checksum"] = bool(init_data.get("init_checksum"))
    confirm = http_request(stripe, "POST", protocol.CONFIRM_URL % cs, headers=sh, timeout=25, data=confirm_body)
    out["confirm"] = int(confirm.status_code)
    confirm_data = confirm.json() or {}
    c_err = confirm_data.get("error") or {}
    if isinstance(c_err, dict) and c_err:
        out["confirm_err_type"] = c_err.get("type")
        out["confirm_err_code"] = c_err.get("code")
        out["confirm_err_param"] = c_err.get("param")
        out["confirm_err_msg"] = str(c_err.get("message") or "")[:220]
    si = confirm_data.get("setup_intent") or {}
    out["confirm_si_status"] = si.get("status")
    out["confirm_error"] = (si.get("last_setup_error") or {}).get("code")

    ah = identity.chatgpt_headers(token, fp,
                                  referer="https://chatgpt.com/checkout/%s/%s" % (processor, cs),
                                  route="/backend-api/payments/checkout/approve")
    ah.update(sentinel or {})
    approve = http_request(chatgpt, "POST", protocol.APPROVE_URL, headers=ah, timeout=25,
                           json={"checkout_session_id": cs, "processor_entity": processor})
    out["approve"] = int(approve.status_code)
    try:
        out["approve_result"] = str((approve.json() or {}).get("result") or "")
    except Exception:
        out["approve_result"] = ""

    page = http_request(stripe, "GET", "%s?key=%s" % (protocol.PAGE_URL % cs, pk), headers=sh, timeout=25)
    page_data = page.json() or {}
    si2 = page_data.get("setup_intent") or {}
    err = si2.get("last_setup_error") or {}
    out["page"] = int(page.status_code)
    out["si_status"] = si2.get("status")
    out["err_type"] = err.get("type")
    out["err_code"] = err.get("code")
    out["err_decline"] = err.get("decline_code")
    out["err_message"] = str(err.get("message") or "")[:160]
    out["pm_attached"] = bool(si2.get("payment_method"))
    out["next_action_type"] = str((si2.get("next_action") or {}).get("type") or "")
finally:
    for session in (stripe, chatgpt):
        try:
            if session is not None:
                session.close()
        except Exception:
            pass

print(json.dumps(out, ensure_ascii=False))
