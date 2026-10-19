import json
import threading
from urllib.request import Request, urlopen

from upi_zero_link import server

server._Handler.log_message = lambda *args, **kwargs: None
state = server.ServerState(
    cdk="fixture-cdk",
    extractor=lambda account, proxy, *, tries, emit: {
        "ok": True,
        "url": "https://fixture.example/upi",
        "qr_png": "https://fixture.example/upi.png",
    },
)
httpd = server.make_server("127.0.0.1", 0, state)
thread = threading.Thread(target=httpd.serve_forever, daemon=True)
thread.start()
base = "http://127.0.0.1:%d" % httpd.server_address[1]
try:
    body = json.dumps({"token": "fixture-token", "link_type": "upi", "cdk": "fixture-cdk"}).encode()
    req = Request(base + "/api/extract", data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=5) as response:
        created = json.loads(response.read())
    with urlopen(base + "/api/jobs/%s/events?cdk=fixture-cdk" % created["job_id"], timeout=5) as response:
        stream = response.read().decode()
    print("http=202 job_id=yes sse_result=%s sse_done=%s payment_method=upi" % (
        "event: result" in stream,
        "event: done" in stream,
    ))
finally:
    httpd.shutdown()
    httpd.server_close()
    state.executor.shutdown(wait=True, cancel_futures=True)
