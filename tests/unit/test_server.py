import os
import tempfile
import threading
import time
import unittest
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from rocksdict import Checkpoint, Options, Rdict

import app as app_module
from webapp.maintenance import RetentionWorker, prune_checkpoints, retention_settings
from logstore import LogStore


class ServerOptionsTest(unittest.TestCase):
    def server_options(self, environ):
        options = getattr(app_module, "server_options", None)
        self.assertTrue(callable(options), "app.py should expose validated Waitress settings")
        return options(environ)

    def test_server_defaults_are_local_and_bounded(self):
        self.assertEqual(self.server_options({}), {
            "host": "127.0.0.1", "port": 5000, "threads": 8,
        })

    def test_server_settings_can_be_configured(self):
        self.assertEqual(self.server_options({
            "LOGSTORE_HOST": "0.0.0.0", "LOGSTORE_PORT": "5050", "LOGSTORE_THREADS": "12",
        }), {"host": "0.0.0.0", "port": 5050, "threads": 12})

    def test_invalid_server_settings_are_rejected(self):
        for name, value in (
            ("LOGSTORE_HOST", " "),
            ("LOGSTORE_PORT", "abc"),
            ("LOGSTORE_PORT", "0"),
            ("LOGSTORE_PORT", "65536"),
            ("LOGSTORE_THREADS", "zero"),
            ("LOGSTORE_THREADS", "0"),
            ("LOGSTORE_THREADS", "65"),
        ):
            with self.subTest(name=name, value=value):
                self.assertRaises(ValueError, self.server_options, {name: value})


