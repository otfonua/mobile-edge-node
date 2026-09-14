"""SQLite write-ahead spool.

Every frame is committed to disk before the caller moves on. The forwarder
reads unacknowledged rows in insertion order and marks them acked only after
the base station confirms receipt. A crash or link drop at any point leaves
the row in place, so the worst case is a re-send, never a loss.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional

from .framing import Frame

SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seq         INTEGER NOT NULL,
    t_us        INTEGER NOT NULL,
    payload     BLOB    NOT NULL,
    received_at REAL    NOT NULL,
    acked       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS frames_pending ON frames (acked, id);
"""


@dataclass(frozen=True)
class Row:
    id: int
    seq: int
    t_us: int
    payload: bytes
    received_at: float


class Spool:
    def __init__(self, path: str, synchronous: str = "NORMAL") -> None:
        self.path = path
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(f"PRAGMA synchronous={synchronous}")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def append(self, frame: Frame, received_at: Optional[float] = None) -> int:
        cur = self._db.execute(
            "INSERT INTO frames (seq, t_us, payload, received_at) VALUES (?, ?, ?, ?)",
            (frame.seq, frame.t_us, frame.payload, received_at or time.time()),
        )
        return int(cur.lastrowid)

    def append_many(self, frames: Iterable[Frame]) -> int:
        now = time.time()
        with self._db:
            self._db.execute("BEGIN")
            n = 0
            for f in frames:
                self._db.execute(
                    "INSERT INTO frames (seq, t_us, payload, received_at) VALUES (?, ?, ?, ?)",
                    (f.seq, f.t_us, f.payload, now),
                )
                n += 1
        return n

    def pending(self, limit: int = 256) -> List[Row]:
        cur = self._db.execute(
            "SELECT id, seq, t_us, payload, received_at FROM frames "
            "WHERE acked = 0 ORDER BY id LIMIT ?",
            (limit,),
        )
        return [Row(*r) for r in cur.fetchall()]

    def ack(self, upto_id: int) -> int:
        cur = self._db.execute(
            "UPDATE frames SET acked = 1 WHERE acked = 0 AND id <= ?", (upto_id,)
        )
        return cur.rowcount

    def pending_count(self) -> int:
        (n,) = self._db.execute("SELECT COUNT(*) FROM frames WHERE acked = 0").fetchone()
        return int(n)

    def total_count(self) -> int:
        (n,) = self._db.execute("SELECT COUNT(*) FROM frames").fetchone()
        return int(n)

    def prune_acked(self, keep_last: int = 0) -> int:
        """Delete acked rows, optionally keeping the most recent `keep_last`."""
        cur = self._db.execute(
            "DELETE FROM frames WHERE acked = 1 AND id <= "
            "(SELECT COALESCE(MAX(id), 0) - ? FROM frames)",
            (keep_last,),
        )
        return cur.rowcount

    def stats(self) -> dict:
        return {
            "path": self.path,
            "total": self.total_count(),
            "pending": self.pending_count(),
        }
