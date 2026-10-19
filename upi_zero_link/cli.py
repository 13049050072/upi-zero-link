# -*- coding: utf-8 -*-
"""命令行入口：`upi-zero-link` 或 `python -m upi_zero_link`。"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from .protocol import NetworkDown, extract
from .proxy import normalize_proxy
from .service import extract_via_service, normalize_service_url

DEFAULT_PROXY = os.environ.get("UPI_PROXY") or "socks5h://127.0.0.1:1080"
DEFAULT_SERVICE_URL = os.environ.get("UPI_SERVICE_URL", "").strip()
DEFAULT_CDK = os.environ.get("UPI_CDK", "").strip()


def log(message: str) -> None:
    print("%s %s" % (time.strftime("%H:%M:%S"), message), flush=True)


def load_accounts(args) -> list[dict]:
    if args.accounts:
        data = json.loads(Path(args.accounts).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("accounts") if isinstance(data.get("accounts"), list) else [data]
        if not isinstance(data, list):
            raise SystemExit("账号文件必须是 JSON 数组，或带 accounts 数组的对象")
        accounts = []
        for item in data:
            if not isinstance(item, dict):
                raise SystemExit("账号文件每一项都要是对象")
            accounts.append({
                "email": str(item.get("email") or ""),
                "access_token": str(item.get("access_token") or item.get("token") or ""),
                "session_token": str(item.get("session_token") or ""),
                "cdk": str(item.get("cdk") or ""),
            })
        return accounts
    if not args.token:
        raise SystemExit("要么给 --token，要么给 --accounts 文件")
    return [{"email": args.email or "", "access_token": args.token,
             "session_token": "", "cdk": args.cdk or ""}]


def make_proxies(args) -> list[str]:
    proxies = args.proxy or [DEFAULT_PROXY]
    return [normalize_proxy(p) for p in proxies if p]


def notify(record: dict) -> None:
    """可选提醒：Windows 上响铃 + 托盘气泡，其他平台退回终端响铃。"""
    text = record.get("url") or ""
    if sys.platform.startswith("win"):
        import subprocess
        script = (
            "$ErrorActionPreference='SilentlyContinue';"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "1..4|ForEach-Object{[console]::beep(1200,250);[console]::beep(800,250)};"
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Warning;"
            "$n.BalloonTipTitle='UPI link ready';"
            "$n.BalloonTipText='%s';"
            "$n.Visible=$true;$n.ShowBalloonTip(120000);Start-Sleep -Seconds 15"
        ) % text.replace("'", "")
        try:
            subprocess.Popen(["powershell", "-NoProfile", "-Command", script],
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return
        except OSError:
            pass
    for _ in range(4):
        print("\a", end="", flush=True)
        time.sleep(0.3)


def save_links(path: Path, record: dict) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            data = []
    except (OSError, ValueError):
        data = []
    data.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def run_account(account: dict, proxies: list[str], *, rounds: int, tries: int,
                quiet: bool, service_url: str = "", cdk: str = "") -> dict | None:
    """按出口轮换反复试同一个号；出核验通过的链就返回。"""
    extractor = extract_via_service if service_url else extract
    for index in range(max(1, rounds)):
        proxy = proxies[index % len(proxies)]
        label = "第 %d/%d 轮（出口 %s）" % (index + 1, max(1, rounds), _label(proxy))
        log("%s %s" % (account.get("email") or "?", label))
        try:
            if service_url:
                result = extractor(account, proxy, tries=tries,
                                   service_url=service_url, cdk=cdk,
                                   emit=(lambda *a: None) if quiet else log)
            else:
                result = extractor(account, proxy, tries=tries,
                                   emit=(lambda *a: None) if quiet else log)
        except NetworkDown as exc:
            log("  出口断了，换下一条：%s" % str(exc)[:140])
            continue
        except ValueError as exc:
            raise SystemExit(str(exc))
        if result.get("ok"):
            result["exit"] = _label(proxy)
            return result
        log("  未出链：%s" % result.get("error"))
        if result.get("error") in ("declined", "nonzero_due", "upi_unavailable", "link_unverified"):
            # 该号已经跑到终点，再打只是白烧资格
            break
    return None


def run_accounts(accounts: list[dict], proxies: list[str], *, rounds: int, tries: int,
                 quiet: bool, concurrency: int, service_url: str = "",
                 cdk: str = "", stop_after_success: bool = False) -> list[tuple[dict, dict | None]]:
    """Run a bounded batch and return completed results in input order."""
    if concurrency not in (1, 2, 3, 4):
        raise ValueError("concurrency 只支持 1 到 4")

    def worker(account: dict) -> dict | None:
        return run_account(account, proxies, rounds=rounds, tries=tries,
                           quiet=quiet, service_url=service_url, cdk=cdk)

    if not stop_after_success:
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(worker, accounts))
        return list(zip(accounts, results))

    if concurrency == 1:
        completed = []
        for account in accounts:
            result = worker(account)
            completed.append((account, result))
            if result:
                break
        return completed

    results = {}
    futures = {}
    next_index = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        while next_index < len(accounts) and len(futures) < concurrency:
            futures[pool.submit(worker, accounts[next_index])] = next_index
            next_index += 1

        while futures:
            done, _ = concurrent.futures.wait(
                futures, return_when=concurrent.futures.FIRST_COMPLETED)
            stop = False
            for future in done:
                index = futures.pop(future)
                result = future.result()
                results[index] = result
                stop = stop or bool(result)
            if stop:
                for future in futures:
                    future.cancel()
                break
            while next_index < len(accounts) and len(futures) < concurrency:
                futures[pool.submit(worker, accounts[next_index])] = next_index
                next_index += 1

    return [(accounts[index], results[index]) for index in sorted(results)]


def _label(proxy: str) -> str:
    normalized = normalize_proxy(proxy)
    parsed = urlsplit(normalized)
    if parsed.hostname:
        try:
            port = ":%d" % parsed.port if parsed.port else ""
        except ValueError:
            port = ""
        return parsed.hostname + port
    parts = normalized.split(":", 3)
    if len(parts) == 4:
        return "%s:%s" % (parts[0], parts[1])
    return normalized.rsplit("@", 1)[-1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="upi-zero-link",
        description="印度 ₹0 UPI Autopay 提链（协议层，无浏览器）")
    parser.add_argument("--token", help="ChatGPT access_token")
    parser.add_argument("--email", default="", help="用于 Stripe billing_details 的邮箱")
    parser.add_argument("--accounts", help="账号 JSON 文件：[{\"email\":..,\"access_token\":..}, ...]")
    parser.add_argument("--proxy", action="append", default=None,
                        help="印度出口，可重复；默认取环境变量 UPI_PROXY 或 socks5h://127.0.0.1:1080")
    parser.add_argument("--service-url", "--api-url", dest="service_url",
                        default=DEFAULT_SERVICE_URL,
                        help="提链服务 API 地址；设置后使用服务模式")
    parser.add_argument("--cdk", default=DEFAULT_CDK,
                        help="提链服务 CDK；也可放在账号 JSON 的 cdk 字段")
    parser.add_argument("--rounds", type=int, default=4, help="每个号最多换几条出口（默认 4）")
    parser.add_argument("--tries", type=int, default=2, choices=(1, 2),
                        help="同一会话里 confirm 重试次数（默认 2）")
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 3, 4), default=1,
                        help="批量账号并发数，范围 1-4（默认 1）")
    parser.add_argument("--all", action="store_true", help="出链后继续跑完剩下的账号")
    parser.add_argument("--quiet", action="store_true", help="只打结论，不打过程")
    parser.add_argument("--notify", action="store_true", help="出链时响铃 / 弹窗")
    parser.add_argument("--out", default="links.json", help="结果落盘路径（默认 links.json）")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        service_url = normalize_service_url(args.service_url) if args.service_url else ""
    except ValueError as exc:
        parser.error(str(exc))

    accounts = load_accounts(args)
    proxies = make_proxies(args)
    out_path = Path(args.out)
    log("账号 %d 个，出口 %s，每号最多 %d 轮，并发 %d%s" % (
        len(accounts), ", ".join(_label(p) for p in proxies), max(1, args.rounds),
        args.concurrency, "，服务模式" if service_url else "，本地模式"))

    results = run_accounts(accounts, proxies, rounds=args.rounds, tries=args.tries,
                           quiet=args.quiet, concurrency=args.concurrency,
                           service_url=service_url, cdk=args.cdk,
                           stop_after_success=not args.all)
    hits = []
    for account, result in results:
        if not result:
            log("%s 没出核验通过的链" % (account.get("email") or "?"))
            continue
        expires = int(result.get("expires_at") or 0)
        left = max(0, expires - int(time.time())) if expires else 0
        record = {
            "email": result.get("email") or account.get("email") or "",
            "url": result.get("url") or "",
            "qr_png": result.get("qr_png") or "",
            "expires_at": result.get("expires_at") or "",
            "seconds_left": left,
            "due": result.get("due"),
            "exit": result.get("exit") or "",
            "issued_at": result.get("at") or int(time.time()),
            "intent_state": "verified",
        }
        save_links(out_path, record)
        if args.notify:
            notify(record)
        hits.append(record)
        print()
        print("###### ★ ₹0 UPI link (verified) ######")
        print("account : %s" % record["email"])
        print("url     : %s" % record["url"])
        print("qr      : %s" % (record["qr_png"] or "-"))
        print("due     : %s   exit: %s" % (record["due"], record["exit"]))
        if expires:
            print("expires : %s (%d seconds left)" % (
                time.strftime("%H:%M:%S", time.localtime(expires)), left))
        print("saved   : %s" % out_path)
        print("######################################")
        print()
        if not args.all:
            return 0

    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())
