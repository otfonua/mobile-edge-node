import sqlite3

from src.framing import Frame
from src.spool import Spool


def frames(n, start=0):
    return [Frame(seq=i, t_us=i * 10, payload=bytes([i & 0xFF]) * 3) for i in range(start, start + n)]


def test_append_and_pending_order(tmp_path):
    s = Spool(str(tmp_path / "s.db"))
    for f in frames(10):
        s.append(f)
    rows = s.pending(limit=100)
    assert [r.seq for r in rows] == list(range(10))
    assert [r.id for r in rows] == list(range(1, 11))
    assert s.pending_count() == 10


def test_ack_marks_prefix_only(tmp_path):
    s = Spool(str(tmp_path / "s.db"))
    s.append_many(frames(10))
    assert s.ack(4) == 4
    rows = s.pending(limit=100)
    assert [r.id for r in rows] == list(range(5, 11))
    assert s.ack(4) == 0  # idempotent


def test_wal_mode_and_durability_across_reopen(tmp_path):
    path = str(tmp_path / "s.db")
    s = Spool(path)
    mode = sqlite3.connect(path).execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    s.append_many(frames(5))
    s.ack(2)
    s.close()  # simulate process death after commit
    s2 = Spool(path)
    assert s2.total_count() == 5
    assert [r.id for r in s2.pending()] == [3, 4, 5]


def test_batch_limit(tmp_path):
    s = Spool(str(tmp_path / "s.db"))
    s.append_many(frames(50))
    assert len(s.pending(limit=8)) == 8
    assert s.pending(limit=8)[0].id == 1


def test_prune_acked(tmp_path):
    s = Spool(str(tmp_path / "s.db"))
    s.append_many(frames(20))
    s.ack(15)
    assert s.prune_acked(keep_last=10) == 10  # ids 1..10 deleted, 11..15 kept (within last 10)
    assert s.total_count() == 10
    assert s.pending_count() == 5


def test_device_seq_reset_does_not_reorder(tmp_path):
    """Device reboots and restarts seq at 0; spool order must follow arrival."""
    s = Spool(str(tmp_path / "s.db"))
    s.append_many(frames(3, start=100))
    s.append_many(frames(3, start=0))
    assert [r.seq for r in s.pending()] == [100, 101, 102, 0, 1, 2]
