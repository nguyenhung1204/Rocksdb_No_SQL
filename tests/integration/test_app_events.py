import json
import os
import re
import shutil
import tempfile
import unittest
from io import BytesIO

from app import create_app


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
        invalid = b'{"level":"INFO","source":"a","msg":"good"}\n{"level":"LOUD","source":"a","msg":"bad"}\n'
        response = self.client.post("/api/import", data={"file": (BytesIO(invalid), "invalid.jsonl")},
                                    headers={"X-CSRF-Token":token}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/stats").json["total_events"], 0)

        valid = b'{"level":"INFO","source":"a","msg":"one","fields":{"trace_id":"t1"},"ts":1700000000000}\n{"level":"WARN","source":"b","msg":"two","ts":1700000000001}\n'
        imported = self.client.post("/api/import", data={"file": (BytesIO(valid), "events.jsonl")},
                                    headers={"X-CSRF-Token":token}, content_type="multipart/form-data")
        self.assertEqual(imported.status_code, 201)
        exported = self.client.get("/api/export")
        rows = [json.loads(line) for line in exported.get_data(as_text=True).splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["msg"] for row in rows}, {"one", "two"})

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


if __name__ == "__main__":
    unittest.main()
