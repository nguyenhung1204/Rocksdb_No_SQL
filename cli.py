"""
cli.py - Giao dien dong lenh cho LogStore.

Vi du:
  python cli.py gen --n 100000                  # sinh du lieu mau
  python cli.py query --level ERROR --last 1h   # loi trong 1 gio gan nhat
  python cli.py stats
"""

import argparse
import datetime
import json
import os
import re
import sys
import time

from logstore import LogStore, LEVELS
from logstore.demo import seed_demo
from logstore.gen import generate
from logstore.store import is_internal_audit_event

DEFAULT_DB = os.environ.get("LOGSTORE_PATH", "data/logdb")


def parse_duration(text):
    """'30m' -> ms, '2h' -> ms, '7d' -> ms"""
    m = re.fullmatch(r"(\d+)([smhd])", text.strip())
    if not m:
        raise argparse.ArgumentTypeError("định dạng thời gian: 30s, 15m, 2h, 7d")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}[unit]


def parse_time(text):
    """'2026-10-12 09:30' hoặc '2026-10-12' -> ms (giờ địa phương)"""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.datetime.strptime(text, fmt).timestamp() * 1000)
        except ValueError:
            pass
    raise argparse.ArgumentTypeError("dung dinh dang YYYY-MM-DD [HH:MM[:SS]]")


def fmt_ts(ms):
    return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S.") + "%03d" % (ms % 1000)


def print_event(e):
    print("%s  %-5s  %-16s %s" % (fmt_ts(e["ts"]), e["level"], e["source"], e["msg"]))


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024
    return "%.1f TB" % n


# ------------------------------------------------------------------ lenh con
def cmd_gen(a, s):
    t = time.time()
    events = generate(a.n, span_hours=a.hours, seed=a.seed, vietnamese=True)
    t_gen = time.time() - t
    t = time.time()
    for i in range(0, len(events), a.batch):
        s.put_many(events[i:i + a.batch])
    dt = time.time() - t
    print("Đã ghi %d sự kiện (sinh: %.2fs, ghi: %.2fs, %d sự kiện/giây, batch=%d)"
          % (a.n, t_gen, dt, a.n / dt if dt else 0, a.batch))


def cmd_seed_demo(a, s):
    result = seed_demo(s, a.password)
    print("Đã thêm %d tài khoản mẫu và %d sự kiện theo luồng nghiệp vụ"
          % (result["users_created"], result["events_written"]))


def cmd_add(a, s):
    fields = json.loads(a.fields) if a.fields else None
    eid = s.put_event(a.level, a.source, a.msg, fields)
    print("Đã ghi, id =", eid.hex())


def cmd_get(a, s):
    e = s.get(LogStore.parse_id(a.id))
    print(json.dumps(e, ensure_ascii=False, indent=2) if e else "Không tìm thấy")


def cmd_query(a, s):
    now = int(time.time() * 1000)
    start, end = a.start, a.end
    if a.last:
        start, end = now - a.last, now
    t = time.time()
    rows = s.query(start_ms=start, end_ms=end, level=a.level, source=a.source,
                   trace_id=a.trace_id, contains=a.contains, limit=a.limit, reverse=not a.oldest_first)
    dt = (time.time() - t) * 1000
    for e in rows:
        if a.json:
            print(json.dumps(e, ensure_ascii=False))
        else:
            print_event(e)
    print("-- %d kết quả trong %.2f ms" % (len(rows), dt), file=sys.stderr)


def cmd_count(a, s):
    c = s.count_by_level(a.start, a.end)
    for lv in LEVELS:
        print("%-6s %d" % (lv, c[lv]))
    print("%-6s %d" % ("TỔNG", sum(c.values())))


def cmd_sources(a, s):
    print("\n".join(s.sources()))