class MaintenanceTest(unittest.TestCase):
    def test_retention_is_disabled_by_default_and_settings_are_bounded(self):
        self.assertEqual(retention_settings({}), {"hours": 0, "interval_seconds": 3600, "backup_count": 0})
        self.assertEqual(retention_settings({
            "LOGSTORE_RETENTION_HOURS": "24",
            "LOGSTORE_RETENTION_INTERVAL_SECONDS": "90",
            "LOGSTORE_BACKUP_RETENTION_COUNT": "3",
        }), {"hours": 24, "interval_seconds": 90, "backup_count": 3})
        for env in (
            {"LOGSTORE_RETENTION_HOURS": "-1"},
            {"LOGSTORE_RETENTION_HOURS": "87601"},
            {"LOGSTORE_RETENTION_INTERVAL_SECONDS": "0"},
            {"LOGSTORE_RETENTION_INTERVAL_SECONDS": "86401"},
            {"LOGSTORE_BACKUP_RETENTION_COUNT": "-1"},
        ):
            with self.subTest(env=env), self.assertRaises(ValueError):
                retention_settings(env)

    def test_retention_worker_resolves_active_store_on_each_run(self):
        calls = []

        class Store:
            def purge_older_than(self, cutoff):
                calls.append((self, cutoff))
                return 0

        first, active = Store(), Store()
        app = SimpleNamespace(
            config={},
            extensions={"logstore": first, "store_lock": threading.RLock()},
            logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        )
        worker = RetentionWorker(app, hours=1, interval_seconds=100)
        worker.run_once()
        app.extensions["logstore"] = active
        worker.run_once()
        self.assertEqual([store for store, _ in calls], [first, active])

    def test_scheduled_retention_purges_old_ingested_events_but_preserves_audit(self):
        with tempfile.TemporaryDirectory() as root:
            store = LogStore(os.path.join(root, "db"))
            try:
                now = int(time.time() * 1000)
                store.put_ingested_many(
                    [("INFO", "worker", "old", {}, now - 2 * 3_600_000)],
                    "worker-a", ["old-source-id"],
                )
                audit_id = store.put_audit("admin", "test", "retention")
                app = SimpleNamespace(
                    extensions={"logstore": store, "store_lock": threading.RLock()},
                    logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
                )
                worker = RetentionWorker(app, hours=1, interval_seconds=3600)

                self.assertEqual(worker.run_once(), 1)
                self.assertIsNotNone(store.get(audit_id))
                self.assertEqual(store.count(), 1)
                _, written = store.put_ingested_many(
                    [("INFO", "worker", "new", {}, now)], "worker-a", ["old-source-id"],
                )
                self.assertEqual(written, 1)
            finally:
                store.close()

    def test_retention_worker_joins_before_store_close(self):
        entered = threading.Event()
        release = threading.Event()

        class Store:
            closed = False

            def purge_older_than(self, cutoff):
                self.assert_open()
                entered.set()
                release.wait(2)
                return 7

            def assert_open(self):
                if self.closed:
                    raise AssertionError("retention overlapped store close")

            def close(self):
                self.closed = True

        active = Store()
        app = SimpleNamespace(
            config={},
            extensions={"logstore": active, "store_lock": threading.RLock()},
            logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        )
        worker = RetentionWorker(app, hours=1, interval_seconds=0.01)
        worker.start()
        self.assertTrue(entered.wait(2))
        releaser = threading.Thread(target=lambda: (time.sleep(0.05), release.set()))
        releaser.start()
        worker.stop()
        releaser.join()
        active.close()
        self.assertFalse(worker.thread.is_alive())
        self.assertTrue(active.closed)

    def test_server_shutdown_stops_worker_before_closing_active_store(self):
        order = []

        class Worker:
            def stop(self):
                order.append("worker_stopped")

        class Store:
            def close(self):
                order.append("store_closed")

        application = SimpleNamespace(
            extensions={"logstore": Store(), "retention_worker": None, "shutdown_complete": False}
        )
        with patch.object(app_module, "start_retention_worker", return_value=Worker()):
            app_module.run_server(application, server=lambda *_a, **_k: order.append("served"))
        self.assertEqual(order, ["served", "worker_stopped", "store_closed"])

    def test_checkpoint_rotation_keeps_newest_and_ignores_invalid_directories(self):
        with tempfile.TemporaryDirectory() as root:
            backups = Path(root)
            store = LogStore(str(backups / "source-db"))

            def checkpoint(name, ts):
                path = backups / name
                store.put_many([("INFO", "svc", name, {}, ts)])
                store.flush()
                store.backup(str(path))
                return path

            oldest = checkpoint("backup-20260101-000000-00000001", 1)
            middle = checkpoint("backup-20260102-000000-00000002", 2)
            newest = checkpoint("backup-20260103-000000-00000003", 3)
            store.close()

            # The new checkpoint must remain even if timestamp ordering is odd.
            os.utime(newest, (1, 1))
            invalid = backups / "backup-20260104-000000-00000004"
            shutil.copytree(newest, invalid)
            (invalid / next(invalid.glob("*.sst")).name).unlink()
            ordinary = backups / "keep-me"
            ordinary.mkdir()

            removed = prune_checkpoints(backups, keep_count=2, newest_path=newest)

            self.assertEqual(removed, 1)
            self.assertFalse(oldest.exists())
            self.assertTrue(middle.exists())
            self.assertTrue(newest.exists())
            self.assertTrue(invalid.exists())
            self.assertTrue(ordinary.exists())

    def test_checkpoint_rotation_never_removes_the_only_valid_checkpoint(self):
        with tempfile.TemporaryDirectory() as root:
            store = LogStore(os.path.join(root, "source-db"))
            store.put_many([("INFO", "svc", "hello", {}, 1)])
            store.flush()
            path = Path(root) / "backup-20260101-000000-00000001"
            store.backup(str(path))
            store.close()
            self.assertEqual(prune_checkpoints(root, keep_count=0), 0)
            self.assertEqual(prune_checkpoints(root, keep_count=1, newest_path=path), 0)
            self.assertTrue(path.exists())

    def test_checkpoint_rotation_accepts_older_schema_without_ingest_column_family(self):
        with tempfile.TemporaryDirectory() as root:
            backup_root = Path(root)
            legacy_path = backup_root / "backup-20260101-000000-00000001"
            options = Options(raw_mode=True)
            options.create_if_missing(True)
            options.create_missing_column_families(True)
            old_column_families = {
                name: Options(raw_mode=True)
                for name in ("idx_level", "idx_source", "idx_trace", "meta", "users")
            }
            legacy_db = Rdict(str(backup_root / "legacy-source"), options, column_families=old_column_families)
            legacy_db[b"legacy-event"] = b"payload"
            Checkpoint(legacy_db).create_checkpoint(str(legacy_path))
            legacy_db.close()

            current_store = LogStore(str(backup_root / "current-source"))
            current_store.put_many([("INFO", "svc", "current", {}, 2)])
            current_store.flush()
            current_path = backup_root / "backup-20260102-000000-00000002"
            current_store.backup(str(current_path))
            current_store.close()

            removed = prune_checkpoints(backup_root, keep_count=1, newest_path=legacy_path)

            self.assertEqual(removed, 1)
            self.assertTrue(legacy_path.exists())
            self.assertFalse(current_path.exists())
