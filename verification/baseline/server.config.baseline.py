"""HTTP/SSE server compatible with turb-gpt-free-register link extraction.

The server keeps the existing UPI protocol as its execution engine and exposes:

* POST /api/extract
* GET  /api/jobs/<job_id>/events
* GET  /api/cdk

Only UPI is accepted. The implementation uses the standard library so the
server does not add a web-framework dependency to the protocol client.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, urlsplit


PAYMENT_METHOD = "upi"
DEFAULT_PROXY = os.environ.get("UPI_PROXY", "socks5h://127.0.0.1:1080")


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _default_extractor(account: dict, proxy: str, *, tries: int, emit) -> dict:
    """Load the network protocol lazily so /healthz works without its deps."""
    from .protocol import extract

    return extract(account, proxy, tries=tries, emit=emit)


def _result_payload(result: dict) -> dict:
    """Map the local result shape to the target service result shape."""
    long_url = str(result.get("url") or result.get("long_url") or "")
    qr_png = str(result.get("qr_png") or result.get("image_url_png") or "")
    return {
        "long_url": long_url,
        "copy_paste": str(result.get("copy_paste") or long_url),
        "image_url_png": qr_png,
        "image_url_svg": str(result.get("image_url_svg") or ""),
        "payment_method": PAYMENT_METHOD,
        "payment_link_type": PAYMENT_METHOD,
        "expires_at": result.get("expires_at") or "",
        "due": result.get("due"),
        "intent_state": str(result.get("intent_state") or "verified"),
    }


@dataclass
class _Job:
    job_id: str
    token: str
    email: str
    proxy: str
    tries: int
    created_at: float = field(default_factory=time.time)
    status: str = "pending"
    events: list[tuple[str, dict]] = field(default_factory=list)
    result: dict | None = None
    error: str = ""
    condition: threading.Condition = field(default_factory=threading.Condition)

    @property
    def terminal(self) -> bool:
        return self.status in {"succeeded", "failed"}

    def publish(self, event: str, data: dict) -> None:
        with self.condition:
            self.events.append((event, data))
            self.condition.notify_all()


class _Quota:
    """Single configured CDK with optional successful-use quota."""

    def __init__(self, cdk: str, max_uses: int = 0) -> None:
        self.cdk = cdk.strip()
        self.max_uses = max(0, max_uses)
        self.used = 0
        self.reserved = 0
        self.lock = threading.Lock()

    def authenticate(self, value: str) -> bool:
        return bool(self.cdk) and value.strip() == self.cdk

    def reserve(self) -> bool:
        with self.lock:
            if self.max_uses and self.used + self.reserved >= self.max_uses:
                return False
            self.reserved += 1
            return True

    def commit(self) -> None:
        with self.lock:
            self.reserved = max(0, self.reserved - 1)
            self.used += 1

    def release(self) -> None:
        with self.lock:
            self.reserved = max(0, self.reserved - 1)

    def snapshot(self) -> dict:
        with self.lock:
            remaining = None
            if self.max_uses:
                remaining = max(0, self.max_uses - self.used - self.reserved)
            return {
                "payment_method": PAYMENT_METHOD,
                "link_type": PAYMENT_METHOD,
                "used": self.used,
                "reserved": self.reserved,
                "limit": self.max_uses or None,
                "remaining": remaining,
                "unlimited": not bool(self.max_uses),
            }


class ServerState:
    """Thread-safe job manager shared by all HTTP handler instances."""

    def __init__(
        self,
        *,
        cdk: str,
        proxy: str = DEFAULT_PROXY,
        workers: int = 1,
        queue_limit: int = 500,
        max_uses: int = 0,
        extractor: Callable[..., dict] | None = None,
        job_ttl: int = 900,
    ) -> None:
        self.quota = _Quota(cdk, max_uses=max_uses)
        self.proxy = proxy.strip() or DEFAULT_PROXY
        self.queue_limit = max(1, queue_limit)
        self.job_ttl = max(60, job_ttl)
        self.extractor = extractor or _default_extractor
        self.executor = ThreadPoolExecutor(
            max_workers=max(1, min(4, workers)), thread_name_prefix="upi-link"
        )
        self.jobs: dict[str, _Job] = {}
        self.lock = threading.Lock()

    def _prune(self) -> None:
        cutoff = time.time() - self.job_ttl
        with self.lock:
            expired = [
                job_id for job_id, job in self.jobs.items()
                if job.terminal and job.created_at < cutoff
            ]
            for job_id in expired:
                self.jobs.pop(job_id, None)

    def get(self, job_id: str) -> _Job | None:
        self._prune()
        with self.lock:
            return self.jobs.get(job_id)

    def submit(
        self,
        *,
        token: str,
        email: str = "",
        proxy: str = "",
        tries: int = 2,
    ) -> _Job:
        if not token.strip():
            raise ValueError("token 不能为空")
        if tries not in (1, 2):
            raise ValueError("tries 只支持 1 或 2")
        self._prune()
        with self.lock:
            active = sum(not job.terminal for job in self.jobs.values())
            if active >= self.queue_limit:
                raise RuntimeError("提链队列已满")
            if not self.quota.reserve():
                raise RuntimeError("CDK 可用次数已耗尽")
            job = _Job(
                job_id=uuid.uuid4().hex,
                token=token.strip(),
                email=email.strip(),
                proxy=proxy.strip() or self.proxy,
                tries=tries,
            )
            self.jobs[job.job_id] = job
        job.publish("log", {"message": "queued", "payment_method": PAYMENT_METHOD})
        self.executor.submit(self._run, job)
        return job

    def _run(self, job: _Job) -> None:
        job.status = "running"
        job.publish("log", {"message": "started", "payment_method": PAYMENT_METHOD})

        def emit(message: str) -> None:
            job.publish("log", {"message": str(message)[:500]})

        try:
            result = self.extractor(
                {"email": job.email, "access_token": job.token},
                job.proxy,
                tries=job.tries,
                emit=emit,
            )
            if isinstance(result, dict) and result.get("ok"):
                job.result = _result_payload(result)
                self.quota.commit()
                job.publish("result", {"result": job.result})
                job.status = "succeeded"
            else:
                job.error = str((result or {}).get("error") or "提链失败")
                self.quota.release()
                job.publish("error", {"error": job.error, "message": job.error})
                job.status = "failed"
        except Exception as exc:  # noqa: BLE001
            job.error = "%s: %s" % (type(exc).__name__, str(exc)[:400])
            self.quota.release()
            job.publish("error", {"error": job.error, "message": job.error})
            job.status = "failed"
        finally:
            job.publish("done", {"status": job.status, "job_id": job.job_id})

    def events(self, job: _Job, timeout: float = 15.0) -> Iterator[tuple[str, dict]]:
        index = 0
        while True:
            with job.condition:
                while index >= len(job.events) and not job.terminal:
                    job.condition.wait(timeout=timeout)
                    if index >= len(job.events) and not job.terminal:
                        yield "", {}
                while index < len(job.events):
                    item = job.events[index]
                    index += 1
                    yield item
                if job.terminal:
                    return


class _Handler(BaseHTTPRequestHandler):
    state: ServerState
    server_version = "upi-zero-link/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        path = urlsplit(self.path).path
        print("%s %s" % (self.log_date_time_string(), format % ((path,) + args[1:])), flush=True)

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._send_json({"ok": False, "error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1024 * 1024:
            raise ValueError("请求体为空或超过 1 MiB")
        raw = self.rfile.read(length)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _request_cdk(self, data: dict | None = None, query: dict | None = None) -> str:
        data = data or {}
        query = query or {}
        header = str(self.headers.get("X-CDK") or "").strip()
        auth = str(self.headers.get("Authorization") or "").strip()
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        return str(data.get("cdk") or header or bearer or (query.get("cdk") or query.get("code") or [""])[0]).strip()

    def _authorize(self, data: dict | None = None, query: dict | None = None) -> bool:
        return self.state.quota.authenticate(self._request_cdk(data, query))

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-CDK")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/healthz":
            self._send_json({"ok": True, "service": "upi-zero-link", "payment_method": PAYMENT_METHOD})
            return
        if parsed.path in ("/api/cdk", "/api/extract-link/cdk"):
            if not self._authorize(query=query):
                self._error("CDK 无效", HTTPStatus.UNAUTHORIZED)
                return
            self._send_json({"ok": True, **self.state.quota.snapshot()})
            return
        prefix = "/api/jobs/"
        suffix = "/events"
        if parsed.path.startswith(prefix) and parsed.path.endswith(suffix):
            job_id = parsed.path[len(prefix):-len(suffix)].strip("/")
            job = self.state.get(job_id)
            if not job:
                self._error("任务不存在", HTTPStatus.NOT_FOUND)
                return
            if not self._authorize(query=query):
                self._error("CDK 无效", HTTPStatus.UNAUTHORIZED)
                return
            self._stream_events(job)
            return
        self._error("Not Found", HTTPStatus.NOT_FOUND)

    def _stream_events(self, job: _Job) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            for event, data in self.state.events(job):
                if not event:
                    self.wfile.write(b": keep-alive\n\n")
                else:
                    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(("event: %s\ndata: %s\n\n" % (event, body)).encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            self.close_connection = True

    def do_POST(self) -> None:
        if urlsplit(self.path).path not in ("/api/extract", "/api/extract-link"):
            self._error("Not Found", HTTPStatus.NOT_FOUND)
            return
        try:
            data = self._body()
        except (ValueError, json.JSONDecodeError) as exc:
            self._error(str(exc))
            return
        if not self._authorize(data=data):
            self._error("CDK 无效", HTTPStatus.UNAUTHORIZED)
            return
        link_type = str(data.get("link_type") or data.get("payment_method") or PAYMENT_METHOD).strip().lower()
        if link_type != PAYMENT_METHOD:
            self._error("当前服务只支持 upi")
            return
        token = str(data.get("token") or data.get("access_token") or "").strip()
        try:
            tries = int(data.get("tries", 2))
            job = self.state.submit(
                token=token,
                email=str(data.get("email") or ""),
                proxy=str(data.get("proxy") or ""),
                tries=tries,
            )
        except ValueError as exc:
            self._error(str(exc))
            return
        except RuntimeError as exc:
            status = HTTPStatus.TOO_MANY_REQUESTS if "队列" in str(exc) or "次数" in str(exc) else HTTPStatus.BAD_REQUEST
            self._error(str(exc), status)
            return
        snapshot = self.state.quota.snapshot()
        self._send_json({
            "ok": True,
            "accepted": True,
            "job_id": job.job_id,
            "link_type": PAYMENT_METHOD,
            "payment_method": PAYMENT_METHOD,
            "cdk_remaining": snapshot["remaining"],
        }, HTTPStatus.ACCEPTED)


def make_server(host: str, port: int, state: ServerState) -> ThreadingHTTPServer:
    handler = type("UPIHandler", (_Handler,), {"state": state})
    return ThreadingHTTPServer((host, port), handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UPI 提链兼容服务端（JSON + SSE）")
    parser.add_argument("--host", default=os.environ.get("UPI_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=_int_env("UPI_SERVER_PORT", 8080, 1, 65535))
    parser.add_argument("--cdk", default=os.environ.get("UPI_SERVER_CDK") or os.environ.get("UPI_CDK", ""),
                        help="服务端 CDK；也可用 UPI_SERVER_CDK")
    parser.add_argument("--proxy", default=os.environ.get("UPI_SERVER_PROXY") or DEFAULT_PROXY,
                        help="服务端默认印度出口代理")
    parser.add_argument("--workers", type=int, choices=(1, 2, 3, 4),
                        default=_int_env("UPI_SERVER_WORKERS", 1, 1, 4),
                        help="后台并发数，范围 1-4")
    parser.add_argument("--queue-limit", type=int,
                        default=_int_env("UPI_SERVER_QUEUE_LIMIT", 500, 1, 5000))
    parser.add_argument("--max-uses", type=int,
                        default=_int_env("UPI_SERVER_CDK_MAX_USES", 0, 0, 1000000),
                        help="CDK 成功次数上限；0 表示不限")
    parser.add_argument("--job-ttl", type=int,
                        default=_int_env("UPI_SERVER_JOB_TTL", 900, 60, 86400))
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not str(args.cdk).strip():
        raise SystemExit("必须通过 --cdk 或 UPI_SERVER_CDK 配置服务端 CDK")
    state = ServerState(
        cdk=args.cdk,
        proxy=args.proxy,
        workers=args.workers,
        queue_limit=args.queue_limit,
        max_uses=args.max_uses,
        job_ttl=args.job_ttl,
    )
    server = make_server(args.host, args.port, state)
    print("UPI service listening on http://%s:%d (workers=%d)" % (args.host, args.port, args.workers), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
        state.executor.shutdown(wait=True, cancel_futures=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
