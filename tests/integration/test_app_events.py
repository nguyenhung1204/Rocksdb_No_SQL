import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from io import BytesIO
from unittest.mock import patch

from app import create_app
from werkzeug.security import generate_password_hash


class AppEventTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.app = create_app(
            db_path=os.path.join(self.root, "db"),
            backup_path=os.path.join(self.root, "backups"),
            secret_key="event-test-secret",
        )
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        page = self.client.get("/setup")
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
        self.client.post("/setup", data={
            "csrf_token": token,
            "username": "admin",
            "password": "strong-pass-123",
            "confirm_password": "strong-pass-123",
        })

    def tearDown(self):
        self.app.extensions["logstore"].close()
        shutil.rmtree(self.root, ignore_errors=True)

    def csrf(self):
        return self.client.get("/api/session").json["csrf_token"]

    def test_event_crud_trace_query_and_limits(self):
        token = self.csrf()
        created = self.client.post("/api/events", json={
            "level": "INFO", "source": "api", "msg": "before",
            "fields": {"trace_id": "trace-old", "host": "node-1"},
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(created.status_code, 201)
        event_id = created.json["ids"][0]
        matched = self.client.get("/api/events?trace_id=trace-old&source=api&level=DEBUG")
        self.assertEqual(matched.json["count"], 1)
        updated = self.client.put("/api/events/" + event_id, json={
            "level": "ERROR", "source": "worker", "msg": "after",
            "fields": {"trace_id": "trace-new"},
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json["id"], event_id)
        self.assertEqual(self.client.get("/api/events?trace_id=trace-old").json["count"], 0)
        result = self.client.get("/api/events?trace_id=trace-new&level=ERROR&source=worker")
        self.assertEqual(result.json["events"][0]["msg"], "after")
        self.assertEqual(self.client.get("/api/events?limit=0").json["events"], [])
        self.assertEqual(self.client.get("/api/events?limit=-1").status_code, 400)
        removed = self.client.delete("/api/events/" + event_id, headers={"X-CSRF-Token": token})
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get("/api/events?trace_id=trace-new").json["count"], 0)

    def test_invalid_purge_duration_never_changes_records(self):
        token = self.csrf()
        self.client.post("/api/events", json={"level":"ERROR","source":"api","msg":"keep"},
                         headers={"X-CSRF-Token":token})
        before = self.client.get("/api/stats").json["total_events"]
        response = self.client.post("/api/purge", json={"older_than_hours":-1},
                                    headers={"X-CSRF-Token":token})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/stats").json["total_events"], before)

    def test_malformed_json_field_types_return_validation_errors(self):
        token = self.csrf()
        for record in (
            {"level": ["ERROR"], "source": "api", "msg": "bad level"},
            {"level": "INFO", "source": ["api"], "msg": "bad source"},
        ):
            response = self.client.post("/api/events", json=record, headers={"X-CSRF-Token": token})
            self.assertEqual(response.status_code, 400)
        boolean_hours = self.client.post("/api/purge", json={"older_than_hours": True},
                                        headers={"X-CSRF-Token": token})
        self.assertEqual(boolean_hours.status_code, 400)

    def test_import_validation_is_all_or_nothing_and_export_is_jsonl(self):
        token = self.csrf()
        total_before = self.client.get("/api/stats").json["total_events"]
        invalid = b'{"level":"INFO","source":"a","msg":"good"}\n{"level":"LOUD","source":"a","msg":"bad"}\n'
        response = self.client.post("/api/import", data={"file": (BytesIO(invalid), "invalid.jsonl")},
                                    headers={"X-CSRF-Token":token}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/stats").json["total_events"], total_before)

        valid = b'{"level":"INFO","source":"a","msg":"one","fields":{"trace_id":"t1"},"ts":1700000000000}\n{"level":"WARN","source":"b","msg":"two","ts":1700000000001}\n'
        imported = self.client.post("/api/import", data={"file": (BytesIO(valid), "events.jsonl")},
                                    headers={"X-CSRF-Token":token}, content_type="multipart/form-data")
        self.assertEqual(imported.status_code, 201)
        exported = self.client.get("/api/export")
        rows = [json.loads(line) for line in exported.get_data(as_text=True).splitlines()]
        rows = [row for row in rows if row["source"] in {"a", "b"}]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["msg"] for row in rows}, {"one", "two"})

    def test_export_omits_immutable_audit_rows_so_the_file_can_be_reimported(self):
        store = self.app.extensions["logstore"]
        store.put_audit("admin", "test.audit")
        store.put_event("INFO", "portable", "ordinary event")
        store.put_event("INFO", "legacy", "old marker", {"_audit": True, "legacy": "user data"})

        exported = self.client.get("/api/export", buffered=True)
        rows = [json.loads(line) for line in exported.get_data(as_text=True).splitlines()]
        self.assertTrue(rows)
        self.assertFalse(any(row["source"] == "audit-service" for row in rows))
        self.assertTrue(any(row["source"] == "legacy" and row["fields"].get("_audit") for row in rows))

        token = self.csrf()
        imported = self.client.post("/api/import", data={
            "file": (BytesIO(exported.get_data()), "export.jsonl"),
        }, headers={"X-CSRF-Token":token}, content_type="multipart/form-data")
        self.assertEqual(imported.status_code, 201)

    def test_restore_switches_active_store_to_checkpoint(self):
        token = self.csrf()
        self.client.post("/api/events", json={"level":"INFO","source":"api","msg":"in backup"},
                         headers={"X-CSRF-Token":token})
        created = self.client.post("/api/backup", headers={"X-CSRF-Token":token})
        self.assertEqual(created.status_code, 201)
        self.client.post("/api/events", json={"level":"INFO","source":"api","msg":"after backup"},
                         headers={"X-CSRF-Token":token})
        restored = self.client.post("/api/restore", json={"name":created.json["name"]},
                                    headers={"X-CSRF-Token":token})
        self.assertEqual(restored.status_code, 200)
        self.assertTrue(os.path.isdir(restored.json["active_path"]))
        messages = [event["msg"] for event in self.client.get("/api/events").json["events"]]
        self.assertIn("in backup", messages)
        self.assertNotIn("after backup", messages)

    def test_restore_checkpoint_recovers_user_accounts_with_events(self):
        store = self.app.extensions["logstore"]
        store.put_user("backup-only", {"password_hash": "hash-before", "role": "viewer", "enabled": True})
        token = self.csrf()
        backup = self.client.post("/api/backup", headers={"X-CSRF-Token":token})
        self.assertEqual(backup.status_code, 201)
        store.put_user("created-after", {"password_hash": "hash-after", "role": "viewer", "enabled": True})

        restored = self.client.post("/api/restore", json={"name": backup.json["name"]},
                                    headers={"X-CSRF-Token":token})

        self.assertEqual(restored.status_code, 200)
        usernames = {user["username"] for user in self.app.extensions["logstore"].list_users()}
        self.assertIn("admin", usernames)
        self.assertIn("backup-only", usernames)
        self.assertNotIn("created-after", usernames)

    def test_backup_endpoint_rotates_only_after_success_and_keeps_configured_count(self):
        self.app.config["BACKUP_RETENTION_COUNT"] = 2
        token = self.csrf()
        created = []
        for _ in range(3):
            response = self.client.post("/api/backup", headers={"X-CSRF-Token":token})
            self.assertEqual(response.status_code, 201)
            created.append(response.json["name"])

        backups = self.client.get("/api/backups").json["backups"]

        self.assertEqual(len(backups), 2)
        self.assertIn(created[-1], backups)
        self.assertNotIn(created[0], backups)

    def test_audit_api_is_admin_only_filters_seeded_rows_and_pages_canonically(self):
        store = self.app.extensions["logstore"]
        now = int(time.time() * 1000) + 10_000
        store.put_many([("INFO", "audit-service", "ordinary newest", {"sample": True}, now + 600)] + [
            ("INFO", "audit-service", "ordinary row %d" % index, {"sample": True}, now + 499 - index)
            for index in range(251)
        ])
        store._write_events([
            ("INFO", "audit-service", "canonical newest", {
                "_audit": True, "actor": "new-admin", "action": "event.update",
                "status": 200, "target": "record:2", "password_hash": "do-not-return",
                "token": "do-not-return-either",
            }, now + 500),
            ("INFO", "audit-service", "canonical older", {
                "_audit": True, "actor": "old-admin", "action": "event.delete",
                "status": 204, "target": "record:1",
            }, now + 100),
        ], allow_audit=True)

        first = self.client.get("/api/audit?limit=1")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json["count"], 1)
        self.assertEqual(first.json["events"][0]["actor"], "new-admin")
        self.assertTrue(first.json["has_more"])
        self.assertNotIn("do-not-return", first.get_data(as_text=True))
        self.assertEqual(set(first.json["events"][0]), {
            "id", "ts", "actor", "action", "target", "status",
        })

        second = self.client.get("/api/audit?limit=1&cursor=" + first.json["next_cursor"])
        self.assertEqual(second.status_code, 200)
        self.assertEqual([row["actor"] for row in second.json["events"]], ["old-admin"])
        self.assertTrue(second.json["has_more"])
        final = self.client.get("/api/audit?limit=1&cursor=" + second.json["next_cursor"])
        self.assertEqual(final.status_code, 200)
        self.assertEqual(final.json["count"], 1)
        self.assertFalse(final.json["has_more"])

        store.put_user("viewer", {
            "password_hash": generate_password_hash("viewer-pass-123"),
            "role": "viewer", "enabled": True,
        })
        viewer = self.app.test_client()
        page = viewer.get("/login")
        response = viewer.post("/login", data={
            "csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1),
            "username": "viewer", "password": "viewer-pass-123",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(viewer.get("/api/audit").status_code, 403)
        self.assertEqual(viewer.get("/audit").status_code, 403)
        page = self.client.get("/audit")
        self.assertEqual(page.status_code, 200)
        self.assertIn("cùng cơ sở dữ liệu", page.get_data(as_text=True))

    def test_restore_stays_active_after_a_fresh_app_factory_start(self):
        token = self.csrf()
        self.client.post("/api/events", json={"level":"INFO", "source":"restore", "msg":"in backup"},
                         headers={"X-CSRF-Token":token})
        backup = self.client.post("/api/backup", headers={"X-CSRF-Token":token})
        self.client.post("/api/events", json={"level":"INFO", "source":"restore", "msg":"after backup"},
                         headers={"X-CSRF-Token":token})
        restored = self.client.post("/api/restore", json={"name":backup.json["name"]},
                                    headers={"X-CSRF-Token":token})
        self.assertEqual(restored.status_code, 200)

        state_path = self.app.config["STATE_PATH"]
        self.app.extensions["logstore"].close()
        with patch.dict(os.environ):
            for name in ("LOGSTORE_PATH", "LOGSTORE_BACKUP_PATH", "LOGSTORE_SECRET_KEY"):
                os.environ.pop(name, None)
            os.environ["LOGSTORE_STATE_PATH"] = state_path
            restarted = create_app()
        events = restarted.extensions["logstore"].query(source="restore", limit=10)
        self.assertEqual([event["msg"] for event in events], ["in backup"])
        self.assertEqual(restarted.config["LOGSTORE_PATH"], restored.json["active_path"])
        self.assertEqual(restarted.config["BACKUP_PATH"], self.app.config["BACKUP_PATH"])
        self.app.extensions["logstore"] = restarted.extensions["logstore"]

    def test_event_api_returns_disjoint_cursor_pages(self):
        self.app.extensions["logstore"].put_many([
            ("INFO", "page-test", str(i), {}, 1_700_000_000_000) for i in range(5)
        ])
        first = self.client.get("/api/events?source=page-test&limit=2").json
        second = self.client.get("/api/events?source=page-test&limit=2&cursor=" + first["next_cursor"]).json
        final = self.client.get("/api/events?source=page-test&limit=2&cursor=" + second["next_cursor"]).json

        self.assertEqual([row["msg"] for row in first["events"] + second["events"] + final["events"]],
                         ["4", "3", "2", "1", "0"])
        self.assertTrue(first["has_more"])
        self.assertFalse(final["has_more"])
        self.assertIsNone(final["next_cursor"])

    def test_authenticated_ingest_batches_records_without_browser_session(self):
        self.app.config["INGEST_TOKEN"] = "local-ingest-test-token"
        ingest_client = self.app.test_client()
        events = [
            {"source_event_id":"worker-1", "level":"INFO", "source":"worker", "msg":"started", "fields":{"trace_id":"batch-1"}},
            {"source_event_id":"worker-2", "level":"ERROR", "source":"worker", "msg":"failed", "fields":{"trace_id":"batch-1"}},
        ]

        denied = ingest_client.post("/api/ingest", json=events)
        accepted = ingest_client.post("/api/ingest", json=events,
                                      headers={"Authorization":"Bearer local-ingest-test-token"})
        replay = ingest_client.post("/api/ingest", json=events,
                                    headers={"Authorization":"Bearer local-ingest-test-token"})

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(accepted.json["written"], 2)
        self.assertEqual(accepted.json["duplicates"], 0)
        self.assertEqual(replay.status_code, 201)
        self.assertEqual(replay.json["written"], 0)
        self.assertEqual(replay.json["duplicates"], 2)
        self.assertEqual(replay.json["ids"], accepted.json["ids"])
        self.assertEqual(self.client.get("/api/events?trace_id=batch-1").json["count"], 2)

    def test_ingest_credentials_are_scoped_to_producer_and_replays_conflict_on_change(self):
        self.app.config["INGEST_TOKENS"] = {
            "collector-a": "collector-a-secret",
            "collector-b": "collector-b-secret",
        }
        ingest_client = self.app.test_client()
        event = {"source_event_id":"same-id", "level":"INFO", "source":"worker", "msg":"started"}

        first = ingest_client.post("/api/ingest", json=event,
                                   headers={"Authorization":"Bearer collector-a-secret"})
        other_producer = ingest_client.post("/api/ingest", json=event,
                                            headers={"Authorization":"Bearer collector-b-secret"})
        replay_with_changed_payload = ingest_client.post(
            "/api/ingest", json={**event, "msg":"changed"},
            headers={"Authorization":"Bearer collector-a-secret"},
        )
        wrong_token = ingest_client.post(
            "/api/ingest", json={**event, "source_event_id":"other-id"},
            headers={"Authorization":"Bearer unknown-secret"},
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(other_producer.status_code, 201)
        self.assertNotEqual(first.json["ids"], other_producer.json["ids"])
        self.assertEqual(replay_with_changed_payload.status_code, 409)
        self.assertEqual(wrong_token.status_code, 401)
        self.assertNotIn("collector-a-secret", wrong_token.get_data(as_text=True))
        self.assertEqual(self.client.get("/api/events?source=worker").json["count"], 2)

    def test_ingest_requires_a_source_event_id(self):
        self.app.config["INGEST_TOKEN"] = "local-ingest-test-token"
        before = self.app.extensions["logstore"].count()

        response = self.app.test_client().post(
            "/api/ingest", json={"level":"INFO", "source":"worker", "msg":"missing id"},
            headers={"Authorization":"Bearer local-ingest-test-token"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.app.extensions["logstore"].count(), before)

    def test_empty_ingest_token_is_never_accepted(self):
        self.app.config["INGEST_TOKEN"] = ""
        response = self.app.test_client().post(
            "/api/ingest", json={"source_event_id":"one", "level":"INFO", "source":"worker", "msg":"must reject"},
            headers={"Authorization":"Bearer "},
        )
        self.assertEqual(response.status_code, 401)

    def test_export_authentication_waits_for_restore_lifecycle_lock(self):
        lifecycle_lock = self.app.extensions["store_lifecycle_lock"]
        auth_checked = threading.Event()
        response_status = []
        client = self.app.test_client()
        with client.session_transaction() as browser_session:
            browser_session["username"] = "admin"

        def current_user():
            auth_checked.set()
            return {"username":"admin"}

        def request_export():
            with patch("webapp.auth.current_user", side_effect=current_user):
                response_status.append(client.get("/api/export").status_code)

        writer = lifecycle_lock.write()
        writer.__enter__()
        thread = threading.Thread(target=request_export)
        try:
            thread.start()
            self.assertFalse(auth_checked.wait(0.05))
        finally:
            writer.__exit__(None, None, None)
        thread.join(timeout=2)

        self.assertFalse(thread.is_alive())
        self.assertTrue(auth_checked.is_set())
        self.assertEqual(response_status, [200])

    def test_export_rejects_snapshot_that_grows_past_the_limit(self):
        store = self.app.extensions["logstore"]
        store.put_many([("INFO", "limit", str(i), {}, None) for i in range(5)])
        original_snapshot = store.snapshot_events

        def add_before_snapshot():
            store.put_event("INFO", "limit", "concurrent insert")
            return original_snapshot()

        with patch("webapp.api.MAX_EXPORT_EVENTS", 6), \
                patch.object(store, "snapshot_events", side_effect=add_before_snapshot):
            response = self.client.get("/api/export", buffered=True)

        self.assertEqual(response.status_code, 413)

    def test_export_keeps_one_snapshot_during_concurrent_changes(self):
        store = self.app.extensions["logstore"]
        ids = store.put_many([("INFO", "snapshot-export", str(i), {}, 1_700_000_000_000)
                              for i in range(5)])
        original_snapshot = store.snapshot_events
        mutated = False

        def make_snapshot():
            nonlocal mutated
            snapshot = original_snapshot()
            if not mutated:
                mutated = True
                store.delete_event(min(ids))
                store.put_event("INFO", "snapshot-export", "inserted during export", {}, 1_700_000_000_000)
            return snapshot

        with patch.object(store, "snapshot_events", side_effect=make_snapshot):
            response = self.client.get("/api/export", buffered=True)

        exported = [json.loads(line) for line in response.get_data(as_text=True).splitlines()]
        messages = [event["msg"] for event in exported if event["source"] == "snapshot-export"]
        self.assertEqual(set(messages), {str(i) for i in range(5)})
        self.assertEqual(len(messages), 5)

    def test_web_mutations_write_audit_events(self):
        token = self.csrf()
        created = self.client.post("/api/events", json={"level":"INFO", "source":"audit-test", "msg":"created"},
                                   headers={"X-CSRF-Token":token})

        audit = self.app.extensions["logstore"].query(source="audit-service", contains="event.create", limit=10)
        self.assertTrue(any(event["fields"].get("actor") == "admin" for event in audit))
        audit_id = next(event["id"] for event in audit if event["fields"].get("action") == "event.create")
        changed = self.client.put("/api/events/" + audit_id, json={"msg":"changed"},
                                  headers={"X-CSRF-Token":token})
        deleted = self.client.delete("/api/events/" + audit_id, headers={"X-CSRF-Token":token})

        self.assertEqual(created.status_code, 201)
        self.assertEqual(changed.status_code, 400)
        self.assertEqual(deleted.status_code, 400)

    def test_login_audit_uses_new_account_when_switching_users(self):
        store = self.app.extensions["logstore"]
        store.put_user("second", {
            "password_hash": generate_password_hash("second-user-pass"),
            "role":"viewer", "enabled":True,
        })
        response = self.client.post("/login", data={
            "csrf_token":self.csrf(), "username":"second", "password":"second-user-pass",
        })
        self.assertEqual(response.status_code, 302)
        audit = store.query(source="audit-service", contains="session.login", limit=10)
        login = next(event for event in audit if event["fields"].get("action") == "session.login")
        self.assertEqual(login["fields"]["actor"], "second")

    def test_read_request_does_not_wait_for_the_write_lock(self):
        lock = self.app.extensions["store_lock"]
        finished = threading.Event()
        response = []

        def read_stats():
            response.append(self.client.get("/api/stats").status_code)
            finished.set()

        lock.acquire()
        thread = threading.Thread(target=read_stats)
        try:
            thread.start()
            completed_while_write_lock_was_held = finished.wait(0.2)
        finally:
            lock.release()
        thread.join(timeout=2)

        self.assertTrue(completed_while_write_lock_was_held)
        self.assertEqual(response, [200])

    def test_import_is_one_write_and_export_uses_a_stream_iterator(self):
        token = self.csrf()
        records = [
            json.dumps({"level":"INFO", "source":"bulk-test", "msg":"row-%04d" % i})
            for i in range(1201)
        ]
        payload = ("\n".join(records) + "\n").encode("utf-8")
        store = self.app.extensions["logstore"]
        write_sizes = []
        original_write = store.put_many

        def write_batch(batch):
            write_sizes.append(len(batch))
            return original_write(batch)

        with patch.object(store, "put_many", side_effect=write_batch):
            imported = self.client.post("/api/import", data={"file": (BytesIO(payload), "bulk.jsonl")},
                                        headers={"X-CSRF-Token":token}, content_type="multipart/form-data")
        self.assertEqual(imported.status_code, 201)
        self.assertEqual(sum(write_sizes), 1201)
        self.assertEqual(write_sizes, [1201])

        with patch.object(store, "query", side_effect=AssertionError("export should use one snapshot iterator")):
            exported = self.client.get("/api/export", buffered=True)
        export_rows = [json.loads(line) for line in exported.get_data(as_text=True).splitlines()]
        self.assertEqual(sum(row["source"] == "bulk-test" for row in export_rows), 1201)

    def test_streamed_export_holds_store_lifecycle_lock_until_closed(self):
        lifecycle_lock = self.app.extensions["store_lifecycle_lock"]
        self.app.extensions["logstore"].put_event("INFO", "stream-test", "keep stream open")
        head_response = self.client.head("/api/export")
        self.assertEqual(lifecycle_lock.reader_count, 1)
        head_response.close()
        self.assertEqual(lifecycle_lock.reader_count, 0)

        response = self.client.get("/api/export", buffered=False)
        self.assertEqual(lifecycle_lock.reader_count, 1)
        response.close()
        self.assertEqual(lifecycle_lock.reader_count, 0)

        response = self.client.get("/api/export", buffered=False)
        iterator = iter(response.response)
        next(iterator)
        self.assertEqual(lifecycle_lock.reader_count, 1)

        response.close()

        self.assertEqual(lifecycle_lock.reader_count, 0)

    def test_store_lifecycle_lock_allows_parallel_reads_and_waits_for_restore(self):
        lifecycle_lock = self.app.extensions["store_lifecycle_lock"]
        release_first = lifecycle_lock.acquire_read()
        release_second = lifecycle_lock.acquire_read()
        writer_acquired = threading.Event()

        def restore_writer():
            with lifecycle_lock.write():
                writer_acquired.set()

        thread = threading.Thread(target=restore_writer)
        try:
            self.assertEqual(lifecycle_lock.reader_count, 2)
            thread.start()
            self.assertFalse(writer_acquired.wait(0.05))
        finally:
            release_first()
            release_second()
        self.assertTrue(writer_acquired.wait(1))
        thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
