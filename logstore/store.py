"""
store.py - Lop LogStore: bo luu tru nhat ky/su kien tren RocksDB.

Thiet ke tong quan (xem them trong bao cao):

  Column family "default"     : du lieu chinh   key = event_id            value = JSON
  Column family "idx_level"   : index theo muc  key = level(1B) + event_id   value = rong
  Column family "idx_source"  : index theo nguon key = source + 0x00 + event_id   value = rong
  Column family "idx_trace"   : index theo trace key = trace_id + 0x00 + event_id  value = rong
  Column family "meta"        : danh sach source va bo dem su kien ben vung
  Column family "users"       : tai khoan, password hash, vai tro va trang thai

event_id = 8 byte timestamp (ms, big-endian) + 4 byte so thu tu (big-endian)
=> so sanh byte-by-byte cung chinh la thu tu thoi gian, nen RocksDB (luu key da
sap xep) quet theo khoang thoi gian rat nhanh, khong can sort lai.
"""

import gc
import hashlib
import heapq
import json
import os
import shutil
import struct
import threading
import time

from rocksdict import (
    BlockBasedOptions,
    Cache,
    Checkpoint,
    DBCompressionType,
    Options,
    Rdict,
    WriteBatch,
    WriteOptions,
)

LEVELS = ["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]
LEVEL_NUM = {name: i for i, name in enumerate(LEVELS)}


class IdempotencyConflict(ValueError):
    """A producer reused a source event ID for different event content."""

CF_INDEX_LEVEL = "idx_level"
CF_INDEX_SOURCE = "idx_source"
CF_INDEX_TRACE = "idx_trace"
CF_META = "meta"
CF_USERS = "users"
CF_INGEST = "idx_ingest"
META_COUNT_VERSION = b"\x00count:version"
META_COUNT_TOTAL = b"\x00count:total"
META_COUNT_LEVEL = b"\x00count:level:"

ID_STRUCT = struct.Struct(">QI")  # 8 byte ts_ms + 4 byte seq
ID_LEN = ID_STRUCT.size  # 12
MIN_ID = ID_STRUCT.pack(0, 0)
AUDIT_SOURCE = "audit-service"


def is_internal_audit_event(event):
    """Match only the canonical records produced by put_audit()."""
    fields = event.get("fields") or {}
    return (
        event.get("source") == AUDIT_SOURCE
        and fields.get("_audit") is True
        and isinstance(fields.get("actor"), str)
        and isinstance(fields.get("action"), str)
        and isinstance(fields.get("status"), int)
        and not isinstance(fields.get("status"), bool)
    )


class EventSnapshot:
    """Streaming iterator pinned to one primary-column-family view."""

    def __init__(self, db, total_count):
        self.total_count = total_count
        self._snapshot = db.snapshot()
        self._iterator = self._snapshot.iter()
        self._iterator.seek_to_last()

    def __iter__(self):
        return self

    def __next__(self):
        if self._iterator is None or not self._iterator.valid():
            raise StopIteration
        event = LogStore._decode(self._iterator.key(), self._iterator.value())
        self._iterator.prev()
        return event

    def close(self):
        self._iterator = None
        self._snapshot = None


def make_id(ts_ms, seq=0):
    return ID_STRUCT.pack(ts_ms, seq)


def split_id(event_id):
    return ID_STRUCT.unpack(event_id)


def _base_options():
    """Options dung chung cho moi column family."""
    opts = Options(raw_mode=True)
    opts.set_compression_type(DBCompressionType.lz4())
    # Bloom filter giup Get() tranh doc cac file SST khong chua key
    table = BlockBasedOptions()
    table.set_bloom_filter(10, False)
    table.set_block_cache(Cache(32 * 1024 * 1024))
    opts.set_block_based_table_factory(table)
    return opts


class LogStore:
    def __init__(self, path, sync_writes=False):
        """
        path        : thu muc chua du lieu RocksDB
        sync_writes : True = fsync WAL sau moi lan ghi (an toan hon, cham hon)
        """
        self.path = path
        self._lock = threading.Lock()

        db_opts = _base_options()
        db_opts.create_if_missing(True)
        db_opts.create_missing_column_families(True)
        db_opts.set_write_buffer_size(64 * 1024 * 1024)  # memtable 64MB
        db_opts.set_max_write_buffer_number(3)
        db_opts.set_max_background_jobs(4)
        db_opts.increase_parallelism(4)

        cfs = {
            CF_INDEX_LEVEL: _base_options(),
            CF_INDEX_SOURCE: _base_options(),
            CF_INDEX_TRACE: _base_options(),
            CF_META: _base_options(),
            CF_USERS: _base_options(),
            CF_INGEST: _base_options(),
        }
        self.db = Rdict(path, db_opts, column_families=cfs)
        self.cf_level = self.db.get_column_family(CF_INDEX_LEVEL)
        self.cf_source = self.db.get_column_family(CF_INDEX_SOURCE)
        self.cf_trace = self.db.get_column_family(CF_INDEX_TRACE)
        self.cf_meta = self.db.get_column_family(CF_META)
        self.cf_users = self.db.get_column_family(CF_USERS)
        self.cf_ingest = self.db.get_column_family(CF_INGEST)
        self.h_default = self.db.get_column_family_handle("default")
        self.h_level = self.db.get_column_family_handle(CF_INDEX_LEVEL)
        self.h_source = self.db.get_column_family_handle(CF_INDEX_SOURCE)
        self.h_trace = self.db.get_column_family_handle(CF_INDEX_TRACE)
        self.h_meta = self.db.get_column_family_handle(CF_META)
        self.h_users = self.db.get_column_family_handle(CF_USERS)
        self.h_ingest = self.db.get_column_family_handle(CF_INGEST)

        wo = WriteOptions()
        wo.sync = bool(sync_writes)
        self.db.set_write_options(wo)

        # Nho ID cuoi cung de cap phat nhanh cho timestamp moi nhat.
        self._last_ts, self._last_seq = self._load_last_id()
        self._late_sequences = {}
        self._known_sources = set(self._load_sources())
        self._total_events, self._event_counts = self._load_event_counts()

    # ------------------------------------------------------------------ noi bo
    def _load_last_id(self):
        it = self.db.iter()
        it.seek_to_last()
        if it.valid():
            return split_id(it.key())
        return 0, -1

    def _load_sources(self):
        it = self.cf_meta.iter()
        it.seek_to_first()
        out = []
        while it.valid():
            key = it.key()
            if not key.startswith(b"\x00"):
                out.append(key.decode("utf-8"))
            it.next()
        return out

    def _load_event_counts(self):
        if self.cf_meta.get(META_COUNT_VERSION) == b"1":
            total = int(self.cf_meta.get(META_COUNT_TOTAL) or b"0")
            counts = {
                level: int(self.cf_meta.get(META_COUNT_LEVEL + level.encode("ascii")) or b"0")
                for level in LEVELS
            }
            return total, counts

        counts = dict.fromkeys(LEVELS, 0)
        total = 0
        it = self.db.iter()
        it.seek_to_first()
        while it.valid():
            record = json.loads(it.value().decode("utf-8"))
            counts[record["level"]] += 1
            total += 1
            it.next()
        wb = WriteBatch(raw_mode=True)
        wb.put(META_COUNT_TOTAL, str(total).encode("ascii"), self.h_meta)
        for level, count in counts.items():
            wb.put(META_COUNT_LEVEL + level.encode("ascii"), str(count).encode("ascii"), self.h_meta)
        wb.put(META_COUNT_VERSION, b"1", self.h_meta)
        self.db.write(wb)
        return total, counts

    def _count_delta_batch(self, wb, total_delta, level_deltas):
        updated_counts = self._event_counts.copy()
        for level, delta in level_deltas.items():
            updated_counts[level] += delta
            if updated_counts[level] < 0:
                raise RuntimeError("event count would become negative")
            wb.put(META_COUNT_LEVEL + level.encode("ascii"),
                   str(updated_counts[level]).encode("ascii"), self.h_meta)
        updated_total = self._total_events + total_delta
        if updated_total < 0:
            raise RuntimeError("event count would become negative")
        wb.put(META_COUNT_TOTAL, str(updated_total).encode("ascii"), self.h_meta)
        return updated_total, updated_counts

    def _next_id(self, ts_ms):
        """Tao ID theo timestamp su kien; log den tre van giu dung thoi diem."""
        if ts_ms < self._last_ts:
            seq = self._late_sequences.get(ts_ms)
            if seq is None:
                seq = 0
                while self.db.get(make_id(ts_ms, seq)) is not None:
                    seq += 1
            self._late_sequences[ts_ms] = seq + 1
            return make_id(ts_ms, seq), ts_ms
        if ts_ms == self._last_ts:
            self._last_seq += 1
        else:
            self._last_seq = 0
        self._last_ts = ts_ms
        return make_id(ts_ms, self._last_seq), ts_ms

    @staticmethod
    def _check_event(level, source, msg=None, fields=None):
        if not isinstance(level, str) or level not in LEVEL_NUM:
            raise ValueError("level phai thuoc %s" % LEVELS)
        if not isinstance(source, str) or not source or "\x00" in source:
            raise ValueError("source khong duoc rong va khong chua ky tu NUL")
        if not isinstance(msg, str):
            raise ValueError("msg phai la chuoi")
        if fields is not None and not isinstance(fields, dict):
            raise ValueError("fields phai la object JSON")
        trace_id = (fields or {}).get("trace_id")
        if trace_id is not None and (not isinstance(trace_id, str) or "\x00" in trace_id):
            raise ValueError("fields.trace_id phai la chuoi khong chua ky tu NUL")

    def _add_to_batch(self, wb, event_id, level, source, msg, fields):
        value = json.dumps(
            {"level": level, "source": source, "msg": msg, "fields": fields or {}},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        src = source.encode("utf-8")
        wb.put(event_id, value, self.h_default)
        wb.put(bytes([LEVEL_NUM[level]]) + event_id, b"", self.h_level)
        wb.put(src + b"\x00" + event_id, b"", self.h_source)
        trace_id = (fields or {}).get("trace_id")
        if trace_id:
            wb.put(trace_id.encode("utf-8") + b"\x00" + event_id, b"", self.h_trace)
        if source not in self._known_sources:
            wb.put(src, b"1", self.h_meta)

    # --------------------------------------------------------------------- ghi
    def put_event(self, level, source, msg, fields=None, ts_ms=None):
        """Ghi 1 su kien. Tra ve event_id (bytes)."""
        return self.put_many([(level, source, msg, fields, ts_ms)])[0]

    def put_many(self, events):
        """
        Ghi nhieu su kien trong 1 WriteBatch => nguyen tu: hoac ghi het
        (du lieu + cac index) hoac khong ghi gi. events: list (level, source, msg, fields, ts_ms)
        """
        return self._write_events(events)

    def put_ingested_many(self, events, producer_id, source_event_ids):
        """Atomically store producer-scoped idempotent events.

        Returns ``(event_ids, newly_written_count)`` in input order. A source ID
        remains reserved until its event is explicitly deleted or retained away.
        """
        if not isinstance(producer_id, str) or not producer_id or "\x00" in producer_id:
            raise ValueError("producer_id khong hop le")
        if len(events) != len(source_event_ids):
            raise ValueError("source_event_id bi thieu")
        keys = []
        hashes = []
        prefix = producer_id.encode("utf-8") + b"\x00"
        for event, source_event_id in zip(events, source_event_ids):
            if (not isinstance(source_event_id, str) or not source_event_id.strip()
                    or len(source_event_id) > 512 or "\x00" in source_event_id):
                raise ValueError("source_event_id phai la chuoi 1-512 ky tu")
            level, source, msg, fields, ts_ms = event
            self._check_event(level, source, msg, fields)
            canonical = json.dumps(
                {"level": level, "source": source, "msg": msg,
                 "fields": fields or {}, "ts": ts_ms},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            hashes.append(hashlib.sha256(canonical).hexdigest().encode("ascii"))
            keys.append(b"i\x00" + hashlib.sha256(prefix + source_event_id.encode("utf-8")).digest())

        ids = [None] * len(events)
        written = 0
        new_entries = {}
        new_events = {}
        reverse_entries = {}
        with self._lock:
            for index, (event, key, payload_hash) in enumerate(zip(events, keys, hashes)):
                entry = new_entries.get(key)
                if entry is not None and entry["hash"].encode("ascii") != payload_hash:
                    raise IdempotencyConflict("source_event_id da duoc dung voi noi dung khac")
                if entry is None:
                    raw = self.cf_ingest.get(key)
                    if raw is not None:
                        entry = json.loads(raw.decode("utf-8"))
                        if entry.get("hash", "").encode("ascii") != payload_hash:
                            raise IdempotencyConflict("source_event_id da duoc dung voi noi dung khac")
                        event_id = bytes.fromhex(entry["id"])
                        if self.db.get(event_id) is None:
                            raise RuntimeError("idempotency mapping points to a missing event")
                    else:
                        level, source, msg, fields, ts_ms = event
                        if ts_ms is None:
                            ts_ms = int(time.time() * 1000)
                        event_id, _ = self._next_id(ts_ms)
                        entry = {"id": event_id.hex(), "hash": payload_hash.decode("ascii")}
                        new_entries[key] = entry
                        reverse_entries.setdefault(event_id, []).append(key)
                        new_events[event_id] = event
                        written += 1
                        ids[index] = event_id
                        continue
                ids[index] = bytes.fromhex(entry["id"])

            if written:
                wb = WriteBatch(raw_mode=True)
                new_sources = set()
                level_deltas = dict.fromkeys(LEVELS, 0)
                for event_id, event in new_events.items():
                    level, source, msg, fields, ts_ms = event
                    self._check_event(level, source, msg, fields)
                    if is_internal_audit_event({"source": source, "fields": fields}):
                        raise ValueError("_audit is reserved for system audit records")
                    self._add_to_batch(wb, event_id, level, source, msg, fields)
                    if source not in self._known_sources:
                        new_sources.add(source)
                    level_deltas[level] += 1
                    keys_for_event = reverse_entries[event_id]
                    for key in keys_for_event:
                        entry = new_entries[key]
                        wb.put(key, json.dumps(entry, separators=(",", ":")).encode("utf-8"), self.h_ingest)
                    wb.put(b"r\x00" + event_id, json.dumps(
                        [key.hex() for key in keys_for_event], separators=(",", ":")
                    ).encode("ascii"), self.h_ingest)
                updated_total, updated_counts = self._count_delta_batch(wb, written, level_deltas)
                self.db.write(wb)
                self._known_sources |= new_sources
                self._total_events, self._event_counts = updated_total, updated_counts
        return ids, written

    def _delete_ingest_mapping(self, wb, event_id):
        reverse_key = b"r\x00" + event_id
        raw = self.cf_ingest.get(reverse_key)
        if raw is None:
            return
        try:
            keys = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("invalid ingest reverse mapping") from error
        for key in keys:
            wb.delete(bytes.fromhex(key), self.h_ingest)
        wb.delete(reverse_key, self.h_ingest)

    def put_audit(self, actor, action, target=None, status=200):
        """Append an immutable web action record to the normal event stream."""
        fields = {"_audit": True, "actor": actor, "action": action, "status": status}
        if target:
            fields["target"] = str(target)[:256]
        event = ("INFO", AUDIT_SOURCE, "%s %s" % (actor, action), fields, None)
        return self._write_events([event], allow_audit=True)[0]

    def _write_events(self, events, allow_audit=False):
        ids = []
        wb = WriteBatch(raw_mode=True)
        new_sources = set()
        level_deltas = dict.fromkeys(LEVELS, 0)
        with self._lock:
            for level, source, msg, fields, ts_ms in events:
                self._check_event(level, source, msg, fields)
                event = {"source": source, "fields": fields}
                if is_internal_audit_event(event) and not allow_audit:
                    raise ValueError("_audit is reserved for system audit records")
                if allow_audit and not is_internal_audit_event(event):
                    raise ValueError("invalid system audit record")
                if ts_ms is None:
                    ts_ms = int(time.time() * 1000)
                event_id, _ = self._next_id(ts_ms)
                self._add_to_batch(wb, event_id, level, source, msg, fields)
                if source not in self._known_sources:
                    new_sources.add(source)
                level_deltas[level] += 1
                ids.append(event_id)
            if not ids:
                return []
            updated_total, updated_counts = self._count_delta_batch(wb, len(ids), level_deltas)
            self.db.write(wb)
            self._known_sources |= new_sources
            self._total_events, self._event_counts = updated_total, updated_counts
        return ids

    # --------------------------------------------------------------------- doc
    def get(self, event_id):
        """Doc 1 su kien theo id. Tra ve dict hoac None."""
        raw = self.db.get(event_id)
        if raw is None:
            return None
        return self._decode(event_id, raw)

    @staticmethod
    def _decode(event_id, raw):
        ts_ms, seq = split_id(event_id)
        d = json.loads(raw.decode("utf-8"))
        d["id"] = event_id.hex()
        d["ts"] = ts_ms
        d["seq"] = seq
        return d

    @staticmethod
    def parse_id(hex_id):
        return bytes.fromhex(hex_id)

    # ---------------------------------------------------------------- quet khoang
    @staticmethod
    def _scan(cf, lo, hi, reverse=False):
        """
        Duyet cac key trong [lo, hi) cua column family cf.
        Tu kiem tra can thay vi dua vao ReadOptions de de kiem soat.
        """
        it = cf.iter()
        if not reverse:
            it.seek(lo)
            while it.valid():
                k = it.key()
                if k >= hi:
                    break
                yield k, it.value()
                it.next()
        else:
            it.seek_for_prev(hi)
            while it.valid():
                k = it.key()
                if k >= hi:  # seek_for_prev co the tra ve dung key hi
                    it.prev()
                    continue
                if k < lo:
                    break
                yield k, it.value()
                it.prev()

    def _ids_from_index(self, cf, prefix, lo_id, hi_id, reverse):
        """Sinh ra cac event_id tu 1 index (da sap xep theo thoi gian)."""
        n = len(prefix)
        for k, _ in self._scan(cf, prefix + lo_id, prefix + hi_id, reverse):
            yield k[n:]

    def snapshot_events(self):
        """Mo iterator su kien streamable, nhat quan tu thoi diem tao snapshot."""
        with self._lock:
            return EventSnapshot(self.db, self._total_events)

    def query(self, start_ms=None, end_ms=None, level=None, source=None,
              contains=None, limit=100, reverse=True, trace_id=None,
              before_id=None, after_id=None):
        """
        Truy van su kien.
          start_ms, end_ms : khoang thoi gian [start, end] (ms), None = khong gioi han
          level            : chi lay muc >= level (vd "WARN" -> WARN, ERROR, FATAL)
          source           : chi lay 1 nguon
          trace_id         : chi lay 1 trace (dung index rieng)
          contains         : loc chuoi con trong msg (loc sau, khong co index!)
          limit            : so ban ghi toi da
          reverse          : True = moi nhat truoc
        """
        if limit < 0:
            raise ValueError("limit khong duoc am")
        if limit == 0:
            return []
        if before_id and after_id:
            raise ValueError("before_id va after_id khong the dung cung luc")

        lo = make_id(max(start_ms, 0) if start_ms is not None else 0, 0)
        hi = make_id((end_ms + 1) if end_ms is not None else 0xFFFFFFFFFFFFFFF, 0)
        if before_id:
            hi = min(hi, self._validate_cursor(before_id))
        elif after_id:
            cursor = self._validate_cursor(after_id)
            lo = max(lo, (int.from_bytes(cursor, "big") + 1).to_bytes(ID_LEN, "big"))
        if lo >= hi:
            return []
        min_level = LEVEL_NUM[level] if level else 0

        if source:
            # dung index theo nguon, loc muc o buoc doc du lieu
            prefix = source.encode("utf-8") + b"\x00"
            id_iter = self._ids_from_index(self.cf_source, prefix, lo, hi, reverse)
            rows = ((i, self.db.get(i)) for i in id_iter)
        elif trace_id:
            prefix = trace_id.encode("utf-8") + b"\x00"
            id_iter = self._ids_from_index(self.cf_trace, prefix, lo, hi, reverse)
            rows = ((i, self.db.get(i)) for i in id_iter)
        elif min_level > 0:
            # gop (merge) cac index cua tung muc >= min_level theo thu tu id
            streams = [
                self._ids_from_index(self.cf_level, bytes([lv]), lo, hi, reverse)
                for lv in range(min_level, len(LEVELS))
            ]
            id_iter = heapq.merge(*streams, reverse=reverse)
            rows = ((i, self.db.get(i)) for i in id_iter)
        else:
            rows = self._scan(self.db, lo, hi, reverse)

        out = []
        for event_id, raw in rows:
            if raw is None:  # index con sot lai (khong xay ra neu ghi bang WriteBatch)
                continue
            ev = self._decode(event_id, raw)
            if LEVEL_NUM[ev["level"]] < min_level:
                continue
            if trace_id and (ev.get("fields") or {}).get("trace_id") != trace_id:
                continue
            if contains and contains.lower() not in ev["msg"].lower():
                continue
            out.append(ev)
            if len(out) >= limit:
                break
        return out

    @staticmethod
    def _validate_cursor(cursor):
        if isinstance(cursor, bytes) and len(cursor) == ID_LEN:
            return cursor
        try:
            event_id = bytes.fromhex(cursor)
        except (TypeError, ValueError):
            raise ValueError("cursor phai la event ID hex hop le")
        if len(event_id) != ID_LEN:
            raise ValueError("cursor phai la event ID hex hop le")
        return event_id

    def update_event(self, event_id, *, level=None, source=None, msg=None, fields=None):
        """Cap nhat mot su kien, giu nguyen ID va dong bo cac index trong WriteBatch."""
        if len(event_id) != ID_LEN:
            raise ValueError("id khong hop le")
        with self._lock:
            raw = self.db.get(event_id)
            if raw is None:
                return None
            old = self._decode(event_id, raw)
            level = old["level"] if level is None else level
            source = old["source"] if source is None else source
            msg = old["msg"] if msg is None else msg
            fields = old["fields"] if fields is None else fields
            self._check_event(level, source, msg, fields)
            if is_internal_audit_event(old):
                raise ValueError("khong the sua audit event")
            if is_internal_audit_event({"source": source, "fields": fields}):
                raise ValueError("_audit is reserved for system audit records")

            wb = WriteBatch(raw_mode=True)
            old_trace = old["fields"].get("trace_id")
            wb.delete(bytes([LEVEL_NUM[old["level"]]]) + event_id, self.h_level)
            wb.delete(old["source"].encode("utf-8") + b"\x00" + event_id, self.h_source)
            if old_trace:
                wb.delete(old_trace.encode("utf-8") + b"\x00" + event_id, self.h_trace)
            self._add_to_batch(wb, event_id, level, source, msg, fields)
            level_deltas = dict.fromkeys(LEVELS, 0)
            if old["level"] != level:
                level_deltas[old["level"]] -= 1
                level_deltas[level] += 1
            updated_total, updated_counts = self._count_delta_batch(wb, 0, level_deltas)
            self.db.write(wb)
            self._total_events, self._event_counts = updated_total, updated_counts
            self._known_sources.add(source)
            return self._decode(event_id, self.db.get(event_id))

    def delete_event(self, event_id):
        """Xoa mot su kien va cac index lien quan trong cung mot WriteBatch."""
        if len(event_id) != ID_LEN:
            raise ValueError("id khong hop le")
        with self._lock:
            raw = self.db.get(event_id)
            if raw is None:
                return False
            old = self._decode(event_id, raw)
            if is_internal_audit_event(old):
                raise ValueError("khong the xoa audit event")
            wb = WriteBatch(raw_mode=True)
            wb.delete(event_id, self.h_default)
            wb.delete(bytes([LEVEL_NUM[old["level"]]]) + event_id, self.h_level)
            wb.delete(old["source"].encode("utf-8") + b"\x00" + event_id, self.h_source)
            trace_id = old["fields"].get("trace_id")
            if trace_id:
                wb.delete(trace_id.encode("utf-8") + b"\x00" + event_id, self.h_trace)
            self._delete_ingest_mapping(wb, event_id)
            updated_total, updated_counts = self._count_delta_batch(wb, -1, {old["level"]: -1})
            self.db.write(wb)
            self._total_events, self._event_counts = updated_total, updated_counts
            return True

    # ------------------------------------------------------------- tai khoan
    @staticmethod
    def _normalize_username(username):
        if not isinstance(username, str) or not username.strip():
            raise ValueError("username khong duoc rong")
        return username.strip().lower()

    def get_user(self, username):
        raw = self.cf_users.get(self._normalize_username(username).encode("utf-8"))
        return json.loads(raw.decode("utf-8")) if raw is not None else None

    def list_users(self):
        it = self.cf_users.iter()
        it.seek_to_first()
        users = []
        while it.valid():
            user = json.loads(it.value().decode("utf-8"))
            user["username"] = it.key().decode("utf-8")
            users.append(user)
            it.next()
        return users

    def put_user(self, username, user):
        username = self._normalize_username(username)
        if not isinstance(user, dict):
            raise ValueError("user phai la object")
        self.cf_users.put(username.encode("utf-8"), json.dumps(
            user, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8"))

    def delete_user(self, username):
        username = self._normalize_username(username).encode("utf-8")
        exists = self.cf_users.get(username) is not None
        if exists:
            self.cf_users.delete(username)
        return exists

    def count(self, start_ms=None, end_ms=None, level_exact=None):
        """Dem su kien tong truc tiep; truy van theo khoang quet index."""
        if start_ms is None and end_ms is None:
            return self._event_counts[level_exact] if level_exact else self._total_events
        lo = make_id(start_ms or 0, 0)
        hi = make_id((end_ms + 1) if end_ms is not None else 0xFFFFFFFFFFFFFFF, 0)
        if level_exact:
            cf, prefix = self.cf_level, bytes([LEVEL_NUM[level_exact]])
            lo, hi = prefix + lo, prefix + hi
        else:
            cf = self.db
        return sum(1 for _ in self._scan(cf, lo, hi))

    def ping(self):
        """Read one metadata key to verify that the RocksDB handles are usable."""
        with self._lock:
            self.cf_meta.get(META_COUNT_VERSION)
        return True

    def count_by_level(self, start_ms=None, end_ms=None):
        if start_ms is None and end_ms is None:
            return self._event_counts.copy()
        return {lv: self.count(start_ms, end_ms, lv) for lv in LEVELS}

    def sources(self):
        with self._lock:
            return sorted(self._known_sources)

    # ------------------------------------------------------------- retention
    def purge_older_than(self, cutoff_ms, compact=False):
        """
        Xoa log thuong cu hon cutoff; audit noi bo duoc giu lai.
        Dung DeleteRange khi khong co audit trong khoang; neu co, xoa log thuong
        theo WriteBatch nho de khong xoa audit va cac index cua no.
        """
        cutoff = make_id(cutoff_ms, 0)
        with self._lock:
            deleted_by_level = dict.fromkeys(LEVELS, 0)
            deleted = 0
            has_expired_audit = False
            expired_ids = []
            it = self.db.iter()
            it.seek_to_first()
            while it.valid() and it.key() < cutoff:
                expired_ids.append(it.key())
                record = json.loads(it.value().decode("utf-8"))
                if is_internal_audit_event(record):
                    has_expired_audit = True
                else:
                    deleted_by_level[record["level"]] += 1
                    deleted += 1
                it.next()
            if not deleted:
                if compact:
                    self.compact()
                return 0

            if has_expired_audit:
                deleted = self._purge_expired_preserving_audit(cutoff)
            else:
                wb = WriteBatch(raw_mode=True)
                wb.delete_range(MIN_ID, cutoff, self.h_default)
                for lv in range(len(LEVELS)):
                    p = bytes([lv])
                    wb.delete_range(p + MIN_ID, p + cutoff, self.h_level)
                for src in self._known_sources:
                    p = src.encode("utf-8") + b"\x00"
                    wb.delete_range(p + MIN_ID, p + cutoff, self.h_source)
                for event_id in expired_ids:
                    self._delete_ingest_mapping(wb, event_id)
                level_deltas = {level: -count for level, count in deleted_by_level.items() if count}
                updated_total, updated_counts = self._count_delta_batch(wb, -deleted, level_deltas)
                self.db.write(wb)
                self._total_events, self._event_counts = updated_total, updated_counts
                self._delete_expired_trace_ranges(cutoff)
        if compact:
            self.compact()
        return deleted

    def _purge_expired_preserving_audit(self, cutoff):
        it = self.db.iter()
        it.seek_to_first()
        batch = WriteBatch(raw_mode=True)
        pending = 0
        pending_levels = dict.fromkeys(LEVELS, 0)
        deleted = 0

        def flush():
            nonlocal batch, pending, pending_levels
            if not pending:
                return
            level_deltas = {level: -count for level, count in pending_levels.items() if count}
            updated_total, updated_counts = self._count_delta_batch(batch, -pending, level_deltas)
            self.db.write(batch)
            self._total_events, self._event_counts = updated_total, updated_counts
            batch = WriteBatch(raw_mode=True)
            pending = 0
            pending_levels = dict.fromkeys(LEVELS, 0)

        while it.valid() and it.key() < cutoff:
            event_id = it.key()
            record = json.loads(it.value().decode("utf-8"))
            if not is_internal_audit_event(record):
                batch.delete(event_id, self.h_default)
                batch.delete(bytes([LEVEL_NUM[record["level"]]]) + event_id, self.h_level)
                batch.delete(record["source"].encode("utf-8") + b"\x00" + event_id, self.h_source)
                trace_id = (record.get("fields") or {}).get("trace_id")
                if trace_id:
                    batch.delete(trace_id.encode("utf-8") + b"\x00" + event_id, self.h_trace)
                self._delete_ingest_mapping(batch, event_id)
                pending_levels[record["level"]] -= 1
                pending += 1
                deleted += 1
                if pending >= 1000:
                    flush()
            it.next()
        flush()
        return deleted

    def _delete_expired_trace_ranges(self, cutoff):
        it = self.cf_trace.iter()
        it.seek_to_first()
        batch = WriteBatch(raw_mode=True)
        ranges = 0
        while it.valid():
            key = it.key()
            separator = key.find(b"\x00")
            if separator < 0 or len(key) - separator - 1 != ID_LEN:
                it.next()
                continue
            prefix = key[:separator + 1]
            if key[separator + 1:] < cutoff:
                batch.delete_range(prefix + MIN_ID, prefix + cutoff, self.h_trace)
                ranges += 1
                if ranges >= 1000:
                    self.db.write(batch)
                    batch = WriteBatch(raw_mode=True)
                    ranges = 0
            it.seek(prefix + b"\xff" * (ID_LEN + 1))
        if ranges:
            self.db.write(batch)

    # --------------------------------------------------------------- bao tri
    def flush(self):
        """Day memtable xuong dia thanh file SST."""
        self.db.flush()
        for cf in (self.cf_level, self.cf_source, self.cf_trace, self.cf_meta, self.cf_users, self.cf_ingest):
            cf.flush()

    def compact(self):
        """Compaction thu cong: gop file SST, thuc su giai phong dung luong sau khi xoa."""
        self.db.compact_range(None, None)
        for cf in (self.cf_level, self.cf_source, self.cf_trace, self.cf_meta, self.cf_users, self.cf_ingest):
            cf.compact_range(None, None)

    def stats(self):
        def prop(cf, name):
            v = cf.property_int_value(name)
            return int(v) if v is not None else 0

        out = {"path": self.path, "column_families": {}}
        for name, cf in (("default", self.db), (CF_INDEX_LEVEL, self.cf_level),
                         (CF_INDEX_SOURCE, self.cf_source), (CF_INDEX_TRACE, self.cf_trace),
                         (CF_META, self.cf_meta), (CF_USERS, self.cf_users),
                         (CF_INGEST, self.cf_ingest)):
            out["column_families"][name] = {
                "estimate_num_keys": prop(cf, "rocksdb.estimate-num-keys"),
                "sst_size_bytes": prop(cf, "rocksdb.total-sst-files-size"),
                "memtable_bytes": prop(cf, "rocksdb.cur-size-all-mem-tables"),
                "l0_files": prop(cf, "rocksdb.num-files-at-level0"),
            }
        out["disk_bytes"] = dir_size(self.path)
        return out

    def backup(self, dest_dir):
        """
        Sao luu bang Checkpoint cua RocksDB: file SST bat bien duoc hard-link nen
        rat nhanh va khong chan viec ghi. dest_dir phai chua ton tai.
        """
        if os.path.exists(dest_dir):
            raise FileExistsError(dest_dir)
        parent = os.path.dirname(os.path.abspath(dest_dir))
        os.makedirs(parent, exist_ok=True)
        Checkpoint(self.db).create_checkpoint(dest_dir)
        return dest_dir

    @staticmethod
    def restore(backup_dir, target_dir):
        """Khoi phuc: copy thu muc checkpoint thanh thu muc du lieu moi."""
        if os.path.exists(target_dir):
            raise FileExistsError(target_dir)
        shutil.copytree(backup_dir, target_dir)
        return target_dir

    def close(self):
        # Phai dong cac column family VA bo cac handle di thi file LOCK moi duoc nha
        # (da gap loi "lock hold by current process" khi mo lai DB neu thieu buoc nay)
        for cf in (self.cf_level, self.cf_source, self.cf_trace, self.cf_meta, self.cf_users, self.cf_ingest):
            cf.close()
        self.db.close()
        self.h_default = self.h_level = self.h_source = self.h_trace = self.h_meta = self.h_users = self.h_ingest = None
        gc.collect()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total
