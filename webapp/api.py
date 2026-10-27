"""Event management, JSONL transfer, and checkpoint endpoints."""

import json
import math
import secrets
import shutil
import time
from datetime import datetime
from pathlib import Path
import re

from flask import Blueprint, Response, current_app, jsonify, request, session

from logstore import LEVELS, LogStore
from logstore.demo import seed_demo as seed_demo_data
from logstore.gen import generate
from .auth import current_user, login_required, roles_required


api_bp = Blueprint("api", __name__)
MAX_IMPORT_EVENTS = 100_000
MAX_EXPORT_EVENTS = 1_000_000
MAX_RETENTION_HOURS = 24 * 3650
BACKUP_NAME_RE = re.compile(r"^backup-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$")


def _store():
    return current_app.extensions["logstore"]


def _json_object():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("body phai la object JSON")
    return value


def _query_integer(name, default=None):
    value = request.args.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError("tham so %s phai la so nguyen" % name)


def _event_time(value, line=None):
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 0xFFFFFFFFFFFFFFFF:
        where = " tai dong %d" % line if line else ""
        raise ValueError("ts%s phai la so nguyen milliseconds khong am" % where)
    return value


@api_bp.get("/api/session")
@login_required
def session_info():
    user = current_user()
    return jsonify({"username": user["username"], "role": user["role"], "csrf_token": session["_csrf"]})


@api_bp.get("/api/stats")
@login_required
def stats():
    store = _store()
    now = int(time.time() * 1000)
    result = store.stats()
    result.update({
        "by_level": store.count_by_level(),
        "by_level_last_hour": store.count_by_level(now - 3_600_000, now),
        "sources": store.sources(),
        "total_events": store.count(),
    })
    return jsonify(result)


@api_bp.route("/api/events", methods=["GET", "POST"])
@login_required
def events():
    store = _store()
    if request.method == "POST":
        if current_user()["role"] == "viewer":
            return jsonify({"error": "khong du quyen"}), 403
        body = request.get_json(silent=True)
        if body is None:
            raise ValueError("body phai la JSON")
        records = body if isinstance(body, list) else [body]
        if not records or len(records) > MAX_IMPORT_EVENTS:
            raise ValueError("so su kien phai trong khoang 1-100000")
        batch = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("moi su kien phai la object JSON")
            level = record.get("level", "INFO")
            level = level.upper() if isinstance(level, str) else level
            source, msg, fields = record.get("source", ""), record.get("msg", ""), record.get("fields")
            ts = _event_time(record.get("ts"))
            store._check_event(level, source, msg, fields)
            batch.append((level, source, msg, fields, ts))
        ids = store.put_many(batch)
        return jsonify({"written": len(ids), "ids": [event_id.hex() for event_id in ids]}), 201

    last_minutes = _query_integer("last_minutes")
    start, end = _query_integer("start"), _query_integer("end")
    if last_minutes is not None:
        if last_minutes <= 0 or last_minutes > 24 * 3650 * 60:
            raise ValueError("last_minutes phai lon hon 0 va khong qua 10 nam")
        end = int(time.time() * 1000)
        start = end - last_minutes * 60_000
    if (start is not None and start < 0) or (end is not None and end < 0):
        raise ValueError("start va end khong duoc am")
    if start is not None and end is not None and end < start:
        raise ValueError("end phai lon hon hoac bang start")
    level = request.args.get("level") or None
    if level:
        level = level.upper()
        if level not in LEVELS:
            raise ValueError("level phai thuoc %s" % LEVELS)
    limit = _query_integer("limit", 100)
    if limit < 0 or limit > 1000:
        raise ValueError("limit phai trong khoang 0-1000")
    source, trace_id, contains = (request.args.get(key) or None for key in ("source", "trace_id", "q"))
    if any(value and len(value) > 256 for value in (source, trace_id)):
        raise ValueError("source va trace_id toi da 256 ky tu")
    if contains and len(contains) > 500:
        raise ValueError("q toi da 500 ky tu")
    started = time.perf_counter()
    rows = store.query(start_ms=start, end_ms=end, level=level, source=source,
                       trace_id=trace_id, contains=contains, limit=limit)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return jsonify({"count": len(rows), "took_ms": elapsed_ms, "events": rows})


@api_bp.get("/api/events/<event_id>")
@login_required
def get_event(event_id):
    try:
        key = LogStore.parse_id(event_id)
    except ValueError:
        raise ValueError("id khong hop le")
    if len(key) != 12:
        raise ValueError("id khong hop le")
    event = _store().get(key)
    if event is None:
        return jsonify({"error": "khong tim thay"}), 404
    return jsonify(event)


def _event_key(event_id):
    try:
        key = LogStore.parse_id(event_id)
    except ValueError:
        raise ValueError("id khong hop le")
    if len(key) != 12:
        raise ValueError("id khong hop le")
    return key


@api_bp.put("/api/events/<event_id>")
@roles_required("admin", "operator")
def update_event(event_id):
    record = _json_object()
    unknown = set(record) - {"level", "source", "msg", "fields"}
    if unknown:
        raise ValueError("truong khong ho tro: %s" % ", ".join(sorted(unknown)))
    if isinstance(record.get("level"), str):
        record["level"] = record["level"].upper()
    updated = _store().update_event(_event_key(event_id), **record)
    if updated is None:
        return jsonify({"error": "khong tim thay"}), 404
    return jsonify(updated)


@api_bp.delete("/api/events/<event_id>")
@roles_required("admin", "operator")
def delete_event(event_id):
    if not _store().delete_event(_event_key(event_id)):
        return jsonify({"error": "khong tim thay"}), 404
    return jsonify({"deleted": event_id})


