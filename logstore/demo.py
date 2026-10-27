"""Synthetic people and end-to-end application traces for the local demo."""

import time

from werkzeug.security import generate_password_hash


DEMO_USERS = (
    {
        "username": "lan.nguyen",
        "full_name": "Nguyễn Thị Lan",
        "email": "lan.nguyen@example.test",
        "department": "SRE · Vận hành",
        "role": "operator",
    },
    {
        "username": "an.le",
        "full_name": "Lê Hoàng An",
        "email": "an.le@example.test",
        "department": "Hỗ trợ ứng dụng",
        "role": "operator",
    },
    {
        "username": "minh.tran",
        "full_name": "Trần Minh Quân",
        "email": "quan.tran@example.test",
        "department": "Giám sát hệ thống",
        "role": "viewer",
    },
    {
        "username": "thao.pham",
        "full_name": "Phạm Thu Thảo",
        "email": "thao.pham@example.test",
        "department": "Kiểm toán nội bộ",
        "role": "viewer",
    },
)

SEED_MARKER_TRACE = "DEMO-ORDER-001"
DEMO_EVENT_COUNT = 32


def _event(level, source, message, trace_id, ts_ms, **fields):
    return (
        level,
        source,
        message,
        {"trace_id": trace_id, "env": "demo", **fields},
        ts_ms,
    )


