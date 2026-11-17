"""Flask application factory for the local RocksDB demo."""

import json
import os
import secrets
import threading
from contextlib import contextmanager
from pathlib import Path

from flask import Flask, g, jsonify, request, session
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from logstore import LogStore
from .maintenance import retention_settings
from .login_throttle import LoginThrottle


# Setup/login ghi audit qua g.audit_event de khong ghi nham redirect hoac lan that bai.
EXPLICIT_AUDIT_ENDPOINTS = {"auth.setup", "auth.login"}


class StoreLifecycleLock:
    """Allow parallel readers while restore waits before closing the active DB."""

    def __init__(self):
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    @property
    def reader_count(self):
        with self._condition:
            return self._readers

    def acquire_read(self):
        with self._condition:
            while self._writer or self._waiting_writers:
                self._condition.wait()
            self._readers += 1
        released = False

        def release():
            nonlocal released
            with self._condition:
                if not released:
                    released = True
                    self._readers -= 1
                    if self._readers == 0:
                        self._condition.notify_all()

        return release

    @contextmanager
    def write(self):
        with self._condition:
            self._waiting_writers += 1
            try:
                while self._writer or self._readers:
                    self._condition.wait()
                self._writer = True
            finally:
                self._waiting_writers -= 1
                if not self._waiting_writers:
                    self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._writer = False
                self._condition.notify_all()


def _cookie_secure_setting(runtime_environment):
    configured = os.environ.get("LOGSTORE_COOKIE_SECURE")
    if configured is None:
        return runtime_environment == "production"
    value = configured.strip().lower()
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    raise ValueError("LOGSTORE_COOKIE_SECURE phai la true hoac false")


def _ingest_credentials_from_environment():
    raw_mapping = os.environ.get("LOGSTORE_INGEST_TOKENS")
    legacy_token = os.environ.get("LOGSTORE_INGEST_TOKEN")
    if raw_mapping is not None and legacy_token and legacy_token.strip():
        raise ValueError("chi duoc cau hinh LOGSTORE_INGEST_TOKENS hoac LOGSTORE_INGEST_TOKEN")
    if raw_mapping is None:
        return {}, legacy_token
    try:
        mapping = json.loads(raw_mapping)
    except json.JSONDecodeError as error:
        raise ValueError("LOGSTORE_INGEST_TOKENS phai la JSON object hop le") from error
    if not isinstance(mapping, dict) or not mapping or len(mapping) > 128:
        raise ValueError("LOGSTORE_INGEST_TOKENS phai la object co 1-128 producer")
    if any(not isinstance(name, str) or not name.strip() or len(name) > 128 or "\x00" in name
           or not isinstance(token, str) or not token.strip() for name, token in mapping.items()):
        raise ValueError("ten producer va token ingest khong duoc rong hoac vuot gioi han")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("token trong LOGSTORE_INGEST_TOKENS phai duy nhat")
    return mapping, None


