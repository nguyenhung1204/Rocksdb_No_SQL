"""Send a small batch of realistic application events to the local LogStore."""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    token = os.environ.get("LOGSTORE_INGEST_TOKEN")
    if not token:
        raise SystemExit("Hãy đặt LOGSTORE_INGEST_TOKEN trùng với app.py trước khi chạy.")

    events = [
        {
            "level": "INFO",
            "source_event_id": "ORDER-2026-1042-checkout-started",
            "source": "checkout-service",
            "msg": "Order checkout started",
            "fields": {"trace_id": "ORDER-2026-1042", "order_id": "ORD-1042", "user_id": "USR-018"},
        },
        {
            "level": "WARN",
            "source_event_id": "ORDER-2026-1042-payment-attempt-1",
            "source": "payment-service",
            "msg": "Payment provider timed out; retry scheduled",
            "fields": {"trace_id": "ORDER-2026-1042", "order_id": "ORD-1042", "attempt": 1},
        },
        {
            "level": "INFO",
            "source_event_id": "ORDER-2026-1042-payment-attempt-2",
            "source": "payment-service",
            "msg": "Payment completed",
            "fields": {"trace_id": "ORDER-2026-1042", "order_id": "ORD-1042", "attempt": 2},
        },
        {
            "level": "INFO",
            "source_event_id": "ORDER-2026-1042-order-confirmed",
            "source": "order-service",
            "msg": "Order confirmed",
            "fields": {"trace_id": "ORDER-2026-1042", "order_id": "ORD-1042", "status": "confirmed"},
        },
    ]
    request = Request(
        os.environ.get("LOGSTORE_URL", "http://127.0.0.1:5000").rstrip("/") + "/api/ingest",
        data=json.dumps(events).encode("utf-8"),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
        print("Gửi %d sự kiện của trace ORDER-2026-1042 tới LogStore:" % len(events))
        print("  - Ghi mới : %d" % result["written"])
        print("  - Bị trùng: %d (đã gửi trước đó, LogStore trả lại đúng ID cũ, không ghi thêm)"
              % result["duplicates"])
        for event, event_id in zip(events, result["ids"]):
            print("    %-36s -> %s" % (event["source_event_id"], event_id))
    except HTTPError as error:
        print(error.read().decode("utf-8"))
        raise SystemExit("Ingest bị từ chối (HTTP %d)." % error.code)
    except URLError as error:
        raise SystemExit("Không kết nối được tới LogStore: %s" % error.reason)


if __name__ == "__main__":
    main()
