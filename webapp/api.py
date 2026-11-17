"""Event management, JSONL transfer, and checkpoint endpoints."""

import hmac
import json
import math
import secrets
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
import re

from flask import Blueprint, Response, current_app, g, jsonify, render_template, request, session, stream_with_context

from logstore import LEVELS, LogStore
from logstore.demo import seed_demo as seed_demo_data
from logstore.gen import generate
from logstore.store import (AUDIT_SOURCE, MAX_KEY_FIELD_LENGTH, MAX_TS_MS, IdempotencyConflict,
                            is_internal_audit_event)
from .auth import current_user, login_required, roles_required
from .maintenance import prune_checkpoints


api_bp = Blueprint("api", __name__)
MAX_IMPORT_EVENTS = 100_000
MAX_EXPORT_EVENTS = 1_000_000
MAX_RETENTION_HOURS = 24 * 3650
MAX_FUTURE_SKEW_MS = 24 * 3_600_000  # chap nhan dong ho nguon lech toi da 1 ngay
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
    where = " tai dong %d" % line if line else ""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > MAX_TS_MS:
        raise ValueError("ts%s phai la so nguyen milliseconds khong am" % where)
    if value > int(time.time() * 1000) + MAX_FUTURE_SKEW_MS:
        raise ValueError("ts%s khong duoc vuot qua hien tai hon 24 gio" % where)
    return value


def _event_batch(records, store, line_offset=0):
    batch = []
    for index, record in enumerate(records, 1):
        line_number = line_offset + index
        if not isinstance(record, dict):
            raise ValueError("moi su kien phai la object JSON")
        level = record.get("level", "INFO")
        level = level.upper() if isinstance(level, str) else level
        source, msg, fields = record.get("source", ""), record.get("msg", ""), record.get("fields")
        ts = _event_time(record.get("ts"), line_number if line_offset else None)
        store._check_event(level, source, msg, fields)
        if is_internal_audit_event({"source": source, "fields": fields}):
            raise ValueError("_audit danh rieng cho audit-service")
        batch.append((level, source, msg, fields, ts))
    return batch


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
        batch = _event_batch(records, store)
        ids = store.put_many(batch)
        return jsonify({"written": len(ids), "ids": [event_id.hex() for event_id in ids]}), 201

    last_minutes = _query_integer("last_minutes")
    start, end = _query_integer("start"), _query_integer("end")
    if last_minutes is not None:
        if last_minutes <= 0 or last_minutes > 24 * 3650 * 60:
            raise ValueError("last_minutes phai lon hon 0 va khong qua 10 nam")
        end = int(time.time() * 1000)
        start = end - last_minutes * 60_000
    if any(value is not None and not 0 <= value <= MAX_TS_MS for value in (start, end)):
        raise ValueError("start va end phai trong khoang 0-%d" % MAX_TS_MS)
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
    if any(value and len(value) > MAX_KEY_FIELD_LENGTH for value in (source, trace_id)):
        raise ValueError("source va trace_id toi da %d ky tu" % MAX_KEY_FIELD_LENGTH)
    exclude_audit = request.args.get("hide_audit") in {"1", "true"}
    if contains and len(contains) > 500:
        raise ValueError("q toi da 500 ky tu")
    started = time.perf_counter()
    cursor_value = request.args.get("cursor")
    cursor = _event_key(cursor_value) if cursor_value else None
    rows = store.query(start_ms=start, end_ms=end, level=level, source=source,
                       trace_id=trace_id, contains=contains, limit=limit + (1 if limit else 0),
                       before_id=cursor, exclude_audit=exclude_audit)
    has_more = bool(limit and len(rows) > limit)
    if has_more:
        rows = rows[:limit]
    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
    return jsonify({"count": len(rows), "took_ms": elapsed_ms, "events": rows,
                    "has_more": has_more,
                    "next_cursor": rows[-1]["id"] if has_more and rows else None})


