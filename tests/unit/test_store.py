import os
import shutil
import sys
import tempfile
import time
from unittest.mock import patch
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from logstore import LogStore, LEVELS  # noqa: E402
from logstore.gen import generate  # noqa: E402
from logstore.store import META_COUNT_LEVEL, META_COUNT_TOTAL, META_COUNT_VERSION  # noqa: E402

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

    def test_ingest_replay_is_idempotent_per_producer(self):
        event = ("INFO", "worker", "started", {"trace_id": "job-1"}, T0)

        first_ids, first_written = self.s.put_ingested_many([event], "agent-a", ["source-1"])
        replay_ids, replay_written = self.s.put_ingested_many([event], "agent-a", ["source-1"])
        other_ids, other_written = self.s.put_ingested_many([event], "agent-b", ["source-1"])

        self.assertEqual(first_written, 1)
        self.assertEqual(replay_written, 0)
        self.assertEqual(replay_ids, first_ids)
        self.assertEqual(other_written, 1)
        self.assertNotEqual(other_ids, first_ids)
        self.assertEqual(self.s.count(), 2)

    def test_ingest_rejects_conflicting_replay_without_partial_write(self):
        original = ("INFO", "worker", "original", {}, T0)
        self.s.put_ingested_many([original], "agent-a", ["source-1"])
        before = self.s.count()

        with self.assertRaisesRegex(ValueError, "source_event_id"):
            self.s.put_ingested_many([
                ("INFO", "worker", "new row", {}, T0 + 1),
                ("INFO", "worker", "changed payload", {}, T0),
            ], "agent-a", ["source-2", "source-1"])

        self.assertEqual(self.s.count(), before)
        self.assertEqual([row["msg"] for row in self.s.query(source="worker")], ["original"])

    def test_duplicate_source_ids_inside_batch_share_one_event(self):
        event = ("INFO", "worker", "same", {}, T0)

        ids, written = self.s.put_ingested_many(
            [event, event], "agent-a", ["source-1", "source-1"]
        )

        self.assertEqual(ids[0], ids[1])
        self.assertEqual(written, 1)
        self.assertEqual(self.s.count(), 1)

    def test_duplicate_source_id_with_changed_payload_in_batch_is_rejected_atomically(self):
        with self.assertRaisesRegex(ValueError, "source_event_id"):
            self.s.put_ingested_many([
                ("INFO", "worker", "first", {}, T0),
                ("INFO", "worker", "changed", {}, T0),
            ], "agent-a", ["source-1", "source-1"])
        self.assertEqual(self.s.count(), 0)

    def test_deleting_event_allows_source_id_to_be_reused(self):
        event = ("INFO", "worker", "first", {}, T0)
        ids, _ = self.s.put_ingested_many([event], "agent-a", ["source-1"])
        self.assertTrue(self.s.delete_event(ids[0]))

        replacement_ids, written = self.s.put_ingested_many(
            [("INFO", "worker", "replacement", {}, T0 + 1)], "agent-a", ["source-1"]
        )

        self.assertEqual(written, 1)
        self.assertNotEqual(replacement_ids[0], ids[0])
        self.assertEqual(self.s.count(), 1)

    def test_idempotency_mapping_survives_reopen_and_checkpoint_restore(self):
        event = ("INFO", "worker", "stable", {"trace_id": "persist"}, T0)
        event_ids, written = self.s.put_ingested_many([event], "agent-a", ["source-1"])
        self.assertEqual(written, 1)
        checkpoint = os.path.join(self.dir, "backup")
        self.s.backup(checkpoint)
        path = self.path
        self.s.close()
        self.s = LogStore(path)
        replay_ids, replay_written = self.s.put_ingested_many([event], "agent-a", ["source-1"])
        self.assertEqual((replay_ids, replay_written), (event_ids, 0))

        restored_path = os.path.join(self.dir, "restored")
        LogStore.restore(checkpoint, restored_path)
        restored = LogStore(restored_path)
        try:
            restored_ids, restored_written = restored.put_ingested_many(
                [event], "agent-a", ["source-1"]
            )
            self.assertEqual((restored_ids, restored_written), (event_ids, 0))
        finally:
            restored.close()

    def test_retention_releases_idempotency_keys_on_fast_and_audit_paths(self):
        old_fast = ("INFO", "ingest-fast", "old", {}, T0)
        fast_ids, _ = self.s.put_ingested_many([old_fast], "agent-a", ["fast"])
        self.assertEqual(self.s.purge_older_than(T0 + 1), 1)
        replacement, written = self.s.put_ingested_many(
            [("INFO", "ingest-fast", "replacement", {}, T0 + 2)], "agent-a", ["fast"]
        )
        self.assertEqual(written, 1)
        self.assertNotEqual(replacement, fast_ids)

        old_slow = ("INFO", "ingest-slow", "old", {}, T0 + 10)
        slow_ids, _ = self.s.put_ingested_many([old_slow], "agent-a", ["slow"])
        audit_fields = {"_audit": True, "actor": "admin", "action": "test", "status": 200}
        self.s._write_events([("INFO", "audit-service", "old audit", audit_fields, T0 + 20)], allow_audit=True)
        self.assertEqual(self.s.purge_older_than(T0 + 30), 2)
        replacement, written = self.s.put_ingested_many(
            [("INFO", "ingest-slow", "replacement", {}, T0 + 31)], "agent-a", ["slow"]
        )
        self.assertEqual(written, 1)
        self.assertNotEqual(replacement, slow_ids)

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

    def test_query_cursor_pages_events_sharing_the_same_timestamp(self):
        ids = self.s.put_many([
            ("INFO", "page-test", str(i), {}, T0) for i in range(5)
        ])

        first = self.s.query(source="page-test", limit=2)
        second = self.s.query(source="page-test", limit=2, before_id=first[-1]["id"])
        third = self.s.query(source="page-test", limit=2, before_id=second[-1]["id"])

        self.assertEqual([event["id"] for event in first + second + third],
                         [event.hex() for event in reversed(ids)])

    def test_lifetime_counts_survive_update_delete_purge_and_reopen(self):
        error_id = self.s.put_event("ERROR", "counts", "old", ts_ms=T0)
        self.s.put_event("INFO", "counts", "keep", ts_ms=T0 + 1000)
        self.assertEqual(self.s.count(), 2)
        self.assertEqual(self.s.count_by_level(), {
            "DEBUG": 0, "INFO": 1, "WARN": 0, "ERROR": 1, "FATAL": 0,
        })

        self.s.update_event(error_id, level="INFO")
        self.assertEqual(self.s.count_by_level()["ERROR"], 0)
        self.assertEqual(self.s.count_by_level()["INFO"], 2)
        self.assertEqual(self.s.purge_older_than(T0 + 500), 1)
        self.assertEqual(self.s.count(), 1)

        path = self.path
        self.s.close()
        self.s = LogStore(path)
        self.assertEqual(self.s.count(), 1)
        self.assertEqual(self.s.count_by_level()["INFO"], 1)

        with patch.object(self.s, "_scan", side_effect=AssertionError("lifetime count scanned event keys")):
            self.assertEqual(self.s.count(), 1)
            self.assertEqual(self.s.count_by_level()["INFO"], 1)

    def test_legacy_database_migrates_counts_once_on_open(self):
        self.s.put_many([
            ("INFO", "old-db", "one", {}, T0),
            ("ERROR", "old-db", "two", {}, T0 + 1),
        ])
        self.s.cf_meta.delete(META_COUNT_VERSION)
        self.s.cf_meta.delete(META_COUNT_TOTAL)
        for level in LEVELS:
            self.s.cf_meta.delete(META_COUNT_LEVEL + level.encode("ascii"))
        path = self.path
        self.s.close()
        self.s = LogStore(path)

        self.assertEqual(self.s.count(), 2)
        self.assertEqual(self.s.count_by_level()["INFO"], 1)
        with patch.object(self.s, "_scan", side_effect=AssertionError("count migration repeated")):
            self.assertEqual(self.s.count(), 2)

    def test_trace_ids_are_not_retained_in_an_unbounded_python_set(self):
        self.s.put_many([
            ("INFO", "traces", "message", {"trace_id": "trace-%04d" % i}, T0 + i)
            for i in range(200)
        ])
        self.assertEqual(self.s.purge_older_than(T0 + 100), 100)
        self.assertEqual(self.s.count(), 100)
        self.assertFalse(hasattr(self.s, "_known_traces"))

    def test_audit_events_cannot_be_updated_or_deleted(self):
        event_id = self.s.put_audit("admin", "event.delete", "test:1", 200)
        with self.assertRaisesRegex(ValueError, "audit"):
            self.s.update_event(event_id, msg="tampered")
        with self.assertRaisesRegex(ValueError, "audit"):
            self.s.delete_event(event_id)

    def test_retention_keeps_immutable_audit_events_and_their_indexes(self):
        now = int(time.time() * 1000)
        expired_id = self.s.put_event("INFO", "normal", "expire", ts_ms=now - 1000)
        audit_id = self.s.put_audit("admin", "event.delete", "test:1", 200)
        recent_id = self.s.put_event("WARN", "normal", "keep", ts_ms=now + 20 * 60_000)

        deleted = self.s.purge_older_than(now + 10 * 60_000)

        self.assertEqual(deleted, 1)
        self.assertIsNone(self.s.get(expired_id))
        self.assertEqual(self.s.get(audit_id)["source"], "audit-service")
        self.assertEqual(self.s.get(recent_id)["msg"], "keep")
        self.assertEqual([event["id"] for event in self.s.query(source="audit-service")], [audit_id.hex()])
        self.assertEqual(self.s.count(), 2)

    def test_retention_keeps_audit_when_expired_rows_cross_batch_boundary(self):
        now = int(time.time() * 1000)
        self.s.put_many([
            ("INFO", "bulk-retention", "expire", {"trace_id": "expired-%d" % i}, now - 1000)
            for i in range(1001)
        ])
        audit_id = self.s.put_audit("admin", "backup.create", "test", 201)

        deleted = self.s.purge_older_than(now + 10 * 60_000)

        self.assertEqual(deleted, 1001)
        self.assertEqual(self.s.count(), 1)
        self.assertEqual(self.s.get(audit_id)["source"], "audit-service")
        self.assertEqual(self.s.query(source="bulk-retention"), [])
        self.assertEqual(len(self.s.query(source="audit-service")), 1)

    def test_legacy_audit_marker_on_normal_event_does_not_make_it_immutable(self):
        fields = {"_audit": True, "legacy": "user data"}
        event_id = self.s.put_event("INFO", "legacy-source", "older row", fields, T0)

        updated = self.s.update_event(event_id, msg="updated")
        self.assertEqual(updated["msg"], "updated")
        self.assertTrue(self.s.delete_event(event_id))


if __name__ == "__main__":
    unittest.main(verbosity=2)
