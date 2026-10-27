import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from logstore import LogStore  # noqa: E402
from logstore.demo import build_demo_events, seed_demo  # noqa: E402
from werkzeug.security import check_password_hash  # noqa: E402


class DemoSeedTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = LogStore(os.path.join(self.root, "db"))
        self.store.put_user("admin", {
            "password_hash": "keep-existing-hash", "role": "admin", "enabled": True,
        })

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_seed_adds_synthetic_profiles_and_correlated_workflows_idempotently(self):
        timestamps = [row[4] for row in build_demo_events(now_ms=1_800_000_000_000)]
        self.assertEqual(timestamps, sorted(timestamps))
        first = seed_demo(self.store, "Demo-temp-pass-123")
        self.assertEqual(first, {"users_created": 4, "events_written": 32})
        self.assertEqual(self.store.get_user("admin")["password_hash"], "keep-existing-hash")
        user = self.store.get_user("lan.nguyen")
        self.assertEqual(user["full_name"], "Nguyễn Thị Lan")
        self.assertTrue(check_password_hash(user["password_hash"], "Demo-temp-pass-123"))
        self.assertGreaterEqual(len(self.store.query(trace_id="DEMO-ORDER-001", limit=20)), 5)
        count = self.store.count()
        self.assertEqual(seed_demo(self.store, "Ignored-on-repeat-123"),
                         {"users_created": 0, "events_written": 0})
        self.assertEqual(self.store.count(), count)

    def test_seed_rejects_short_password_before_writing_anything(self):
        with self.assertRaises(ValueError):
            seed_demo(self.store, "short")
        self.assertEqual(self.store.count(), 0)
        self.assertIsNone(self.store.get_user("lan.nguyen"))

    def test_seed_requires_an_active_admin(self):
        self.store.delete_user("admin")
        with self.assertRaisesRegex(ValueError, "tao admin dau tien"):
            seed_demo(self.store, "Demo-temp-pass-123")
        self.assertEqual(self.store.list_users(), [])
        self.assertEqual(self.store.count(), 0)

    def test_seed_repairs_legacy_demo_events_with_clamped_timestamps(self):
        legacy_timestamp = 1_800_000_000_000
        self.store.put_event("INFO", "existing", "existing data", ts_ms=legacy_timestamp)
        legacy_events = [
            (level, source, message, fields, legacy_timestamp)
            for level, source, message, fields, _ in build_demo_events(now_ms=legacy_timestamp)
        ]
        self.store.put_many(legacy_events)

        result = seed_demo(self.store, "Demo-temp-pass-123")

        rows = []
        for trace_id in {row[3]["trace_id"] for row in legacy_events}:
            rows.extend(self.store.query(trace_id=trace_id, limit=100))
        self.assertEqual(result["events_written"], 32)
        self.assertEqual(len(rows), 32)
        self.assertGreater(len({row["ts"] for row in rows}), 1)
        self.assertEqual(self.store.count(), 33)

    def test_seed_repairs_remaining_legacy_events_without_restoring_deleted_event(self):
        legacy_timestamp = 1_800_000_000_000
        self.store.put_event("INFO", "existing", "existing data", ts_ms=legacy_timestamp)
        expected = build_demo_events(now_ms=legacy_timestamp)
        missing = expected.pop()
        self.store.put_many([
            (level, source, message, fields, legacy_timestamp)
            for level, source, message, fields, _ in expected
        ])

        result = seed_demo(self.store, "Demo-temp-pass-123")

        rows = []
        for trace_id in {row[3]["trace_id"] for row in expected}:
            rows.extend(self.store.query(trace_id=trace_id, limit=100))
        self.assertEqual(result["events_written"], 31)
        self.assertEqual(len(rows), 31)
        self.assertGreater(len({row["ts"] for row in rows}), 1)
        self.assertFalse(any(row["msg"] == missing[2] for row in rows))
        self.assertEqual(self.store.count(), 32)


if __name__ == "__main__":
    unittest.main()