@api_bp.post("/api/ingest")
def ingest():
    supplied = request.headers.get("Authorization", "")
    candidate = supplied[7:] if supplied.startswith("Bearer ") else ""
    producer_id = None
    configured_tokens = current_app.config.get("INGEST_TOKENS") or {}
    for configured_producer, configured_token in configured_tokens.items():
        token_matches = hmac.compare_digest(candidate.encode("utf-8"), configured_token.encode("utf-8"))
        if token_matches:
            producer_id = configured_producer
    legacy_token = current_app.config.get("INGEST_TOKEN")
    if not configured_tokens and isinstance(legacy_token, str) and legacy_token.strip():
        if hmac.compare_digest(candidate.encode("utf-8"), legacy_token.encode("utf-8")):
            producer_id = "legacy"
    if producer_id is None:
        return jsonify({"error": "token ingest khong hop le hoac chua duoc cau hinh"}), 401
    g.audit_actor = "ingest:%s" % producer_id
    body = request.get_json(silent=True)
    if body is None:
        raise ValueError("body phai la JSON")
    records = body if isinstance(body, list) else [body]
    if not records or len(records) > MAX_IMPORT_EVENTS:
        raise ValueError("so su kien phai trong khoang 1-100000")
    source_event_ids = []
    for record in records:
        if not isinstance(record, dict) or "source_event_id" not in record:
            raise ValueError("moi su kien can co source_event_id")
        source_event_ids.append(record["source_event_id"])
    batch = _event_batch(records, _store())
    try:
        ids, written = _store().put_ingested_many(batch, producer_id, source_event_ids)
    except IdempotencyConflict as error:
        return jsonify({"error": str(error)}), 409
    return jsonify({"written": written, "duplicates": len(ids) - written,
                    "ids": [event_id.hex() for event_id in ids]}), 201


@api_bp.get("/audit")
@roles_required("admin")
def audit_page():
    return render_template("audit.html", user=current_user())


@api_bp.get("/api/audit")
@roles_required("admin")
def audit_events():
    limit = _query_integer("limit", 50)
    if limit < 1 or limit > 100:
        raise ValueError("limit phai trong khoang 1-100")
    cursor_text = request.args.get("cursor")
    cursor = _event_key(cursor_text) if cursor_text else None
    store = _store()
    events = []
    scanned = 0
    scan_cursor = cursor
    scan_budget = 5000
    batch_size = 250
    exhausted = False

    while scanned < scan_budget and len(events) <= limit:
        requested = min(batch_size, scan_budget - scanned)
        rows = store.query(source=AUDIT_SOURCE, limit=requested, before_id=scan_cursor)
        if not rows:
            exhausted = True
            break
        for event in rows:
            scan_cursor = bytes.fromhex(event["id"])
            scanned += 1
            if is_internal_audit_event(event):
                fields = event.get("fields") or {}
                events.append({
                    "id": event["id"], "ts": event["ts"],
                    "actor": fields["actor"], "action": fields["action"],
                    "target": fields.get("target"), "status": fields["status"],
                })
                if len(events) > limit:
                    return jsonify({"count": limit, "events": events[:limit], "has_more": True,
                                    "next_cursor": events[limit - 1]["id"]})
        if len(rows) < requested:
            exhausted = True
            break

    more_raw_rows = False
    if not exhausted and scan_cursor is not None:
        more_raw_rows = bool(store.query(source=AUDIT_SOURCE, limit=1, before_id=scan_cursor))
    has_more = more_raw_rows
    return jsonify({"count": len(events), "events": events, "has_more": has_more,
                    "next_cursor": scan_cursor.hex() if has_more and scan_cursor else None})


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
    lifecycle_lock = current_app.extensions["store_lifecycle_lock"]
    release_lifecycle_lock = getattr(g, "store_read_release", None)
    if release_lifecycle_lock is None:
        release_lifecycle_lock = lifecycle_lock.acquire_read()
    else:
        g.store_read_release = None
    snapshot_holder = []

    def release_export():
        for snapshot in snapshot_holder:
            close = getattr(snapshot, "close", None)
            if close:
                close()
        snapshot_holder.clear()
        release_lifecycle_lock()

    try:
        store = _store()
        snapshot_holder.append(store.snapshot_events())
        if snapshot_holder[0].total_count > MAX_EXPORT_EVENTS:
            release_export()
            return jsonify({"error": "co hon 1000000 su kien; hay loc va xuat bang CLI"}), 413

        def records():
            scanned = 0
            try:
                for event in snapshot_holder[0]:
                    scanned += 1
                    if is_internal_audit_event(event):
                        continue
                    yield json.dumps({key: event[key] for key in ("level", "source", "msg", "fields", "ts")},
                                     ensure_ascii=False, separators=(",", ":")) + "\n"
                    if scanned >= MAX_EXPORT_EVENTS:
                        return
            finally:
                release_export()

        response = Response(stream_with_context(records()), mimetype="application/x-ndjson",
                            headers={"Content-Disposition": "attachment; filename=logstore-export.jsonl"})
        response.call_on_close(release_export)
        return response
    except Exception:
        release_export()
        raise


