"""Client for an optional remote link-extraction service."""
from __future__ import annotations

from urllib.parse import urlparse

import requests

from .protocol import NetworkDown

SERVICE_TIMEOUT = 60


def normalize_service_url(value: str) -> str:
    """Validate and normalize the configured service endpoint."""
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("service URL 必须是完整的 http(s) URL")
    return url.rstrip("/")


def extract_via_service(
    account: dict,
    proxy: str,
    *,
    service_url: str,
    cdk: str = "",
    tries: int = 2,
    emit=print,
) -> dict:
    """Request one UPI link from a configured service endpoint.

    The local protocol remains the default. This client is used only when the
    CLI receives ``--service-url`` and sends the CDK as request credentials.
    """
    endpoint = normalize_service_url(service_url)
    account_cdk = str(account.get("cdk") or cdk or "").strip()
    payload = {
        "email": str(account.get("email") or ""),
        "access_token": str(account.get("access_token") or ""),
        "session_token": str(account.get("session_token") or ""),
        "proxy": proxy,
        "tries": tries,
        "cdk": account_cdk,
        "payment_method": "upi",
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Payment-Method": "upi",
    }
    if account_cdk:
        headers["Authorization"] = "Bearer " + account_cdk
        headers["X-CDK"] = account_cdk

    emit("stage service")
    try:
        response = requests.post(endpoint, json=payload, headers=headers,
                                 timeout=SERVICE_TIMEOUT)
    except requests.RequestException as exc:
        raise NetworkDown("service: %s" % str(exc)[:140]) from exc

    if response.status_code >= 400:
        return {"ok": False, "error": "service_http_%s" % response.status_code}
    try:
        body = response.json()
    except ValueError:
        return {"ok": False, "error": "service_invalid_json"}
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        body = body["data"]
    if not isinstance(body, dict):
        return {"ok": False, "error": "service_invalid_response"}
    if "ok" not in body:
        body = {"ok": bool(body.get("url")), **body}
    return body