def create_app(db_path=None, backup_path=None, secret_key=None):
    runtime_environment = os.environ.get("LOGSTORE_ENV", "development").strip().lower()
    configured_secret = secret_key if secret_key is not None else os.environ.get("LOGSTORE_SECRET_KEY")
    if runtime_environment == "production" and (
        not isinstance(configured_secret, str) or len(configured_secret.strip()) < 32
    ):
        raise RuntimeError("LOGSTORE_SECRET_KEY phai co it nhat 32 ky tu trong production")
    secure_cookie = _cookie_secure_setting(runtime_environment)
    ingest_tokens, legacy_ingest_token = _ingest_credentials_from_environment()
    maintenance = retention_settings()

    explicit_db_path = db_path is not None or bool(os.environ.get("LOGSTORE_PATH"))
    db_path = Path(db_path or os.environ.get("LOGSTORE_PATH", "data/webdb")).resolve()
    state_path = Path(os.environ.get("LOGSTORE_STATE_PATH", db_path.parent / ".logstore-state.json")).resolve()
    saved_backup_path = None
    if not explicit_db_path and state_path.is_file():
        try:
            saved_state = json.loads(state_path.read_text(encoding="utf-8"))
            active_path = Path(saved_state["db_path"]).resolve()
            if (active_path / "CURRENT").is_file():
                db_path = active_path
                saved_backup_path = saved_state.get("backup_path")
        except (OSError, ValueError, KeyError, TypeError):
            pass
    db_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = Path(backup_path or os.environ.get("LOGSTORE_BACKUP_PATH") or
                       saved_backup_path or db_path.parent / "backups").resolve()
    backup_path.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, template_folder="../templates")
    app.secret_key = configured_secret or secrets.token_urlsafe(32)
    app.config.update(
        LOGSTORE_PATH=str(db_path),
        BACKUP_PATH=str(backup_path),
        STATE_PATH=str(state_path),
        INGEST_TOKEN=legacy_ingest_token,
        INGEST_TOKENS=ingest_tokens,
        RETENTION_HOURS=maintenance["hours"],
        RETENTION_INTERVAL_SECONDS=maintenance["interval_seconds"],
        BACKUP_RETENTION_COUNT=maintenance["backup_count"],
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=secure_cookie,
    )
    app.extensions["logstore"] = LogStore(str(db_path))
    app.extensions["store_lock"] = threading.RLock()
    app.extensions["store_lifecycle_lock"] = StoreLifecycleLock()
    app.extensions["account_lock"] = threading.RLock()
    app.extensions["login_throttle"] = LoginThrottle()

    def persist_active_paths(active_path, active_backup_path):
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = state_path.with_name(state_path.name + ".tmp")
        temporary.write_text(json.dumps({
            "db_path": str(Path(active_path).resolve()),
            "backup_path": str(Path(active_backup_path).resolve()),
        }, indent=2), encoding="utf-8")
        os.replace(temporary, state_path)

    app.extensions["persist_active_paths"] = persist_active_paths

    @app.get("/health/live")
    def health_live():
        return jsonify({"status": "live"})

    @app.get("/health/ready")
    def health_ready():
        try:
            app.extensions["logstore"].ping()
        except Exception:
            app.logger.warning("RocksDB readiness check failed", exc_info=True)
            return jsonify({"status": "not_ready"}), 503
        return jsonify({"status": "ready"})

    @app.before_request
    def prepare_request():
        session.setdefault("_csrf", secrets.token_urlsafe(32))
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.endpoint != "api.ingest":
            supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not supplied or not secrets.compare_digest(session.get("_csrf", ""), supplied):
                return jsonify({"error": "CSRF token khong hop le"}), 400
        g.audit_actor = session.get("username")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            lock = app.extensions["store_lock"]
            lock.acquire()
            g.store_lock_acquired = True
        elif request.method in {"GET", "HEAD"}:
            g.store_read_release = app.extensions["store_lifecycle_lock"].acquire_read()

    @app.after_request
    def record_successful_mutation(response):
        action_by_endpoint = {
            "auth.setup": "admin.setup",
            "auth.login": "session.login",
            "auth.logout": "session.logout",
            "auth.users_create": "user.create",
            "auth.users_update": "user.update",
            "auth.users_delete": "user.delete",
            "api.events": "event.create",
            "api.update_event": "event.update",
            "api.delete_event": "event.delete",
            "api.purge": "event.retention",
            "api.simulate": "event.generate",
            "api.seed_demo": "demo.seed",
            "api.import_jsonl": "event.import",
            "api.create_backup": "backup.create",
            "api.restore_backup": "backup.restore",
            "api.ingest": "event.ingest",
        }
        explicit = getattr(g, "audit_event", None)
        if explicit is not None:
            # View tu quyet dinh: chi ghi khi thao tac that su xay ra (setup/login).
            action, actor, target, status = explicit
        else:
            action = action_by_endpoint.get(request.endpoint) if request.method in {"POST", "PUT", "PATCH", "DELETE"} else None
            if request.endpoint in EXPLICIT_AUDIT_ENDPOINTS or not action or not 200 <= response.status_code < 400:
                return response
            actor = getattr(g, "audit_actor", None) or session.get("username") or "anonymous"
            target = next(iter((request.view_args or {}).values()), None)
            status = response.status_code
        try:
            app.extensions["logstore"].put_audit(actor, action, target, status)
        except Exception:
            app.logger.exception("Could not write audit event for %s", action)
        return response

    @app.teardown_request
    def release_store_lock(_error=None):
        if getattr(g, "store_lock_acquired", False):
            g.store_lock_acquired = False
            app.extensions["store_lock"].release()
        release_read = getattr(g, "store_read_release", None)
        if release_read:
            g.store_read_release = None
            release_read()

    @app.errorhandler(ValueError)
    def invalid_request(error):
        return jsonify({"error": str(error)}), 400

    from logstore.store import IdempotencyConflict

    @app.errorhandler(IdempotencyConflict)
    def idempotency_conflict(error):
        return jsonify({"error": str(error)}), 409

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return jsonify({"error": "Yeu cau vuot qua gioi han 10 MB"}), 413

    @app.errorhandler(Exception)
    def unexpected_error(error):
        if isinstance(error, HTTPException):
            return error
        app.logger.exception("Unhandled error on %s %s", request.method, request.path)
        if request.path.startswith("/api/"):
            return jsonify({"error": "loi he thong; xem log ung dung"}), 500
        return "Loi he thong", 500

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "khong tim thay"}), 404
        return "Khong tim thay trang", 404

    from .auth import auth_bp
    from .api import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    return app
