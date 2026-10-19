# -*- coding: utf-8 -*-
"""在 upi-zero-link 镜像里跑一次完整提链，可切换指纹策略。

用法（token 从 stdin 读）：python /probe.py <stable|random>
只输出阶段、结果状态与脱敏链接。
"""
import configparser
import json
import sys
import uuid

from upi_zero_link import identity, protocol

token = sys.stdin.readline().strip()
mode = sys.argv[1] if len(sys.argv) > 1 else "stable"

cfg = configparser.ConfigParser()
cfg.read("/etc/upi-zero-link/server.ini")
raw = cfg.get("server", "proxies", fallback="")
proxy = next((line.strip() for line in raw.splitlines() if line.strip()), "")
if not proxy:
    print(json.dumps({"error": "no_proxy"}))
    raise SystemExit(2)

if mode == "random":
    original = identity.fingerprint

    def patched(access_token, country="IN"):
        fp = dict(original(access_token, country))
        digest = uuid.uuid4().hex
        fp["device_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "probe-device|" + digest))
        fp["oai_session_id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, "probe-session|" + digest))
        return fp

    identity.fingerprint = patched

events = []
try:
    result = protocol.extract(
        {"email": "", "access_token": token},
        proxy,
        tries=1,
        emit=lambda message: events.append(str(message)[:110]),
    )
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"mode": mode, "exception": type(exc).__name__, "events": events[-10:]}, ensure_ascii=False))
    raise SystemExit(1)

url = str(result.get("url") or "")
print(json.dumps({
    "mode": mode,
    "ok": bool(result.get("ok")),
    "error": result.get("error"),
    "si": result.get("si"),
    "due": result.get("due"),
    "has_url": bool(url),
    "url_tail": url[-24:] if url else "",
    "events": events[-8:],
}, ensure_ascii=False))