def cmd_stats(a, s):
    st = s.stats()
    print("Thư mục DB :", st["path"])
    print("Dung lượng :", human(st["disk_bytes"]))
    print("%-12s %12s %12s %12s %8s" % ("CF", "số key (ước)", "SST", "memtable", "L0 file"))
    for name, v in st["column_families"].items():
        print("%-12s %12d %12s %12s %8d" % (name, v["estimate_num_keys"],
              human(v["sst_size_bytes"]), human(v["memtable_bytes"]), v["l0_files"]))


def cmd_purge(a, s):
    if a.older_than <= 0:
        raise ValueError("--older-than phải lớn hơn 0")
    cutoff = int(time.time() * 1000) - a.older_than
    before = s.stats()["disk_bytes"]
    n = s.purge_older_than(cutoff, compact=a.compact)
    after = s.stats()["disk_bytes"]
    print("Đã xóa %d sự kiện trước %s" % (n, fmt_ts(cutoff)))
    print("Dung lượng: %s -> %s%s" % (human(before), human(after),
          "" if a.compact else "  (chạy thêm --compact để giải phóng dung lượng)"))


def cmd_compact(a, s):
    before = s.stats()["disk_bytes"]
    s.flush()
    s.compact()
    print("Dung lượng: %s -> %s" % (human(before), human(s.stats()["disk_bytes"])))


def cmd_backup(a, s):
    s.flush()
    try:
        s.backup(a.dest)
    except FileExistsError:
        sys.exit("Thư mục %s đã tồn tại, hãy chọn tên khác (checkpoint cần thư mục mới)" % a.dest)
    print("Đã sao lưu vào", a.dest)


def cmd_import(a, s):
    events = []
    with open(a.path, "r", encoding="utf-8-sig") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            if len(events) >= 100_000:
                raise ValueError("tệp vượt quá 100000 sự kiện")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError("JSONL lỗi tại dòng %d: %s" % (line_no, error.msg))
            if not isinstance(record, dict):
                raise ValueError("dòng %d phải là object JSON" % line_no)
            level = record.get("level", "INFO")
            level = level.upper() if isinstance(level, str) else level
            source, msg, fields = record.get("source", ""), record.get("msg", ""), record.get("fields")
            ts = record.get("ts")
            if ts is not None and (not isinstance(ts, int) or isinstance(ts, bool) or not 0 <= ts <= 0xFFFFFFFFFFFFFFFF):
                raise ValueError("ts tại dòng %d phải là số nguyên milliseconds hợp lệ" % line_no)
            s._check_event(level, source, msg, fields)
            events.append((level, source, msg, fields, ts))
    if not events:
        raise ValueError("tệp JSONL không có sự kiện")
    ids = s.put_many(events)
    print("Đã nhập %d sự kiện" % len(ids))


def cmd_export(a, s):
    if not 1 <= a.limit <= 1_000_000:
        raise ValueError("--limit phải trong khoảng 1-1000000")
    total = s.count()
    if total > a.limit:
        raise ValueError("có %d sự kiện, vượt quá giới hạn --limit %d" % (total, a.limit))
    rows = s.query(limit=a.limit)
    os.makedirs(os.path.dirname(os.path.abspath(a.path)), exist_ok=True)
    exported_count = 0
    with open(a.path, "w", encoding="utf-8", newline="\n") as stream:
        for event in rows:
            if is_internal_audit_event(event):
                continue
            record = {key: event[key] for key in ("level", "source", "msg", "fields", "ts")}
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            exported_count += 1
    print("Đã xuất %d sự kiện vào %s" % (exported_count, a.path))


def cmd_restore(a, _s=None):
    LogStore.restore(a.backup, a.dest)
    print("Đã khôi phục %s vào %s" % (a.backup, a.dest))


