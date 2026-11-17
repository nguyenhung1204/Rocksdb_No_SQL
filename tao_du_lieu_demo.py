"""Tạo một CSDL demo LogStore mới với dữ liệu "tươi" tính đến thời điểm hiện tại.

Chạy ngay trước buổi demo để các ô "60 phút qua" và bộ lọc "1 giờ gần nhất" có dữ liệu:

    python tao_du_lieu_demo.py --reset

Script tạo: 1 tài khoản admin, 4 tài khoản mẫu (2 operator, 2 viewer), 32 sự kiện theo
5 luồng nghiệp vụ mẫu, N sự kiện tự sinh rải đều trong số giờ gần nhất, và 1 bản
sao lưu checkpoint ban đầu (dùng cho demo khôi phục và cho ldb/sst_dump).

Mật khẩu không nằm trong mã nguồn: đọc từ biến môi trường
LOGSTORE_DEMO_ADMIN_PASSWORD / LOGSTORE_DEMO_USER_PASSWORD (ví dụ đặt trong .env.demo,
tệp này đã được .gitignore bỏ qua), nếu không có thì hỏi khi chạy.
"""

import argparse
import getpass
import os
import secrets
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from werkzeug.security import generate_password_hash

from logstore import LogStore
from logstore.demo import seed_demo
from logstore.gen import generate

MIN_PASSWORD_LENGTH = 10


def _ask_password(env_name, label):
    value = os.environ.get(env_name, "")
    if value:
        return value
    while True:
        first = getpass.getpass("Mật khẩu cho %s (tối thiểu %d ký tự): " % (label, MIN_PASSWORD_LENGTH))
        if len(first) < MIN_PASSWORD_LENGTH:
            print("  Mật khẩu quá ngắn, hãy nhập lại.")
            continue
        if getpass.getpass("Nhập lại mật khẩu: ") != first:
            print("  Hai lần nhập không khớp, hãy nhập lại.")
            continue
        return first


def _reset(db_path, backup_root):
    data_root = db_path.parent
    for path in (db_path, backup_root, data_root / "restores"):
        if path.exists():
            shutil.rmtree(path)
    state_file = data_root / ".logstore-state.json"
    if state_file.exists():
        state_file.unlink()


def build(db_path, backup_root, n_events, hours, admin_user, admin_password, user_password,
          seed=None, now_ms=None):
    """Tạo dữ liệu demo vào db_path (phải chưa có tài khoản). Trả về thống kê."""
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with LogStore(str(db_path)) as store:
        if store.list_users():
            raise SystemExit("CSDL %s đã có tài khoản; chạy lại với --reset để tạo mới." % db_path)
        store.put_user(admin_user, {
            "password_hash": generate_password_hash(admin_password),
            "role": "admin",
            "enabled": True,
            "full_name": "Quản trị viên demo",
        })
        demo = seed_demo(store, user_password)
        generated = generate(n_events, end_ms=now_ms - 5_000, span_hours=hours,
                             seed=seed if seed is not None else now_ms % 100_000, vietnamese=True)
        for start in range(0, len(generated), 1000):
            store.put_many(generated[start:start + 1000])
        store.flush()
        backup_root.mkdir(parents=True, exist_ok=True)
        backup_name = "backup-%s-%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"), secrets.token_hex(4))
        store.backup(str(backup_root / backup_name))
        total = store.count()
    return {"users": 1 + demo["users_created"], "demo_events": demo["events_written"],
            "generated": len(generated), "total": total, "backup": backup_name}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Tạo CSDL demo LogStore với dữ liệu mới")
    parser.add_argument("--db", default="data/webdb", help="thư mục CSDL (mặc định data/webdb)")
    parser.add_argument("--events", type=int, default=50_000, help="số sự kiện tự sinh (mặc định 50000)")
    parser.add_argument("--hours", type=int, default=24, help="rải sự kiện trong bao nhiêu giờ gần nhất")
    parser.add_argument("--admin", default="admin", help="tên đăng nhập quản trị viên (mặc định admin)")
    parser.add_argument("--reset", action="store_true",
                        help="xóa CSDL, bản sao lưu và trạng thái khôi phục cũ trước khi tạo")
    args = parser.parse_args()
    if not 0 <= args.events <= 200_000:
        raise SystemExit("--events phải trong khoảng 0-200000")
    if not 1 <= args.hours <= 24 * 30:
        raise SystemExit("--hours phải trong khoảng 1-720")

    db_path = Path(args.db).resolve()
    backup_root = db_path.parent / "backups"
    if args.reset:
        _reset(db_path, backup_root)
    elif (db_path / "CURRENT").is_file():
        raise SystemExit("Đã có CSDL tại %s. Dùng --reset nếu muốn xóa và tạo lại." % db_path)

    admin_password = _ask_password("LOGSTORE_DEMO_ADMIN_PASSWORD", "tài khoản '%s'" % args.admin)
    user_password = _ask_password("LOGSTORE_DEMO_USER_PASSWORD", "4 tài khoản mẫu")
    started = time.time()
    result = build(db_path, backup_root, args.events, args.hours, args.admin,
                   admin_password, user_password)
    print("Đã tạo CSDL demo tại %s trong %.1f giây:" % (db_path, time.time() - started))
    print("  - %d tài khoản (admin '%s' + 4 tài khoản mẫu)" % (result["users"], args.admin))
    print("  - %d sự kiện mẫu theo 5 trace DEMO-*" % result["demo_events"])
    print("  - %d sự kiện tự sinh trong %d giờ gần nhất" % (result["generated"], args.hours))
    print("  - Tổng cộng %d sự kiện, bản sao lưu ban đầu: %s" % (result["total"], result["backup"]))


if __name__ == "__main__":
    main()