@api_bp.post("/api/purge")
@roles_required("admin", "operator")
def purge():
    body = _json_object()
    raw_hours = body.get("older_than_hours", 24)
    if isinstance(raw_hours, bool):
        raise ValueError("older_than_hours phai la so, khong phai true/false")
    try:
        hours = float(raw_hours)
    except (TypeError, ValueError):
        raise ValueError("older_than_hours phai la so")
    if not math.isfinite(hours) or hours <= 0 or hours > MAX_RETENTION_HOURS:
        raise ValueError("older_than_hours phai lon hon 0 va khong qua 10 nam")
    compact = body.get("compact", False)
    if not isinstance(compact, bool):
        raise ValueError("compact phai la true hoac false")
    cutoff = int(time.time() * 1000) - int(hours * 3_600_000)
    deleted = _store().purge_older_than(cutoff, compact=compact)
    return jsonify({"deleted": deleted, "cutoff_ms": cutoff})


@api_bp.post("/api/simulate")
@roles_required("admin", "operator")
def simulate():
    body = _json_object()
    count, hours = body.get("n", 1000), body.get("hours", 1)
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 200_000:
        raise ValueError("n phai trong khoang 1-200000")
    if not isinstance(hours, int) or isinstance(hours, bool) or not 1 <= hours <= 24 * 3650:
        raise ValueError("hours phai trong khoang 1-87600")
    generated = generate(count, span_hours=hours, seed=int(time.time()) % 100_000)
    store = _store()
    for offset in range(0, count, 1000):
        store.put_many(generated[offset:offset + 1000])
    return jsonify({"written": count})


@api_bp.post("/api/demo/seed")
@roles_required("admin")
def seed_demo():
    body = _json_object()
    result = seed_demo_data(_store(), body.get("password"))
    status = 201 if result["users_created"] or result["events_written"] else 200
    return jsonify(result), status


@api_bp.get("/api/export")
@login_required
def export_jsonl():
    store = _store()
    if store.count() > MAX_EXPORT_EVENTS:
        return jsonify({"error": "co hon 1000000 su kien; hay loc va xuat bang CLI"}), 413
    records = store.query(limit=MAX_EXPORT_EVENTS)
    text = "".join(json.dumps({key: event[key] for key in ("level", "source", "msg", "fields", "ts")},
                              ensure_ascii=False, separators=(",", ":")) + "\n" for event in records)
    return Response(text, mimetype="application/x-ndjson",
                    headers={"Content-Disposition": "attachment; filename=logstore-export.jsonl"})


@api_bp.post("/api/import")
@roles_required("admin", "operator")
def import_jsonl():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise ValueError("hay chon tep JSONL")
    try:
        content = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("tep phai duoc ma hoa UTF-8")
    store = _store()
    events_to_write = []
    for line_number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        if len(events_to_write) >= MAX_IMPORT_EVENTS:
            raise ValueError("tep vuot qua 100000 su kien")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("JSONL loi tai dong %d: %s" % (line_number, error.msg))
        if not isinstance(record, dict):
            raise ValueError("dong %d phai la object JSON" % line_number)
        level = record.get("level", "INFO")
        level = level.upper() if isinstance(level, str) else level
        source, msg = record.get("source", ""), record.get("msg", "")
        fields = record.get("fields")
        ts = _event_time(record.get("ts"), line_number)
        store._check_event(level, source, msg, fields)
        events_to_write.append((level, source, msg, fields, ts))
    if not events_to_write:
        raise ValueError("tep JSONL khong co su kien")
    ids = store.put_many(events_to_write)
    return jsonify({"written": len(ids), "ids": [event_id.hex() for event_id in ids]}), 201


@api_bp.get("/api/backups")
@roles_required("admin", "operator")
def list_backups():
    root = Path(current_app.config["BACKUP_PATH"])
    backups = sorted(path.name for path in root.iterdir() if path.is_dir() and (path / "CURRENT").is_file())
    return jsonify({"backups": backups})


@api_bp.post("/api/backup")
@roles_required("admin", "operator")
def create_backup():
    root = Path(current_app.config["BACKUP_PATH"])
    name = "backup-%s-%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"), secrets.token_hex(4))
    target = root / name
    store = _store()
    store.flush()
    store.backup(str(target))
    return jsonify({"name": name, "path": str(target)}), 201


@api_bp.post("/api/restore")
@roles_required("admin")
def restore_backup():
    body = _json_object()
    name = body.get("name")
    if not isinstance(name, str) or not BACKUP_NAME_RE.fullmatch(name):
        raise ValueError("ten ban sao luu khong hop le")
    backup_root = Path(current_app.config["BACKUP_PATH"]).resolve()
    source = (backup_root / name).resolve()
    if source.parent != backup_root or not (source / "CURRENT").is_file():
        return jsonify({"error": "khong tim thay ban sao luu hop le"}), 404
    restore_root = backup_root.parent / "restores"
    restore_root.mkdir(parents=True, exist_ok=True)
    target = restore_root / ("restore-%s-%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"), secrets.token_hex(4)))
    LogStore.restore(str(source), str(target))
    restored_store = None
    try:
        restored_store = LogStore(str(target))
        restored_store.stats()
        _store().close()
    except Exception as error:
        if restored_store is not None:
            restored_store.close()
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError("khong the mo ban sao luu; co so du lieu hien tai duoc giu nguyen") from error
    current_app.extensions["logstore"] = restored_store
    current_app.config["LOGSTORE_PATH"] = str(target)
    return jsonify({"restored": name, "active_path": str(target)})
