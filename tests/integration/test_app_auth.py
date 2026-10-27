import os
import re
import shutil
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from app import create_app


class AppAuthTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.app = create_app(
            db_path=os.path.join(self.root, "db"),
            backup_path=os.path.join(self.root, "backups"),
            secret_key="unit-test-secret",
        )
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["logstore"].close()
        shutil.rmtree(self.root, ignore_errors=True)

    def csrf_from(self, response):
        match = re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True))
        self.assertIsNotNone(match, "form should include a CSRF token")
        return match.group(1)

    def create_admin(self):
        response = self.client.get("/setup")
        token = self.csrf_from(response)
        return self.client.post("/setup", data={
            "csrf_token": token,
            "username": "admin",
            "password": "strong-pass-123",
            "confirm_password": "strong-pass-123",
        })

    def test_first_run_setup_creates_admin_once(self):
        response = self.create_admin()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.app.extensions["logstore"].get_user("admin")["role"], "admin")
        self.assertNotEqual(
            self.app.extensions["logstore"].get_user("admin")["password_hash"],
            "strong-pass-123",
        )
        self.assertEqual(self.client.get("/setup").status_code, 302)

    def test_admin_dashboard_exposes_demo_operations(self):
        self.create_admin()
        response = self.client.get("/")
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        for label in ("Ghi sự kiện", "Trace ID", "Xuất JSONL", "Tạo bản sao lưu", "Khôi phục bản sao lưu",
                      "Trang trước", "Trang tiếp", "columnFamilies"):
            self.assertIn(label, page)

    def test_viewer_can_query_but_cannot_mutate(self):
        self.create_admin()
        self.app.extensions["logstore"].put_user("viewer", {
            "password_hash": generate_password_hash("viewer-pass-123"),
            "role": "viewer",
            "enabled": True,
        })
        csrf = self.client.get("/api/session").json["csrf_token"]
        self.client.post("/logout", headers={"X-CSRF-Token": csrf})
        page = self.client.get("/login")
        response = self.client.post("/login", data={
            "csrf_token": self.csrf_from(page),
            "username": "viewer",
            "password": "viewer-pass-123",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/api/events").status_code, 200)
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("Tra cứu sự kiện", page)
        self.assertNotIn("Ghi sự kiện</h2>", page)
        self.assertNotIn("Khôi phục bản sao lưu", page)
        csrf = self.client.get("/api/session").json["csrf_token"]
        self.assertEqual(self.client.post("/api/demo/seed", json={"password": "Demo-temp-pass-123"},
                                          headers={"X-CSRF-Token": csrf}).status_code, 403)
        denied = self.client.post("/api/events", json={
            "level": "INFO", "source": "demo", "msg": "blocked",
        }, headers={"X-CSRF-Token": csrf})
        self.assertEqual(denied.status_code, 403)

    def test_mutating_api_requires_csrf_token(self):
        self.create_admin()
        response = self.client.post("/api/events", json={
            "level": "INFO", "source": "demo", "msg": "blocked",
        })
        self.assertEqual(response.status_code, 400)

    def test_admin_can_create_update_and_delete_users_without_exposing_hash(self):
        self.create_admin()
        csrf = self.client.get("/api/session").json["csrf_token"]
        created = self.client.post("/api/users", json={
            "username": "operator1", "password": "operator-pass-123", "role": "operator",
            "full_name": "Nguyễn Văn Demo", "email": "demo@example.test", "department": "Vận hành",
        }, headers={"X-CSRF-Token": csrf})
        self.assertEqual(created.status_code, 201)
        listed = self.client.get("/api/users").json["users"]
        created_user = next(user for user in listed if user["username"] == "operator1")
        self.assertNotIn("password_hash", created_user)
        self.assertEqual(created_user["full_name"], "Nguyễn Văn Demo")
        self.assertEqual(created_user["department"], "Vận hành")
        page = self.client.get("/users").get_data(as_text=True)
        self.assertIn("Nạp dữ liệu người dùng và nghiệp vụ mẫu", page)
        self.assertIn("Nhân sự", page)
        updated = self.client.put("/api/users/operator1", json={"enabled": False},
                                  headers={"X-CSRF-Token": csrf})
        self.assertEqual(updated.status_code, 200)
        deleted = self.client.delete("/api/users/operator1", headers={"X-CSRF-Token": csrf})
        self.assertEqual(deleted.status_code, 200)

    def test_admin_can_seed_realistic_users_and_correlated_events(self):
        self.create_admin()
        csrf = self.client.get("/api/session").json["csrf_token"]
        seeded = self.client.post("/api/demo/seed", json={"password": "Demo-temp-pass-123"},
                                  headers={"X-CSRF-Token": csrf})
        self.assertEqual(seeded.status_code, 201)
        self.assertEqual(seeded.json["users_created"], 4)
        self.assertGreaterEqual(seeded.json["events_written"], 30)
        self.assertEqual(self.app.extensions["logstore"].get_user("admin")["role"], "admin")
        users = self.client.get("/api/users").json["users"]
        lan = next(user for user in users if user["username"] == "lan.nguyen")
        self.assertEqual(lan["full_name"], "Nguyễn Thị Lan")
        self.assertNotIn("password_hash", lan)
        self.assertIn("user.full_name", self.client.get("/users").get_data(as_text=True))
        events = self.client.get("/api/events?trace_id=DEMO-ORDER-001").json["events"]
        self.assertGreaterEqual(len(events), 5)
        self.assertTrue(all(event["fields"]["trace_id"] == "DEMO-ORDER-001" for event in events))
        again = self.client.post("/api/demo/seed", json={"password": "Ignored-on-repeat-123"},
                                 headers={"X-CSRF-Token": csrf})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json["users_created"], 0)
        self.assertEqual(again.json["events_written"], 0)

    def test_demo_seed_requires_a_valid_password(self):
        self.create_admin()
        csrf = self.client.get("/api/session").json["csrf_token"]
        response = self.client.post("/api/demo/seed", json={"password": "short"},
                                    headers={"X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 400)

    def test_invalid_role_type_returns_bad_request(self):
        self.create_admin()
        csrf = self.client.get("/api/session").json["csrf_token"]
        response = self.client.post("/api/users", json={
            "username": "user1", "password": "valid-pass-123", "role": [],
        }, headers={"X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 400)

    def test_invalid_email_is_rejected_for_new_users(self):
        self.create_admin()
        csrf = self.client.get("/api/session").json["csrf_token"]
        response = self.client.post("/api/users", json={
            "username": "user1", "password": "valid-pass-123", "role": "viewer",
            "email": "not-an-email",
        }, headers={"X-CSRF-Token": csrf})
        self.assertEqual(response.status_code, 400)

    def test_last_admin_cannot_demote_or_delete_self(self):
        self.create_admin()
        store = self.app.extensions["logstore"]
        store.put_user("admin2", {
            "password_hash": generate_password_hash("another-pass-123"),
            "role": "admin", "enabled": True,
        })
        csrf = self.client.get("/api/session").json["csrf_token"]
        self.client.post("/logout", headers={"X-CSRF-Token": csrf})
        page = self.client.get("/login")
        response = self.client.post("/login", data={
            "csrf_token": self.csrf_from(page), "username": "admin2", "password": "another-pass-123",
        })
        self.assertEqual(response.status_code, 302)
        csrf = self.client.get("/api/session").json["csrf_token"]
        demoted_other = self.client.put("/api/users/admin", json={"role": "viewer"},
                                        headers={"X-CSRF-Token": csrf})
        self.assertEqual(demoted_other.status_code, 200)
        removed_other = self.client.delete("/api/users/admin", headers={"X-CSRF-Token": csrf})
        self.assertEqual(removed_other.status_code, 200)
        csrf = self.client.get("/api/session").json["csrf_token"]
        demoted = self.client.put("/api/users/admin2", json={"role": "viewer"},
                                  headers={"X-CSRF-Token": csrf})
        self.assertEqual(demoted.status_code, 400)
        removed = self.client.delete("/api/users/admin2", headers={"X-CSRF-Token": csrf})
        self.assertEqual(removed.status_code, 400)


if __name__ == "__main__":
    unittest.main()