def build_demo_events(now_ms=None):
    """Build 32 synthetic events in five correlated, time-ordered workflows."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    events = []

    def add_workflow(trace_id, age_hours, rows):
        start_ms = now_ms - age_hours * 3_600_000
        for offset_seconds, level, source, message, fields in rows:
            events.append(_event(
                level, source, message, trace_id,
                start_ms + offset_seconds * 1000, **fields,
            ))

    add_workflow("DEMO-ORDER-001", 2, [
        (0, "INFO", "auth-service", "Đăng nhập thành công customer_id=CUS-1042", {"customer_id": "CUS-1042", "ip": "192.0.2.24"}),
        (90, "INFO", "api-gateway", "POST /api/orders 201 in 42ms", {"http_status": 201, "latency_ms": 42}),
        (130, "INFO", "order-service", "Tạo đơn hàng ORD-2026-10482", {"order_id": "ORD-2026-10482", "customer_id": "CUS-1042"}),
        (190, "INFO", "inventory-service", "Giữ 2 sản phẩm SKU-LAPTOP-15", {"order_id": "ORD-2026-10482", "sku": "SKU-LAPTOP-15", "quantity": 2}),
        (280, "INFO", "payment-service", "Thanh toán ORD-2026-10482 được chấp thuận", {"order_id": "ORD-2026-10482", "amount_vnd": 24_990_000, "currency": "VND"}),
        (360, "INFO", "order-service", "Chuyển ORD-2026-10482 sang trạng thái confirmed", {"order_id": "ORD-2026-10482", "status": "confirmed"}),
        (510, "INFO", "notification-service", "Gửi email xác nhận đơn ORD-2026-10482", {"order_id": "ORD-2026-10482", "channel": "email", "delivery": "sent"}),
    ])

    add_workflow("DEMO-ORDER-002", 6, [
        (0, "INFO", "api-gateway", "POST /api/orders 201 in 53ms", {"http_status": 201, "latency_ms": 53, "customer_id": "CUS-2088"}),
        (45, "INFO", "order-service", "Tạo đơn hàng ORD-2026-10397", {"order_id": "ORD-2026-10397", "customer_id": "CUS-2088"}),
        (95, "INFO", "inventory-service", "Giữ 1 sản phẩm SKU-HEADPHONE-02", {"order_id": "ORD-2026-10397", "sku": "SKU-HEADPHONE-02", "quantity": 1}),
        (160, "WARN", "payment-service", "Cổng thanh toán phản hồi chậm sau 2400ms; thử lại lần 1", {"order_id": "ORD-2026-10397", "attempt": 1, "latency_ms": 2400}),
        (241, "INFO", "payment-service", "Thử lại thanh toán ORD-2026-10397 thành công", {"order_id": "ORD-2026-10397", "attempt": 2, "latency_ms": 318}),
        (290, "INFO", "order-service", "Chuyển ORD-2026-10397 sang trạng thái confirmed", {"order_id": "ORD-2026-10397", "status": "confirmed"}),
        (410, "INFO", "notification-service", "Gửi email xác nhận đơn ORD-2026-10397", {"order_id": "ORD-2026-10397", "channel": "email", "delivery": "sent"}),
    ])

    add_workflow("DEMO-ORDER-003", 11, [
        (0, "INFO", "api-gateway", "POST /api/orders 201 in 61ms", {"http_status": 201, "latency_ms": 61, "customer_id": "CUS-3315"}),
        (40, "INFO", "order-service", "Tạo đơn hàng ORD-2026-10261", {"order_id": "ORD-2026-10261", "customer_id": "CUS-3315"}),
        (85, "INFO", "inventory-service", "Giữ 3 sản phẩm SKU-CAMERA-08", {"order_id": "ORD-2026-10261", "sku": "SKU-CAMERA-08", "quantity": 3}),
        (145, "ERROR", "payment-service", "Thanh toán ORD-2026-10261 bị từ chối bởi ngân hàng phát hành", {"order_id": "ORD-2026-10261", "reason_code": "DO_NOT_HONOR"}),
        (195, "WARN", "order-service", "Hủy đơn ORD-2026-10261 sau khi thanh toán thất bại", {"order_id": "ORD-2026-10261", "status": "cancelled"}),
        (240, "INFO", "inventory-service", "Hoàn lại 3 sản phẩm SKU-CAMERA-08 vào kho", {"order_id": "ORD-2026-10261", "sku": "SKU-CAMERA-08", "quantity": 3}),
        (330, "INFO", "notification-service", "Gửi thông báo thanh toán thất bại cho CUS-3315", {"order_id": "ORD-2026-10261", "channel": "email", "delivery": "sent"}),
    ])

    add_workflow("DEMO-STAFF-004", 27, [
        (0, "INFO", "admin-portal", "Nhân viên lan.nguyen đăng nhập từ mạng văn phòng", {"actor": "lan.nguyen", "ip": "198.51.100.17", "mfa": True}),
        (55, "INFO", "audit-service", "lan.nguyen lọc nhật ký mức ERROR trong 24 giờ", {"actor": "lan.nguyen", "action": "event.query", "filter_level": "ERROR"}),
        (120, "INFO", "audit-service", "lan.nguyen mở trace DEMO-ORDER-003", {"actor": "lan.nguyen", "action": "trace.inspect", "target_trace": "DEMO-ORDER-003"}),
        (200, "WARN", "payment-service", "Tỷ lệ từ chối thanh toán tăng nhẹ trong 15 phút", {"metric": "payment.decline_rate", "window_minutes": 15}),
        (265, "INFO", "admin-portal", "lan.nguyen ghi chú ticket INC-2026-0081", {"actor": "lan.nguyen", "action": "incident.annotate", "ticket": "INC-2026-0081"}),
        (340, "INFO", "audit-service", "Kết thúc phiên quản trị của lan.nguyen", {"actor": "lan.nguyen", "action": "session.logout"}),
    ])

    add_workflow("DEMO-OPS-005", 49, [
        (0, "INFO", "admin-portal", "Nhân viên an.le yêu cầu tạo checkpoint trước bảo trì", {"actor": "an.le", "action": "backup.create", "ticket": "CHG-2026-0142"}),
        (75, "INFO", "backup-service", "Bắt đầu checkpoint RocksDB", {"actor": "an.le", "database": "logstore-demo"}),
        (210, "INFO", "backup-service", "Checkpoint hoàn tất; 6 column family đã đồng bộ", {"actor": "an.le", "database": "logstore-demo", "duration_ms": 135}),
        (285, "INFO", "backup-service", "Bản sao lưu backup-20261025-091500 đã được xác thực", {"actor": "an.le", "backup_id": "backup-20261025-091500", "status": "verified"}),
        (360, "INFO", "audit-service", "an.le đóng yêu cầu thay đổi CHG-2026-0142", {"actor": "an.le", "action": "change.close", "ticket": "CHG-2026-0142"}),
    ])

    return sorted(events, key=lambda event: event[4])


def _repair_clamped_demo_events(store, expected_events):
    expected = {
        (fields["trace_id"], message): (level, source, fields)
        for level, source, message, fields, _ in expected_events
    }
    if len(expected) != DEMO_EVENT_COUNT:
        return 0

    existing = []
    for trace_id in {key[0] for key in expected}:
        existing.extend(store.query(trace_id=trace_id, limit=DEMO_EVENT_COUNT + 1))
    if not existing or len(existing) > DEMO_EVENT_COUNT or len({event["ts"] for event in existing}) != 1:
        return 0

    existing_keys = set()
    for event in existing:
        signature = (event["fields"].get("trace_id"), event["msg"])
        if (signature in existing_keys
                or expected.get(signature) != (event["level"], event["source"], event["fields"])):
            return 0
        existing_keys.add(signature)

    for event in existing:
        store.delete_event(bytes.fromhex(event["id"]))
    replacements = [
        event for event in expected_events
        if (event[3]["trace_id"], event[2]) in existing_keys
    ]
    return len(store.put_many(replacements))


def seed_demo(store, password):
    """Add synthetic staff and linked event workflows without overwriting data."""
    if not isinstance(password, str) or len(password) < 10:
        raise ValueError("mat khau tai khoan demo phai co it nhat 10 ky tu")
    if not any(user.get("role") == "admin" and user.get("enabled", True)
               for user in store.list_users()):
        raise ValueError("hay tao admin dau tien truoc khi nap du lieu demo")

    created_users = 0
    for profile in DEMO_USERS:
        if store.get_user(profile["username"]):
            continue
        store.put_user(profile["username"], {
            **profile,
            "password_hash": generate_password_hash(password),
            "enabled": True,
            "is_demo": True,
        })
        created_users += 1

    demo_events = build_demo_events()
    if store.query(trace_id=SEED_MARKER_TRACE, limit=1):
        events_written = _repair_clamped_demo_events(store, demo_events)
    else:
        events_written = len(store.put_many(demo_events))
    return {"users_created": created_users, "events_written": events_written}
