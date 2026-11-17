import os
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

from webapp import create_app


class AppRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.apps = []

    def tearDown(self):
        for app in self.apps:
            app.extensions["logstore"].close()
        shutil.rmtree(self.root, ignore_errors=True)

    def create_app(self, name, **kwargs):
        app = create_app(
            db_path=os.path.join(self.root, name),
            backup_path=os.path.join(self.root, name + "-backups"),
            **kwargs,
        )
        self.apps.append(app)
        return app

    def test_liveness_and_readiness_are_available_without_login(self):
        app = self.create_app("healthy")
        client = app.test_client()

        live = client.get("/health/live")
        ready = client.get("/health/ready")

        self.assertEqual((live.status_code, live.json), (200, {"status": "live"}))
        self.assertEqual((ready.status_code, ready.json), (200, {"status": "ready"}))

    def test_readiness_fails_without_leaking_store_errors_but_liveness_remains_up(self):
        app = self.create_app("unready")
        client = app.test_client()
        with patch.object(app.extensions["logstore"], "ping", side_effect=OSError("secret path"), create=True), \
                patch.object(app.logger, "warning"):
            ready = client.get("/health/ready")
            live = client.get("/health/live")

        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json, {"status": "not_ready"})
        self.assertNotIn("secret path", ready.get_data(as_text=True))
        self.assertEqual(live.status_code, 200)

    def test_production_requires_a_persistent_secret_of_at_least_32_characters(self):
        for index, secret in enumerate(("", "short", " " * 40)):
            with self.subTest(secret_length=len(secret)), patch.dict(os.environ, {
                "LOGSTORE_ENV": "production", "LOGSTORE_SECRET_KEY": secret,
            }, clear=True):
                db_path = os.path.join(self.root, "weak-secret-%d" % index)
                try:
                    app = create_app(db_path=db_path, backup_path=db_path + "-backups")
                except RuntimeError as error:
                    self.assertIn("LOGSTORE_SECRET_KEY", str(error))
                else:
                    self.apps.append(app)
                    self.fail("production startup accepted a missing or weak session secret")
                self.assertFalse(os.path.exists(db_path), "configuration must fail before opening RocksDB")

    def test_invalid_secure_cookie_setting_is_rejected_before_opening_db(self):
        db_path = os.path.join(self.root, "invalid-cookie-setting")
        with patch.dict(os.environ, {"LOGSTORE_COOKIE_SECURE": "sometimes"}, clear=True):
            with self.assertRaisesRegex(ValueError, "LOGSTORE_COOKIE_SECURE"):
                create_app(db_path=db_path, backup_path=db_path + "-backups")
        self.assertFalse(os.path.exists(db_path))

    def test_production_uses_secure_session_cookies(self):
        with patch.dict(os.environ, {
            "LOGSTORE_ENV": "production", "LOGSTORE_SECRET_KEY": "s" * 48,
        }, clear=True):
            app = self.create_app("secure-cookie")

        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])

    def test_loopback_http_compose_can_explicitly_disable_secure_cookie(self):
        with patch.dict(os.environ, {
            "LOGSTORE_ENV": "production",
            "LOGSTORE_SECRET_KEY": "s" * 48,
            "LOGSTORE_COOKIE_SECURE": "false",
        }, clear=True):
            app = self.create_app("loopback-cookie")
        client = app.test_client()
        page = client.get("/setup")
        cookie = page.headers["Set-Cookie"]

        self.assertFalse(app.config["SESSION_COOKIE_SECURE"])
        self.assertNotIn("; Secure;", cookie)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)).group(1)
        response = client.post("/setup", data={
            "csrf_token": token,
            "username": "smoke-admin",
            "password": "smoke-test-password-123",
            "confirm_password": "smoke-test-password-123",
        })
        self.assertEqual(response.status_code, 302)

    def test_ingest_producer_credentials_load_from_environment_mapping(self):
        with patch.dict(os.environ, {
            "LOGSTORE_INGEST_TOKENS": '{"collector-a":"secret-a","collector-b":"secret-b"}',
        }, clear=True):
            app = self.create_app("ingest-token-map")

        self.assertEqual(app.config["INGEST_TOKENS"], {
            "collector-a": "secret-a", "collector-b": "secret-b",
        })
        self.assertIsNone(app.config["INGEST_TOKEN"])

    def test_ambiguous_ingest_token_environment_is_rejected_before_db_open(self):
        db_path = os.path.join(self.root, "ambiguous-ingest-tokens")
        with patch.dict(os.environ, {
            "LOGSTORE_INGEST_TOKENS": '{"collector-a":"secret-a"}',
            "LOGSTORE_INGEST_TOKEN": "legacy-secret",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "LOGSTORE_INGEST_TOKEN"):
                create_app(db_path=db_path, backup_path=db_path + "-backups")
        self.assertFalse(os.path.exists(db_path))

    def test_invalid_retention_settings_are_rejected_before_db_open(self):
        db_path = os.path.join(self.root, "invalid-retention-setting")
        with patch.dict(os.environ, {"LOGSTORE_RETENTION_HOURS": "forever"}, clear=True):
            with self.assertRaisesRegex(ValueError, "LOGSTORE_RETENTION_HOURS"):
                create_app(db_path=db_path, backup_path=db_path + "-backups")
        self.assertFalse(os.path.exists(db_path))
