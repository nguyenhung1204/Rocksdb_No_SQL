import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from logstore import LogStore, LEVELS  # noqa: E402
from logstore.gen import generate  # noqa: E402

T0 = 1_700_000_000_000  # moc thoi gian co dinh de test lap lai duoc


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "db")
        self.s = LogStore(self.path)

    def tearDown(self):
        try:
            self.s.close()
        except Exception:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)

    def _load(self, n=500):
        ev = generate(n, end_ms=T0 + 3_600_000, span_hours=1, seed=7)
        self.s.put_many(ev)
        return ev

    def test_put_get_roundtrip(self):
        eid = self.s.put_event("ERROR", "auth", "xin chao", {"a": 1}, ts_ms=T0)
        got = self.s.get(eid)
        self.assertEqual(got["msg"], "xin chao")
        self.assertEqual(got["level"], "ERROR")
        self.assertEqual(got["fields"], {"a": 1})
        self.assertEqual(got["ts"], T0)

    def test_get_missing(self):
        self.assertIsNone(self.s.get(b"\x00" * 12))

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            self.s.put_event("LOUD", "x", "m")
        with self.assertRaises(ValueError):
            self.s.put_event("INFO", "bad\x00src", "m")
        with self.assertRaises(ValueError):
            self.s.put_event("INFO", "", "m")

    def test_ids_strictly_increasing_same_ms(self):
        ids = [self.s.put_event("INFO", "a", "m%d" % i, ts_ms=T0) for i in range(50)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 50)

    def test_clock_rollback_preserves_event_time_and_time_query(self):
        self.s.put_event("INFO", "a", "newer", ts_ms=T0 + 1000)
        late = self.s.put_event("INFO", "a", "late arrival", ts_ms=T0)

        self.assertEqual(self.s.get(late)["ts"], T0)
        rows = self.s.query(start_ms=T0, end_ms=T0, limit=10, reverse=False)
        self.assertEqual([row["msg"] for row in rows], ["late arrival"])

    def test_late_event_id_avoids_existing_timestamp_after_reopen(self):
        self.s.put_event("INFO", "a", "newer", ts_ms=T0 + 1000)
        first = self.s.put_event("INFO", "a", "first late arrival", ts_ms=T0)
        self.s.close()
        self.s = LogStore(self.path)

        second = self.s.put_event("INFO", "a", "second late arrival", ts_ms=T0)

        self.assertNotEqual(second, first)
        self.assertEqual(self.s.get(second)["ts"], T0)

    def test_ids_increasing_after_reopen(self):
        a = self.s.put_event("INFO", "a", "x", ts_ms=T0)
        self.s.close()
        self.s = LogStore(self.path)
        b = self.s.put_event("INFO", "a", "y", ts_ms=T0)
        self.assertGreater(b, a)

    def test_time_range_matches_bruteforce(self):
        ev = self._load()
        lo, hi = T0 + 600_000, T0 + 1_200_000
        want = sorted(e[4] for e in ev if lo <= e[4] <= hi)
        got = self.s.query(start_ms=lo, end_ms=hi, limit=10**6, reverse=False)
        self.assertEqual([g["ts"] for g in got], want)

    def test_reverse_is_exact_reverse(self):
        self._load()
        fwd = self.s.query(limit=10**6, reverse=False)
        rev = self.s.query(limit=10**6, reverse=True)
        self.assertEqual([e["id"] for e in fwd], [e["id"] for e in reversed(rev)])
        self.assertEqual(len(fwd), 500)

    def test_level_filter_matches_bruteforce(self):
        ev = self._load()
        for lv in LEVELS:
            m = LEVELS.index(lv)
            want = sum(1 for e in ev if LEVELS.index(e[0]) >= m)
            got = self.s.query(level=lv, limit=10**6)
            self.assertEqual(len(got), want, lv)
            self.assertTrue(all(LEVELS.index(g["level"]) >= m for g in got))
            tss = [g["id"] for g in got]
            self.assertEqual(tss, sorted(tss, reverse=True))  # da gop dung thu tu

    def test_source_filter(self):
        ev = self._load()
        want = sum(1 for e in ev if e[1] == "auth-service")
        got = self.s.query(source="auth-service", limit=10**6)
        self.assertEqual(len(got), want)
        self.assertTrue(all(g["source"] == "auth-service" for g in got))

    def test_source_prefix_not_confused(self):
        # "api" khong duoc khop nham "api-gateway"
        self.s.put_event("INFO", "api", "a", ts_ms=T0)
        self.s.put_event("INFO", "api-gateway", "b", ts_ms=T0 + 1)
        got = self.s.query(source="api")
        self.assertEqual([g["msg"] for g in got], ["a"])

    def test_source_and_level_and_time(self):
        ev = self._load()
        lo, hi = T0 + 300_000, T0 + 900_000
        want = [e for e in ev if e[1] == "payment-service" and e[0] in ("ERROR", "FATAL")
                and lo <= e[4] <= hi]
        got = self.s.query(start_ms=lo, end_ms=hi, level="ERROR",
                           source="payment-service", limit=10**6)
        self.assertEqual(len(got), len(want))

    def test_limit_and_contains(self):
        self._load()
        self.assertEqual(len(self.s.query(limit=7)), 7)
        got = self.s.query(contains="timeout", limit=10**6)
        self.assertTrue(got and all("timeout" in g["msg"].lower() for g in got))

    def test_zero_limit_returns_no_events(self):
        self._load(3)
        self.assertEqual(self.s.query(limit=0), [])

    def test_negative_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            self.s.query(limit=-1)

    def test_trace_id_index_filter(self):
        self.s.put_event("INFO", "api", "first", {"trace_id": "trace-a"}, ts_ms=T0)
        self.s.put_event("ERROR", "api", "second", {"trace_id": "trace-b"}, ts_ms=T0 + 1)
        got = self.s.query(trace_id="trace-a")
        self.assertEqual([e["msg"] for e in got], ["first"])

    def test_update_event_keeps_all_indexes_consistent(self):
        event_id = self.s.put_event("INFO", "old-source", "before", {"trace_id": "old-trace"}, ts_ms=T0)
        updated = self.s.update_event(
            event_id, level="ERROR", source="new-source", msg="after",
            fields={"trace_id": "new-trace"},
        )
        self.assertEqual(updated["id"], event_id.hex())
        self.assertEqual(self.s.query(source="old-source"), [])
        self.assertEqual(self.s.query(level="ERROR", source="new-source", trace_id="new-trace")[0]["msg"], "after")
        self.assertEqual(self.s.query(trace_id="old-trace"), [])

    def test_delete_event_removes_all_indexes(self):
        event_id = self.s.put_event("WARN", "service", "remove", {"trace_id": "trace-delete"}, ts_ms=T0)
        self.assertTrue(self.s.delete_event(event_id))
        self.assertFalse(self.s.delete_event(event_id))
        self.assertIsNone(self.s.get(event_id))
        self.assertEqual(self.s.query(source="service"), [])
        self.assertEqual(self.s.query(trace_id="trace-delete"), [])

    def test_user_records_persist(self):
        record = {"role": "admin", "password_hash": "hashed", "enabled": True}
        self.s.put_user("Admin", record)
        self.assertEqual(self.s.get_user("admin"), record)
        self.assertEqual(self.s.list_users(), [{"username": "admin", **record}])
        self.s.delete_user("ADMIN")
        self.assertIsNone(self.s.get_user("admin"))

    def test_count_by_level(self):
        ev = self._load()
        c = self.s.count_by_level()
        for lv in LEVELS:
            self.assertEqual(c[lv], sum(1 for e in ev if e[0] == lv))
        self.assertEqual(sum(c.values()), 500)

    def test_purge_keeps_indexes_consistent(self):
        ev = self._load()
        cutoff = T0 + 1_800_000
        keep = sum(1 for e in ev if e[4] >= cutoff)
        removed = self.s.purge_older_than(cutoff)
        self.assertEqual(removed, len(ev) - keep)
        self.assertEqual(len(self.s.query(limit=10**6)), keep)
        # index theo muc/nguon cung phai duoc don sach
        self.assertEqual(sum(self.s.count_by_level().values()), keep)
        self.assertEqual(len(self.s.query(level="DEBUG", limit=10**6)), keep)
        for src in self.s.sources():
            n_src = len(self.s.query(source=src, limit=10**6))
            n_all = sum(1 for e in ev if e[1] == src and e[4] >= cutoff)
            self.assertEqual(n_src, n_all, src)

    def test_persistence_after_reopen(self):
        self._load(200)
        self.s.close()
        self.s = LogStore(self.path)
        self.assertEqual(len(self.s.query(limit=10**6)), 200)
        self.assertTrue(len(self.s.sources()) >= 3)

    def test_flush_and_compact_keep_data(self):
        self._load(300)
        self.s.flush()
        self.s.compact()
        self.assertEqual(len(self.s.query(limit=10**6)), 300)

    def test_backup_restore(self):
        self._load(300)
        bk = os.path.join(self.dir, "bk")
        self.s.backup(bk)
        with self.assertRaises(FileExistsError):
            self.s.backup(bk)
        # ghi them sau khi backup -> ban backup khong duoc co
        self.s.put_event("INFO", "x", "sau backup", ts_ms=T0 + 99_999_999)
        target = os.path.join(self.dir, "restored")
        LogStore.restore(bk, target)
        r = LogStore(target)
        try:
            self.assertEqual(len(r.query(limit=10**6)), 300)
            self.assertEqual(r.query(contains="sau backup"), [])
        finally:
            r.close()

    def test_unicode_message(self):
        eid = self.s.put_event("WARN", "dich-vu", "Lỗi kết nối cơ sở dữ liệu", ts_ms=T0)
        self.assertEqual(self.s.get(eid)["msg"], "Lỗi kết nối cơ sở dữ liệu")


if __name__ == "__main__":
    unittest.main(verbosity=2)
