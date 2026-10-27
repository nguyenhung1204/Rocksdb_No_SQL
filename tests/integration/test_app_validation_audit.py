import os
import re
import shutil
import tempfile
import time
import unittest

from app import create_app
from logstore.store import AUDIT_SOURCE, MAX_TS_MS, is_internal_audit_event


class AppValidationAndAuditTest(unittest.TestCase):
    """Regression tests for input bounds and audit records of setup/login."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.app = create_app(
            db_path=os.path.join(self.root, "db"),
            backup_path=os.path.join(self.root, "backups"),
            secret_key="unit-test-secret",
        )
        self.app.config.update(TESTING=True)
        self.store = self.app.extensions["logstore"]
        self.client = self.app.test_client()
        token = self.csrf_from(self.client.get("/setup"))
        self.client.post("/setup", data={
            "csrf_token": token, "username": "admin",
            "password": "strong-pass-123", "confirm_password": "strong-pass-123",
        })
        self.headers = {"X-CSRF-Token": self.client.get("/api/session").json["csrf_token"]}

    def tearDown(self):
        self.app.extensions["logstore"].close()
        shutil.rmtree(self.root, ignore_errors=True)

    def csrf_from(self, response):
        return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)).group(1)

    def audit_actions(self):
        return [(event["fields"]["actor"], event["fields"]["action"], event["fields"]["status"])
                for event in self.store.query(source=AUDIT_SOURCE, limit=100, reverse=False)
                if is_internal_audit_event(event)]

    def post_event(self, **record):
        body = {"level": "INFO", "source": "api", "msg": "m", **record}
        return self.client.post("/api/events", json=body, headers=self.headers)

    def test_far_future_or_unbounded_timestamp_is_rejected(self):
        for ts in (2 ** 64 - 1, MAX_TS_MS, int(time.time() * 1000) + 2 * 86_400_000):
            response = self.post_event(ts=ts)
            self.assertEqual(response.status_code, 400, ts)
        self.assertEqual(self.post_event(ts=int(time.time() * 1000) + 3_600_000).status_code, 201)

    def test_every_stored_event_is_visible_to_unfiltered_query(self):
        self.post_event(ts=int(time.time() * 1000) + 3_600_000)
        self.post_event(ts=0)
        total = self.client.get("/api/stats").json["total_events"]
        visible = self.client.get("/api/events?limit=1000").json["count"]
        self.assertEqual(total, visible)

    def test_huge_query_bounds_return_400_json_instead_of_500(self):
        for query in ("end=18446744073709551615", "start=99999999999999999999", "end=-1"):
            response = self.client.get("/api/events?" + query)
            self.assertEqual(response.status_code, 400, query)
            self.assertIn("error", response.json)
        self.assertEqual(self.client.get("/api/events?start=0&end=%d" % MAX_TS_MS).status_code, 200)

    def test_source_and_trace_longer_than_query_limit_are_rejected_on_write(self):
        self.assertEqual(self.post_event(source="s" * 257).status_code, 400)
        self.assertEqual(self.post_event(fields={"trace_id": "t" * 257}).status_code, 400)
        self.assertEqual(self.post_event(source="s" * 256, fields={"trace_id": "t" * 256}).status_code, 201)
        found = self.client.get("/api/events?source=" + "s" * 256 + "&trace_id=" + "t" * 256)
        self.assertEqual(found.json["count"], 1)

    def test_unexpected_api_error_returns_json(self):
        def broken(*_args, **_kwargs):
            raise RuntimeError("boom")

        self.store.query = broken
        response = self.client.get("/api/events")
        self.assertEqual(response.status_code, 500)
        self.assertIn("error", response.json)

    def test_hide_audit_filters_system_audit_records(self):
        self.post_event(msg="business")
        all_rows = self.client.get("/api/events").json["events"]
        hidden = self.client.get("/api/events?hide_audit=1").json["events"]
        self.assertTrue(any(row["source"] == AUDIT_SOURCE for row in all_rows))
        self.assertEqual([row["msg"] for row in hidden], ["business"])

    def test_anonymous_setup_attempt_after_initialisation_is_not_audited(self):
        before = self.audit_actions()
        anonymous = self.app.test_client()
        token = self.csrf_from(anonymous.get("/login"))
        for _ in range(3):
            response = anonymous.post("/setup", data={"csrf_token": token, "username": "evil", "password": "x"})
            self.assertEqual(response.status_code, 302)
        self.assertEqual(self.audit_actions(), before)
        self.assertEqual(before[0], ("admin", "admin.setup", 302))

    def test_login_success_and_failure_are_audited_without_throttled_attempts(self):
        client = self.app.test_client()
        token = self.csrf_from(client.get("/login"))
        for _ in range(7):
            client.post("/login", data={"csrf_token": token, "username": "admin", "password": "wrong-password"})
        actions = self.audit_actions()
        failed = [row for row in actions if row[1] == "session.login_failed"]
        self.assertEqual(failed, [("anonymous", "session.login_failed", 401)] * 5)
        target = [event["fields"].get("target") for event in self.store.query(source=AUDIT_SOURCE, limit=100)
                  if event["fields"].get("action") == "session.login_failed"]
        self.assertEqual(set(target), {"admin"})

        fresh = self.app.test_client()
        token = self.csrf_from(fresh.get("/login"))
        self.app.extensions["login_throttle"].clear_account("admin")
        self.app.extensions["login_throttle"]._entries.clear()
        response = fresh.post("/login", data={"csrf_token": token, "username": "admin",
                                              "password": "strong-pass-123"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.audit_actions()[-1], ("admin", "session.login", 302))

    def test_ingest_audit_names_the_authenticated_producer(self):
        self.app.config["INGEST_TOKENS"] = {"checkout-service": "token-for-checkout"}
        response = self.client.post("/api/ingest", json={
            "source_event_id": "e-1", "level": "INFO", "source": "checkout", "msg": "paid",
        }, headers={"Authorization": "Bearer token-for-checkout"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.audit_actions()[-1], ("ingest:checkout-service", "event.ingest", 201))


if __name__ == "__main__":
    unittest.main()
