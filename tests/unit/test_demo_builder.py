import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from logstore import LogStore  # noqa: E402
from tao_du_lieu_demo import _reset, build  # noqa: E402
from werkzeug.security import check_password_hash  # noqa: E402


class DemoBuilderTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.db = self.root / "data" / "webdb"
        self.backups = self.root / "data" / "backups"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_build_creates_accounts_fresh_events_and_initial_checkpoint(self):
        now_ms = int(time.time() * 1000)
        result = build(self.db, self.backups, 2_000, 24, "admin",
                       "Admin-pass-12345", "Demo-pass-12345", seed=7, now_ms=now_ms)
        self.assertEqual(result["users"], 5)
        self.assertEqual(result["demo_events"], 32)
        self.assertEqual(result["total"], 2_032)
        self.assertTrue((self.backups / result["backup"] / "CURRENT").is_file())
        with LogStore(str(self.db)) as store:
            admin = store.get_user("admin")
            self.assertEqual(admin["role"], "admin")
            self.assertTrue(check_password_hash(admin["password_hash"], "Admin-pass-12345"))
            roles = sorted(user["role"] for user in store.list_users())
            self.assertEqual(roles, ["admin", "operator", "operator", "viewer", "viewer"])
            # Dữ liệu phải "tươi": có sự kiện trong 60 phút gần nhất để dashboard không trống.
            self.assertGreater(store.count(now_ms - 3_600_000, now_ms), 0)
            self.assertEqual(len(store.query(trace_id="DEMO-ORDER-003", limit=100)), 7)

    def test_build_refuses_existing_accounts_and_reset_clears_everything(self):
        build(self.db, self.backups, 10, 1, "admin", "Admin-pass-12345", "Demo-pass-12345", seed=1)
        with self.assertRaises(SystemExit):
            build(self.db, self.backups, 10, 1, "admin", "Admin-pass-12345", "Demo-pass-12345", seed=1)
        state = self.db.parent / ".logstore-state.json"
        state.write_text("{}", encoding="utf-8")
        (self.db.parent / "restores").mkdir()
        _reset(self.db, self.backups)
        for path in (self.db, self.backups, state, self.db.parent / "restores"):
            self.assertFalse(path.exists(), path)


if __name__ == "__main__":
    unittest.main()
