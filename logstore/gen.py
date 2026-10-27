"""gen.py - Sinh nhat ky gia lap giong he thong that (nhieu service, ty le muc khac nhau)."""

import random
import time

SERVICES = {
    # service: (trong so, danh sach (level, template))
    "api-gateway": (30, [
        ("INFO", "GET /api/products 200 in {ms}ms"),
        ("INFO", "POST /api/orders 201 in {ms}ms"),
        ("WARN", "Rate limit gan cham nguong cho ip {ip}"),
        ("ERROR", "Upstream order-service timeout sau {ms}ms"),
    ]),
    "auth-service": (20, [
        ("INFO", "Dang nhap thanh cong user_id={uid}"),
        ("WARN", "Dang nhap that bai user_id={uid} tu ip {ip}"),
        ("ERROR", "Khong xac thuc duoc JWT: token het han"),
    ]),
    "order-service": (25, [
        ("INFO", "Tao don hang order_id={oid} user_id={uid}"),
        ("DEBUG", "Tinh tong tien order_id={oid} mat {ms}ms"),
        ("ERROR", "Khong the giu hang order_id={oid}: ton kho khong du"),
    ]),
    "payment-service": (15, [
        ("INFO", "Thanh toan thanh cong order_id={oid}"),
        ("WARN", "Cong thanh toan phan hoi cham {ms}ms"),
        ("ERROR", "Thanh toan that bai order_id={oid}: bi tu choi"),
        ("FATAL", "Mat ket noi toi cong thanh toan"),
    ]),
    "db-proxy": (10, [
        ("DEBUG", "Query thuc thi trong {ms}ms"),
        ("WARN", "Slow query phat hien: {ms}ms"),
        ("ERROR", "Connection pool can kiet"),
    ]),
}

# ty le chon tung mau theo muc (de INFO/DEBUG nhieu, FATAL rat hiem)
LEVEL_WEIGHT = {"DEBUG": 25, "INFO": 64, "WARN": 8, "ERROR": 2, "FATAL": 0.1}


def generate(n, end_ms=None, span_hours=24, seed=42, jitter_ms=200):
    """
    Sinh n su kien trai deu trong span_hours gio cuoi (tinh den end_ms).
    jitter_ms: dao dong nho ve thoi gian de mo phong log den hoi lech thu tu.
    Tra ve list (level, source, msg, fields, ts_ms) da sap xep theo thoi gian ghi.
    """
    rnd = random.Random(seed)
    if end_ms is None:
        end_ms = int(time.time() * 1000)
    start_ms = end_ms - span_hours * 3600 * 1000
    names = list(SERVICES)
    weights = [SERVICES[s][0] for s in names]

    events = []
    for i in range(n):
        ts = start_ms + int((end_ms - start_ms) * i / max(n, 1))
        ts += rnd.randint(-jitter_ms, jitter_ms)
        src = rnd.choices(names, weights)[0]
        templates = SERVICES[src][1]
        tw = [LEVEL_WEIGHT[lv] for lv, _ in templates]
        level, tpl = rnd.choices(templates, tw)[0]
        fields = {
            "ms": rnd.randint(1, 3000) if level != "INFO" else rnd.randint(2, 250),
            "ip": "10.0.%d.%d" % (rnd.randint(0, 20), rnd.randint(1, 254)),
            "uid": rnd.randint(1000, 9999),
            "oid": rnd.randint(100000, 999999),
        }
        msg = tpl.format(**fields)
        events.append((level, src, msg, {"host": "node-%d" % rnd.randint(1, 4),
                                         "trace_id": "%08x" % rnd.getrandbits(32)}, ts))
    return events
