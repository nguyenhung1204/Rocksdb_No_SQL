import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from logstore import LogStore
from werkzeug.security import generate_password_hash


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
CLI_PATH = os.path.join(PROJECT_ROOT, "cli.py")


class CliExchangeTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.db = os.path.join(self.root, "db")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, CLI_PATH, "--db", self.db, *args],
            cwd=PROJECT_ROOT, text=True, capture_output=True,
        )

    def test_import_export_round_trip(self):
        source = os.path.join(self.root, "input.jsonl")
        output = os.path.join(self.root, "output.jsonl")
        with open(source, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({"level":"INFO","source":"api","msg":"one","ts":1700000000000}) + "\n")
            stream.write(json.dumps({"level":"ERROR","source":"worker","msg":"two","fields":{"trace_id":"t2"},"ts":1700000000001}) + "\n")
        imported = self.run_cli("import", source)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertIn("2", imported.stdout)
        exported = self.run_cli("export", output)
        self.assertEqual(exported.returncode, 0, exported.stderr)
        with open(output, encoding="utf-8") as stream:
            rows = [json.loads(line) for line in stream]
        self.assertEqual({row["msg"] for row in rows}, {"one", "two"})

    def test_invalid_import_does_not_write_partial_rows(self):
        source = os.path.join(self.root, "invalid.jsonl")
        with open(source, "w", encoding="utf-8") as stream:
            stream.write('{"level":"INFO","source":"api","msg":"would be valid"}\n')
            stream.write('{"level":"LOUD","source":"api","msg":"invalid"}\n')
        result = self.run_cli("import", source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("level phai thuoc", result.stderr)
        with LogStore(self.db) as store:
            self.assertEqual(store.count(), 0)

    def test_restore_command_creates_database_at_new_path(self):
        with LogStore(self.db) as store:
            store.put_event("INFO", "api", "saved", ts_ms=1700000000000)
            backup = os.path.join(self.root, "backup")
            store.backup(backup)
        target = os.path.join(self.root, "restored")
        result = self.run_cli("restore", backup, target)
        self.assertEqual(result.returncode, 0, result.stderr)
        with LogStore(target) as restored:
            self.assertEqual(restored.query(contains="saved")[0]["msg"], "saved")

    def test_zero_retention_is_rejected_without_deleting_events(self):
        with LogStore(self.db) as store:
            store.put_event("INFO", "api", "keep", ts_ms=1700000000000)
        result = self.run_cli("purge", "--older-than", "0h")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lon hon 0", result.stderr)
        with LogStore(self.db) as store:
            self.assertEqual(store.count(), 1)

    def test_query_can_filter_by_trace_id(self):
        result = self.run_cli("add", "INFO", "api", "trace example", "--fields", '{"trace_id":"trace-a"}')
        self.assertEqual(result.returncode, 0, result.stderr)
        query = self.run_cli("query", "--trace-id", "trace-a", "--json")
        self.assertEqual(query.returncode, 0, query.stderr)
        self.assertEqual(json.loads(query.stdout)["fields"]["trace_id"], "trace-a")

    def test_seed_demo_cli_adds_profiles_and_workflows_idempotently(self):
        with LogStore(self.db) as store:
            store.put_user("admin", {
                "password_hash": generate_password_hash("strong-admin-pass"),
                "role": "admin", "enabled": True,
            })
        seeded = self.run_cli("seed-demo", "--password", "Demo-temp-pass-123")
        self.assertEqual(seeded.returncode, 0, seeded.stderr)
        self.assertIn("4 tai khoan mau va 32 su kien", seeded.stdout)
        again = self.run_cli("seed-demo", "--password", "Demo-temp-pass-123")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("0 tai khoan mau va 0 su kien", again.stdout)
        with LogStore(self.db) as store:
            self.assertEqual(store.count(), 32)
            self.assertEqual(len(store.list_users()), 5)


if __name__ == "__main__":
    unittest.main()
