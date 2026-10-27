"""
store.py - Lop LogStore: bo luu tru nhat ky/su kien tren RocksDB.

Thiet ke tong quan (xem them trong bao cao):

  Column family "default"     : du lieu chinh   key = event_id            value = JSON
  Column family "idx_level"   : index theo muc  key = level(1B) + event_id   value = rong
  Column family "idx_source"  : index theo nguon key = source + 0x00 + event_id   value = rong
  Column family "idx_trace"   : index theo trace key = trace_id + 0x00 + event_id  value = rong
  Column family "meta"        : danh sach cac source da gap (de xoa theo retention)
  Column family "users"       : tai khoan, password hash, vai tro va trang thai

event_id = 8 byte timestamp (ms, big-endian) + 4 byte so thu tu (big-endian)
=> so sanh byte-by-byte cung chinh la thu tu thoi gian, nen RocksDB (luu key da
sap xep) quet theo khoang thoi gian rat nhanh, khong can sort lai.
"""

import gc
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

CF_INDEX_LEVEL = "idx_level"
CF_INDEX_SOURCE = "idx_source"
CF_INDEX_TRACE = "idx_trace"
CF_META = "meta"
CF_USERS = "users"

ID_STRUCT = struct.Struct(">QI")  # 8 byte ts_ms + 4 byte seq
ID_LEN = ID_STRUCT.size  # 12
MIN_ID = ID_STRUCT.pack(0, 0)


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
        }
        self.db = Rdict(path, db_opts, column_families=cfs)
        self.cf_level = self.db.get_column_family(CF_INDEX_LEVEL)
        self.cf_source = self.db.get_column_family(CF_INDEX_SOURCE)
        self.cf_trace = self.db.get_column_family(CF_INDEX_TRACE)
        self.cf_meta = self.db.get_column_family(CF_META)
        self.cf_users = self.db.get_column_family(CF_USERS)
        self.h_default = self.db.get_column_family_handle("default")
        self.h_level = self.db.get_column_family_handle(CF_INDEX_LEVEL)
        self.h_source = self.db.get_column_family_handle(CF_INDEX_SOURCE)
        self.h_trace = self.db.get_column_family_handle(CF_INDEX_TRACE)
        self.h_meta = self.db.get_column_family_handle(CF_META)
        self.h_users = self.db.get_column_family_handle(CF_USERS)

        wo = WriteOptions()
        wo.sync = bool(sync_writes)
        self.db.set_write_options(wo)

        # Nho ID cuoi cung de cap phat nhanh cho timestamp moi nhat.
        self._last_ts, self._last_seq = self._load_last_id()
        self._late_sequences = {}
        self._known_sources = set(self._load_sources())
        self._known_traces = set(self._load_traces())

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
            out.append(it.key().decode("utf-8"))
            it.next()
        return out

    def _load_traces(self):
        it = self.cf_trace.iter()
        it.seek_to_first()
        out = set()
        while it.valid():
            key = it.key()
            separator = key.find(b"\x00")
            if separator >= 0:
                out.add(key[:separator].decode("utf-8"))
            it.next()
        return out

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
        ids = []
        wb = WriteBatch(raw_mode=True)
        new_sources = set()
        new_traces = set()
        with self._lock:
            for level, source, msg, fields, ts_ms in events:
                self._check_event(level, source, msg, fields)
                if ts_ms is None:
                    ts_ms = int(time.time() * 1000)
                event_id, _ = self._next_id(ts_ms)
                self._add_to_batch(wb, event_id, level, source, msg, fields)
                if source not in self._known_sources:
                    new_sources.add(source)
                trace_id = (fields or {}).get("trace_id")
                if trace_id:
                    new_traces.add(trace_id)
                ids.append(event_id)
            self.db.write(wb)
            self._known_sources |= new_sources
            self._known_traces |= new_traces
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

    def query(self, start_ms=None, end_ms=None, level=None, source=None,
              contains=None, limit=100, reverse=True, trace_id=None):
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

        lo = make_id(max(start_ms, 0) if start_ms is not None else 0, 0)
        hi = make_id((end_ms + 1) if end_ms is not None else 0xFFFFFFFFFFFFFFF, 0)
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

            wb = WriteBatch(raw_mode=True)
            old_trace = old["fields"].get("trace_id")
            new_trace = fields.get("trace_id")
            wb.delete(bytes([LEVEL_NUM[old["level"]]]) + event_id, self.h_level)
            wb.delete(old["source"].encode("utf-8") + b"\x00" + event_id, self.h_source)
            if old_trace:
                wb.delete(old_trace.encode("utf-8") + b"\x00" + event_id, self.h_trace)
            self._add_to_batch(wb, event_id, level, source, msg, fields)
            self.db.write(wb)
            self._known_sources.add(source)
            if new_trace:
                self._known_traces.add(new_trace)
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
            wb = WriteBatch(raw_mode=True)
            wb.delete(event_id, self.h_default)
            wb.delete(bytes([LEVEL_NUM[old["level"]]]) + event_id, self.h_level)
            wb.delete(old["source"].encode("utf-8") + b"\x00" + event_id, self.h_source)
            trace_id = old["fields"].get("trace_id")
            if trace_id:
                wb.delete(trace_id.encode("utf-8") + b"\x00" + event_id, self.h_trace)
            self.db.write(wb)
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
        """Dem so su kien (quet index, khong doc value)."""
        lo = make_id(start_ms or 0, 0)
        hi = make_id((end_ms + 1) if end_ms is not None else 0xFFFFFFFFFFFFFFF, 0)
        if level_exact:
            cf, prefix = self.cf_level, bytes([LEVEL_NUM[level_exact]])
            lo, hi = prefix + lo, prefix + hi
        else:
            cf = self.db
        return sum(1 for _ in self._scan(cf, lo, hi))

    def count_by_level(self, start_ms=None, end_ms=None):
        return {lv: self.count(start_ms, end_ms, lv) for lv in LEVELS}

    def sources(self):
        return sorted(self._known_sources)

    # ------------------------------------------------------------- retention
    def purge_older_than(self, cutoff_ms, compact=False):
        """
        Xoa moi su kien co timestamp < cutoff_ms (chinh sach luu giu - retention).
        Dung DeleteRange: chi ghi 1 "range tombstone" cho moi khoang, rat re so
        voi xoa tung key. Dung lai WriteBatch de xoa du lieu + index cung luc.
        Tra ve so su kien da xoa.
        """
        cutoff = make_id(cutoff_ms, 0)
        deleted = self.count(end_ms=cutoff_ms - 1) if cutoff_ms > 0 else 0
        wb = WriteBatch(raw_mode=True)
        with self._lock:
            wb.delete_range(MIN_ID, cutoff, self.h_default)
            for lv in range(len(LEVELS)):
                p = bytes([lv])
                wb.delete_range(p + MIN_ID, p + cutoff, self.h_level)
            for src in self._known_sources:
                p = src.encode("utf-8") + b"\x00"
                wb.delete_range(p + MIN_ID, p + cutoff, self.h_source)
            for trace_id in self._known_traces:
                p = trace_id.encode("utf-8") + b"\x00"
                wb.delete_range(p + MIN_ID, p + cutoff, self.h_trace)
            self.db.write(wb)
        if compact:
            self.compact()
        return deleted

    # --------------------------------------------------------------- bao tri
    def flush(self):
        """Day memtable xuong dia thanh file SST."""
        self.db.flush()
        for cf in (self.cf_level, self.cf_source, self.cf_trace, self.cf_meta, self.cf_users):
            cf.flush()

    def compact(self):
        """Compaction thu cong: gop file SST, thuc su giai phong dung luong sau khi xoa."""
        self.db.compact_range(None, None)
        for cf in (self.cf_level, self.cf_source, self.cf_trace, self.cf_meta, self.cf_users):
            cf.compact_range(None, None)

    def stats(self):
        def prop(cf, name):
            v = cf.property_int_value(name)
            return int(v) if v is not None else 0

        out = {"path": self.path, "column_families": {}}
        for name, cf in (("default", self.db), (CF_INDEX_LEVEL, self.cf_level),
                         (CF_INDEX_SOURCE, self.cf_source), (CF_INDEX_TRACE, self.cf_trace),
                         (CF_META, self.cf_meta), (CF_USERS, self.cf_users)):
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
        for cf in (self.cf_level, self.cf_source, self.cf_trace, self.cf_meta, self.cf_users):
            cf.close()
        self.db.close()
        self.h_default = self.h_level = self.h_source = self.h_trace = self.h_meta = self.h_users = None
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