@api_bp.post("/api/import")
@roles_required("admin", "operator")
def import_jsonl():
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise ValueError("hay chon tep JSONL")
    store = _store()
    count = 0
    with tempfile.SpooledTemporaryFile(mode="w+t", max_size=1024 * 1024, encoding="utf-8", newline="\n") as staged:
        for line_number, raw_line in enumerate(upload.stream, 1):
            try:
                line = raw_line.decode("utf-8-sig" if line_number == 1 else "utf-8")
            except UnicodeDecodeError:
                raise ValueError("tep phai duoc ma hoa UTF-8")
            if not line.strip():
                continue
            if count >= MAX_IMPORT_EVENTS:
                raise ValueError("tep vuot qua 100000 su kien")
            try:
                record = json.loads(line)
                batch = _event_batch([record], store, line_number - 1)
            except json.JSONDecodeError as error:
                raise ValueError("JSONL loi tai dong %d: %s" % (line_number, error.msg))
            except ValueError as error:
                message = str(error)
                if "dong" not in message and line_number:
                    message = "dong %d: %s" % (line_number, message)
                raise ValueError(message)
            staged.write(json.dumps(batch[0], ensure_ascii=False, separators=(",", ":")) + "\n")
            count += 1
        if not count:
            raise ValueError("tep JSONL khong co su kien")
        staged.seek(0)
        batch = []
        for line in staged:
            level, source, msg, fields, ts = json.loads(line)
            batch.append((level, source, msg, fields, ts))
        ids = store.put_many(batch)
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
    keep_count = current_app.config.get("BACKUP_RETENTION_COUNT", 0)
    if keep_count:
        try:
            prune_checkpoints(root, keep_count, newest_path=target)
        except Exception:
            current_app.logger.warning("Checkpoint rotation failed after backup %s was created", name, exc_info=True)
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
    lifecycle_lock = current_app.extensions["store_lifecycle_lock"]
    old_store = _store()
    old_path = current_app.config["LOGSTORE_PATH"]
    old_closed = False
    try:
        restored_store = LogStore(str(target))
        restored_store.stats()
        with lifecycle_lock.write():
            old_store.close()
            old_closed = True
            current_app.extensions["persist_active_paths"](target, backup_root)
            current_app.extensions["logstore"] = restored_store
            current_app.config["LOGSTORE_PATH"] = str(target)
    except Exception as error:
        if restored_store is not None:
            restored_store.close()
        with lifecycle_lock.write():
            try:
                if old_closed:
                    current_app.extensions["logstore"] = LogStore(old_path)
            except Exception:
                current_app.logger.exception("Could not reopen the previous RocksDB store")
        shutil.rmtree(target, ignore_errors=True)
        raise ValueError("khong the mo ban sao luu; co so du lieu hien tai duoc giu nguyen") from error
    return jsonify({"restored": name, "active_path": str(target)})
