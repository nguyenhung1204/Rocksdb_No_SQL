"""Send a small batch of realistic application events to the local LogStore."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    token = os.environ.get("LOGSTORE_INGEST_TOKEN")
    if not token:
        raise SystemExit("Hay dat LOGSTORE_INGEST_TOKEN trung voi app.py truoc khi chay.")

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
            print(response.read().decode("utf-8"))
    except HTTPError as error:
        print(error.read().decode("utf-8"))
        raise SystemExit("Ingest bi tu choi (HTTP %d)." % error.code)
    except URLError as error:
        raise SystemExit("Khong ket noi duoc toi LogStore: %s" % error.reason)


if __name__ == "__main__":
    main()
