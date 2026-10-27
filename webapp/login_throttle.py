"""Bounded in-memory login throttling for the single-process local app."""

import math
import threading
import time
from collections import OrderedDict


class LoginThrottle:
    def __init__(self, account_limit=5, client_limit=25, window_seconds=60,
                 cooldown_seconds=60, max_entries=10000, clock=None):
        self.account_limit = int(account_limit)
        self.client_limit = int(client_limit)
        self.window_seconds = int(window_seconds)
        self.cooldown_seconds = int(cooldown_seconds)
        self.max_entries = int(max_entries)
        self.clock = clock or time.monotonic
        if min(self.account_limit, self.client_limit, self.window_seconds,
               self.cooldown_seconds, self.max_entries) < 1:
            raise ValueError("login throttle limits and durations must be positive")
        self._entries = OrderedDict()
        self._lock = threading.Lock()

    @property
    def size(self):
        with self._lock:
            return len(self._entries)

    @staticmethod
    def _keys(username, client_address):
        account = str(username or "").strip().lower()[:128]
        client = str(client_address or "unknown").strip()[:128] or "unknown"
        return ("account", account), ("client", client)

    def _trim(self, now):
        expire_after = self.window_seconds + self.cooldown_seconds
        while self._entries:
            key, entry = next(iter(self._entries.items()))
            if now - entry["last_seen"] <= expire_after or entry["blocked_until"] > now:
                break
            self._entries.pop(key, None)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def retry_after(self, username, client_address):
        now = self.clock()
        retry = 0
        with self._lock:
            for key in self._keys(username, client_address):
                entry = self._entries.get(key)
                if entry is None:
                    continue
                if entry["blocked_until"] > now:
                    retry = max(retry, math.ceil(entry["blocked_until"] - now))
                elif entry["blocked_until"]:
                    entry.update(count=0, first_failure=now, blocked_until=0)
                elif now - entry["first_failure"] >= self.window_seconds:
                    entry.update(count=0, first_failure=now)
                entry["last_seen"] = now
                self._entries.move_to_end(key)
            self._trim(now)
        return retry

    def record_failure(self, username, client_address):
        now = self.clock()
        thresholds = (self.account_limit, self.client_limit)
        with self._lock:
            for key, threshold in zip(self._keys(username, client_address), thresholds):
                entry = self._entries.get(key)
                if (entry is None or entry["blocked_until"] <= now and entry["blocked_until"]
                        or now - entry["first_failure"] >= self.window_seconds):
                    entry = {"count": 0, "first_failure": now, "last_seen": now, "blocked_until": 0}
                entry["count"] += 1
                entry["last_seen"] = now
                if entry["count"] >= threshold:
                    entry["blocked_until"] = now + self.cooldown_seconds
                self._entries[key] = entry
                self._entries.move_to_end(key)
            self._trim(now)

    def clear_account(self, username):
        key = ("account", str(username or "").strip().lower()[:128])
        now = self.clock()
        with self._lock:
            self._entries.pop(key, None)
            self._trim(now)
