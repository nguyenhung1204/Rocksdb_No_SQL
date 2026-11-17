"""Opt-in, single-process maintenance for the local RocksDB application."""

import os
import re
import shutil
import threading
import time
from pathlib import Path


BACKUP_NAME_RE = re.compile(r"^backup-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$")
MANIFEST_RE = re.compile(r"^MANIFEST-[0-9]+$")


def retention_settings(environ=None):
    env = os.environ if environ is None else environ
    values = (
        ("LOGSTORE_RETENTION_HOURS", 0, 0, 24 * 3650),
        ("LOGSTORE_RETENTION_INTERVAL_SECONDS", 3600, 1, 86400),
        ("LOGSTORE_BACKUP_RETENTION_COUNT", 0, 0, 10000),
    )
    parsed = []
    for name, default, minimum, maximum in values:
        raw = env.get(name, str(default))
        try:
            value = int(raw)
        except (TypeError, ValueError) as error:
            raise ValueError("%s phai la so nguyen" % name) from error
        if not minimum <= value <= maximum:
            raise ValueError("%s phai trong khoang %d-%d" % (name, minimum, maximum))
        parsed.append(value)
    return dict(zip(("hours", "interval_seconds", "backup_count"), parsed))


class RetentionWorker:
    """Run retention in one stoppable daemon thread against the active store."""

    def __init__(self, app, hours, interval_seconds):
        self.app = app
        self.hours = int(hours)
        self.interval_seconds = int(interval_seconds)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="logstore-retention", daemon=True)
        self._started = False

    def start(self):
        if self.hours <= 0:
            return False
        self.thread.start()
        self._started = True
        return True

    def _run(self):
        while not self._stop.wait(self.interval_seconds):
            self.run_once()

    def run_once(self):
        cutoff_ms = int(time.time() * 1000) - self.hours * 3_600_000
        try:
            with self.app.extensions["store_lock"]:
                store = self.app.extensions["logstore"]
                deleted = store.purge_older_than(cutoff_ms)
            self.app.logger.info("Scheduled retention complete: deleted=%d cutoff_ms=%d", deleted, cutoff_ms)
            return deleted
        except Exception:
            self.app.logger.warning("Scheduled retention failed", exc_info=True)
            return None

    def stop(self):
        self._stop.set()
        if self._started and self.thread.is_alive():
            self.thread.join()


def start_retention_worker(app):
    hours = app.config["RETENTION_HOURS"]
    if not hours:
        return None
    worker = RetentionWorker(app, hours, app.config["RETENTION_INTERVAL_SECONDS"])
    worker.start()
    return worker


def _is_junction(path):
    check = getattr(path, "is_junction", None)
    return bool(check and check())


def _valid_checkpoint(path, root):
    try:
        if not BACKUP_NAME_RE.fullmatch(path.name) or path.is_symlink() or _is_junction(path):
            return False
        resolved = path.resolve(strict=True)
        if resolved.parent != root or not resolved.is_dir():
            return False
        current = resolved / "CURRENT"
        if current.is_symlink() or not current.is_file() or current.resolve(strict=True).parent != root / path.name:
            return False
        manifest_name = current.read_text(encoding="ascii").strip()
        if not MANIFEST_RE.fullmatch(manifest_name):
            return False
        manifest = resolved / manifest_name
        if (manifest.is_symlink() or not manifest.is_file()
                or manifest.resolve(strict=True).parent != resolved):
            return False

        # File names alone do not prove that the manifest-referenced SSTs exist.
        # Open the checkpoint read-only so RocksDB validates its current version.
        from rocksdict import AccessType, Rdict
        from logstore.store import _base_options

        db_options = _base_options()
        db_options.create_if_missing(False)
        column_families = {
            name: _base_options() for name in Rdict.list_cf(str(resolved), db_options)
            if name != "default"
        }
        with Rdict(
            str(resolved), db_options, column_families=column_families,
            access_type=AccessType.read_only(error_if_log_file_exist=False),
        ):
            pass
        return True
    except Exception:
        return False


def prune_checkpoints(backup_root, keep_count, newest_path=None):
    """Remove only old, valid, directly-contained named RocksDB checkpoints."""
    if not isinstance(keep_count, int) or isinstance(keep_count, bool) or keep_count < 0:
        raise ValueError("keep_count phai la so nguyen khong am")
    if keep_count == 0:
        return 0
    root = Path(backup_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("backup_root phai la thu muc")
    valid = [path for path in root.iterdir() if _valid_checkpoint(path, root)]
    valid.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    if not valid:
        return 0
    keep = set()
    if newest_path is not None:
        candidate = Path(newest_path).resolve(strict=False)
        valid_paths = {path.resolve() for path in valid}
        if candidate.parent != root or candidate not in valid_paths:
            return 0
        keep.add(candidate)
    if not keep:
        keep.add(valid[0].resolve())
    for path in valid:
        if len(keep) >= keep_count:
            break
        keep.add(path.resolve())
    removed = 0
    for path in valid:
        resolved = path.resolve()
        if resolved in keep or resolved.parent != root:
            continue
        shutil.rmtree(resolved)
        removed += 1
    return removed
