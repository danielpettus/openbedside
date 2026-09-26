"""Store-and-forward. Every outbound message is written to SQLite before any attempt
to send it, and stays there until the receiver returns a positive application ACK.

Order is preserved per destination: if the oldest pending message cannot be
delivered, nothing behind it is sent. An EHR that receives an infusion stop before
the start it belongs to is worse off than one that receives both late.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  destination   TEXT NOT NULL,
  control_id    TEXT NOT NULL,
  message       TEXT NOT NULL,
  created       REAL NOT NULL,
  attempts      INTEGER NOT NULL DEFAULT 0,
  next_attempt  REAL NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending',   -- pending | sent | rejected
  last_result   TEXT
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(destination, status, id);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, device_id TEXT, module_id TEXT,
  channel_id TEXT, kind TEXT, derived INTEGER, detail TEXT
);
"""


@dataclass
class Item:
    id: int
    destination: str
    control_id: str
    message: str
    attempts: int


class Outbox:
    def __init__(self, path: str, max_backoff: float = 300.0):
        self.path = path
        self.max_backoff = max_backoff
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def put(self, destination: str, control_id: str, message: str) -> int:
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO outbox(destination, control_id, message, created, next_attempt) VALUES (?,?,?,?,?)",
                (destination, control_id, message, now, now))
            return cur.lastrowid

    def head(self, destination: str) -> Optional[Item]:
        """The oldest pending message for a destination, if it is due."""
        with self._lock:
            row = self._db.execute(
                "SELECT id, destination, control_id, message, attempts, next_attempt FROM outbox "
                "WHERE destination=? AND status='pending' ORDER BY id LIMIT 1", (destination,)).fetchone()
        if not row or row[5] > time.time():
            return None
        return Item(*row[:5])

    def mark_sent(self, item_id: int, result: str) -> None:
        with self._lock:
            self._db.execute("UPDATE outbox SET status='sent', attempts=attempts+1, last_result=? WHERE id=?",
                             (result, item_id))

    def mark_rejected(self, item_id: int, result: str) -> None:
        """Receiver refused it. Kept for review, never retried, and the queue moves on."""
        with self._lock:
            self._db.execute("UPDATE outbox SET status='rejected', attempts=attempts+1, last_result=? WHERE id=?",
                             (result, item_id))

    def mark_retry(self, item_id: int, attempts: int, result: str) -> float:
        delay = min(self.max_backoff, 2.0 ** min(attempts, 10))
        with self._lock:
            self._db.execute("UPDATE outbox SET attempts=attempts+1, next_attempt=?, last_result=? WHERE id=?",
                             (time.time() + delay, result, item_id))
        return delay

    def counts(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT status, COUNT(*) FROM outbox GROUP BY status").fetchall()
        return {k: v for k, v in rows}

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, destination, control_id, created, attempts, status, last_result FROM outbox "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        keys = ["id", "destination", "control_id", "created", "attempts", "status", "last_result"]
        return [dict(zip(keys, r)) for r in rows]

    def log_event(self, at: str, device_id: str, module_id: str, channel_id: str,
                  kind: str, derived: bool, detail: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO events(at, device_id, module_id, channel_id, kind, derived, detail) "
                             "VALUES (?,?,?,?,?,?,?)", (at, device_id, module_id, channel_id, kind, int(derived), detail))

    def recent_events(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT at, device_id, module_id, channel_id, kind, derived, detail "
                                    "FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        keys = ["at", "device_id", "module_id", "channel_id", "kind", "derived", "detail"]
        return [dict(zip(keys, r)) for r in rows]
