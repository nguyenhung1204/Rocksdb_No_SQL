"""Login, first-run setup, and account administration routes."""

import re
from functools import wraps

from flask import Blueprint, current_app, jsonify, make_response, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


auth_bp = Blueprint("auth", __name__)
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
ROLES = {"admin", "operator", "viewer"}
MIN_PASSWORD_LENGTH = 10


def get_store():
    return current_app.extensions["logstore"]


def current_user():
    username = session.get("username")
    if not username:
        return None
    user = get_store().get_user(username)
    if not user or not user.get("enabled", True):
        session.pop("username", None)
        return None
    return {"username": username, **user}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "can dang nhap"}), 401
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "can dang nhap"}), 401
                return redirect(url_for("auth.login"))
            if user["role"] not in roles:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "khong du quyen"}), 403
                return "Khong du quyen", 403
            return view(*args, **kwargs)
        return wrapped
    return decorate


def _valid_username(username):
    return isinstance(username, str) and USERNAME_RE.fullmatch(username.strip()) is not None


def _password_error(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return "mat khau phai co it nhat 10 ky tu"
    return None


def _profile_fields(body):
    profile = {}
    for key, maximum in (("full_name", 80), ("email", 120), ("department", 80)):
        if key not in body:
            continue
        value = body[key]
        if not isinstance(value, str):
            raise ValueError("%s phai la chuoi" % key)
        value = value.strip()
        if len(value) > maximum:
            raise ValueError("%s toi da %d ky tu" % (key, maximum))
        if key == "email" and value and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("email khong hop le")
        if value:
            profile[key] = value
    return profile


@auth_bp.get("/")
def index():
    if not get_store().list_users():
        return redirect(url_for("auth.setup"))
    user = current_user()
    if user is None:
        return redirect(url_for("auth.login"))
    return render_template("index.html", levels=("DEBUG", "INFO", "WARN", "ERROR", "FATAL"), user=user)


@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    store = get_store()
    if store.list_users():
        return redirect(url_for("auth.login"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        confirmation = request.form.get("confirm_password", "")
        if not _valid_username(username):
            error = "ten dang nhap can 3-32 ky tu: chu, so, cham, gach duoi hoac gach ngang"
        elif _password_error(password):
            error = _password_error(password)
        elif password != confirmation:
            error = "mat khau xac nhan khong khop"
        else:
            with current_app.extensions["account_lock"]:
                if store.list_users():
                    return redirect(url_for("auth.login"))
                store.put_user(username, {
                    "password_hash": generate_password_hash(password),
                    "role": "admin",
                    "enabled": True,
                })
            session.clear()
            session["username"] = username
            return redirect(url_for("auth.index"))
    return render_template("setup.html", error=error, csrf_token=session["_csrf"]), 400 if error else 200


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if not get_store().list_users():
        return redirect(url_for("auth.setup"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        throttle = current_app.extensions["login_throttle"]
        client_address = request.remote_addr or "unknown"
        retry_after = throttle.retry_after(username, client_address)
        if retry_after:
            response = make_response(render_template(
                "login.html", error="Dang nhap tam thoi bi gioi han; hay thu lai sau.",
                csrf_token=session["_csrf"],
            ), 429)
            response.headers["Retry-After"] = str(retry_after)
            return response
        user = get_store().get_user(username)
        if not user or not user.get("enabled", True) or not check_password_hash(user["password_hash"], password):
            throttle.record_failure(username, client_address)
            error = "ten dang nhap hoac mat khau khong dung"
        else:
            throttle.clear_account(username)
            session.clear()
            session["username"] = username
            return redirect(url_for("auth.index"))
    return render_template("login.html", error=error, csrf_token=session["_csrf"]), 401 if error else 200


@auth_bp.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth_bp.get("/users")
@roles_required("admin")
def users_page():
    return render_template("users.html", users=get_store().list_users(), user=current_user())


@auth_bp.get("/api/users")
@roles_required("admin")
def users_list():
    users = [{k: v for k, v in row.items() if k != "password_hash"} for row in get_store().list_users()]
    return jsonify({"users": users})


@auth_bp.post("/api/users")
@roles_required("admin")
def users_create():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("body phai la object JSON")
    username = body.get("username", "")
    password = body.get("password", "")
    role = body.get("role", "viewer")
    profile = _profile_fields(body)
    if not _valid_username(username):
        raise ValueError("ten dang nhap can 3-32 ky tu hop le")
    username = username.strip().lower()
    error = _password_error(password)
    if error:
        raise ValueError(error)
    if not isinstance(role, str) or role not in ROLES:
        raise ValueError("role phai la admin, operator hoac viewer")
    store = get_store()
    with current_app.extensions["account_lock"]:
        if store.get_user(username):
            raise ValueError("ten dang nhap da ton tai")
        store.put_user(username, {
            "password_hash": generate_password_hash(password),
            "role": role,
            "enabled": True,
            **profile,
        })
    return jsonify({"username": username, "role": role, "enabled": True}), 201


@auth_bp.put("/api/users/<username>")
@roles_required("admin")
def users_update(username):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("body phai la object JSON")
    unknown = set(body) - {"role", "enabled", "password", "full_name", "email", "department"}
    if unknown:
        raise ValueError("truong khong ho tro: %s" % ", ".join(sorted(unknown)))
    store = get_store()
    username = username.strip().lower()
    with current_app.extensions["account_lock"]:
        user = store.get_user(username)
        if not user:
            return jsonify({"error": "khong tim thay nguoi dung"}), 404
        if username == current_user()["username"] and ("role" in body or body.get("enabled") is False):
            raise ValueError("khong the giam quyen hoac vo hieu hoa tai khoan dang dang nhap")
        was_active_admin = user.get("role") == "admin" and user.get("enabled", True)
        if "role" in body:
            if not isinstance(body["role"], str) or body["role"] not in ROLES:
                raise ValueError("role phai la admin, operator hoac viewer")
            user["role"] = body["role"]
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                raise ValueError("enabled phai la true hoac false")
            user["enabled"] = body["enabled"]
        if "password" in body:
            error = _password_error(body["password"])
            if error:
                raise ValueError(error)
            user["password_hash"] = generate_password_hash(body["password"])
        user.update(_profile_fields(body))
        remains_admin = user.get("role") == "admin" and user.get("enabled", True)
        if was_active_admin and not remains_admin:
            remaining = [u for u in store.list_users() if u["username"] != username and u.get("role") == "admin" and u.get("enabled", True)]
            if not remaining:
                raise ValueError("phai con it nhat mot admin dang hoat dong")
        store.put_user(username, user)
    return jsonify({"username": username, "role": user["role"], "enabled": user.get("enabled", True)})


@auth_bp.delete("/api/users/<username>")
@roles_required("admin")
def users_delete(username):
    store = get_store()
    username = username.strip().lower()
    with current_app.extensions["account_lock"]:
        user = store.get_user(username)
        if not user:
            return jsonify({"error": "khong tim thay nguoi dung"}), 404
        if username == current_user()["username"]:
            raise ValueError("khong the xoa tai khoan dang dang nhap")
        if user.get("role") == "admin" and user.get("enabled", True):
            remaining = [u for u in store.list_users() if u["username"] != username and u.get("role") == "admin" and u.get("enabled", True)]
            if not remaining:
                raise ValueError("phai con it nhat mot admin dang hoat dong")
        store.delete_user(username)
    return jsonify({"deleted": username})
