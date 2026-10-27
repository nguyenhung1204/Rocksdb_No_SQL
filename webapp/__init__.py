"""Flask application factory for the local RocksDB demo."""

import os
import secrets
import threading
from pathlib import Path

from flask import Flask, g, jsonify, request, session
from werkzeug.exceptions import RequestEntityTooLarge

from logstore import LogStore


def create_app(db_path=None, backup_path=None, secret_key=None):
    db_path = Path(db_path or os.environ.get("LOGSTORE_PATH", "data/webdb")).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = Path(backup_path or os.environ.get("LOGSTORE_BACKUP_PATH", db_path.parent / "backups")).resolve()
    backup_path.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, template_folder="../templates")
    app.secret_key = secret_key or os.environ.get("LOGSTORE_SECRET_KEY") or secrets.token_urlsafe(32)
    app.config.update(
        LOGSTORE_PATH=str(db_path),
        BACKUP_PATH=str(backup_path),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    app.extensions["logstore"] = LogStore(str(db_path))
    app.extensions["store_lock"] = threading.RLock()
    app.extensions["account_lock"] = threading.RLock()

    @app.before_request
    def prepare_request():
        session.setdefault("_csrf", secrets.token_urlsafe(32))
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            if not supplied or not secrets.compare_digest(session.get("_csrf", ""), supplied):
                return jsonify({"error": "CSRF token khong hop le"}), 400
        lock = app.extensions["store_lock"]
        lock.acquire()
        g.store_lock_acquired = True

    @app.teardown_request
    def release_store_lock(_error=None):
        if getattr(g, "store_lock_acquired", False):
            g.store_lock_acquired = False
            app.extensions["store_lock"].release()

    @app.errorhandler(ValueError)
    def invalid_request(error):
        return jsonify({"error": str(error)}), 400

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return jsonify({"error": "Yeu cau vuot qua gioi han 10 MB"}), 413

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