def main():
    # In tiếng Việt có dấu đúng trên mọi console/pipe (Windows mặc định không phải UTF-8).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="LogStore - lưu trữ nhật ký trên RocksDB")
    p.add_argument("--db", default=DEFAULT_DB, help="thư mục DB (mặc định %s)" % DEFAULT_DB)
    p.add_argument("--sync", action="store_true", help="fsync WAL mỗi lần ghi")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen", help="sinh dữ liệu mẫu")
    g.add_argument("--n", type=int, default=100_000)
    g.add_argument("--hours", type=int, default=24)
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--batch", type=int, default=1000)
    g.set_defaults(fn=cmd_gen)

    demo = sub.add_parser("seed-demo", help="thêm tài khoản và luồng sự kiện demo có liên kết")
    demo.add_argument("--password", required=True,
                      help="mật khẩu tạm chung cho tài khoản demo, tối thiểu 10 ký tự")
    demo.set_defaults(fn=cmd_seed_demo)

    ad = sub.add_parser("add", help="ghi 1 sự kiện")
    ad.add_argument("level", choices=LEVELS)
    ad.add_argument("source")
    ad.add_argument("msg")
    ad.add_argument("--fields", help='JSON, vd \'{"user":1}\'')
    ad.set_defaults(fn=cmd_add)

    ge = sub.add_parser("get", help="đọc 1 sự kiện theo id")
    ge.add_argument("id")
    ge.set_defaults(fn=cmd_get)

    q = sub.add_parser("query", help="truy vấn sự kiện")
    q.add_argument("--start", type=parse_time)
    q.add_argument("--end", type=parse_time)
    q.add_argument("--last", type=parse_duration, help="ví dụ 30m, 2h, 1d")
    q.add_argument("--level", choices=LEVELS, help="lấy từ mức này trở lên")
    q.add_argument("--source")
    q.add_argument("--trace-id", help="lọc theo trace_id trong fields")
    q.add_argument("--contains", help="lọc chuỗi con trong msg")
    q.add_argument("--limit", type=int, default=20)
    q.add_argument("--oldest-first", action="store_true")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=cmd_query)

    c = sub.add_parser("count", help="đếm sự kiện theo mức")
    c.add_argument("--start", type=parse_time)
    c.add_argument("--end", type=parse_time)
    c.set_defaults(fn=cmd_count)

    sub.add_parser("sources", help="liệt kê các nguồn").set_defaults(fn=cmd_sources)
    sub.add_parser("stats", help="thống kê RocksDB").set_defaults(fn=cmd_stats)

    pg = sub.add_parser("purge", help="xóa log cũ (retention)")
    pg.add_argument("--older-than", type=parse_duration, required=True, help="ví dụ 12h, 7d")
    pg.add_argument("--compact", action="store_true")
    pg.set_defaults(fn=cmd_purge)

    sub.add_parser("compact", help="flush + compaction thủ công").set_defaults(fn=cmd_compact)

    b = sub.add_parser("backup", help="sao lưu bằng checkpoint")
    b.add_argument("dest")
    b.set_defaults(fn=cmd_backup)

    imp = sub.add_parser("import", help="nhập sự kiện từ tệp JSONL")
    imp.add_argument("path", help="đường dẫn tệp UTF-8 JSONL")
    imp.set_defaults(fn=cmd_import)

    exp = sub.add_parser("export", help="xuất sự kiện ra tệp JSONL")
    exp.add_argument("path", help="đường dẫn tệp kết quả")
    exp.add_argument("--limit", type=int, default=1_000_000)
    exp.set_defaults(fn=cmd_export)

    restore = sub.add_parser("restore", help="khôi phục checkpoint vào thư mục DB mới")
    restore.add_argument("backup", help="thư mục checkpoint nguồn")
    restore.add_argument("dest", help="thư mục DB mới, phải chưa tồn tại")
    restore.set_defaults(fn=cmd_restore)

    a = p.parse_args()
    try:
        if a.cmd == "restore":
            a.fn(a)
        else:
            os.makedirs(os.path.dirname(os.path.abspath(a.db)), exist_ok=True)
            with LogStore(a.db, sync_writes=a.sync) as s:
                a.fn(a, s)
    except (OSError, ValueError, TypeError) as error:
        sys.exit("Lỗi: %s" % error)


if __name__ == "__main__":
    main()
