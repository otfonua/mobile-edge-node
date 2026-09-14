"""Wire protocol between the node forwarder and the base receiver.

Newline-delimited JSON over one TCP stream. The forwarder sends a batch of
records followed by a flush marker. The receiver replies with one ack for the
flush. Records carry the spool row id, which is monotonic on the node, so the
receiver can drop duplicates after a reconnect by ignoring ids it has seen.

    node -> base:  {"id": 41, "seq": 7, "t_us": 123456, "payload": "0a0b"}\n
    node -> base:  {"flush": 41}\n
    base -> node:  {"ack": 41}\n
"""
from __future__ import annotations

import json
from typing import Optional

from .spool import Row


def encode_record(row: Row) -> bytes:
    return (
        json.dumps(
            {"id": row.id, "seq": row.seq, "t_us": row.t_us, "payload": row.payload.hex()},
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def encode_flush(last_id: int) -> bytes:
    return (json.dumps({"flush": last_id}) + "\n").encode()


def encode_ack(last_id: int) -> bytes:
    return (json.dumps({"ack": last_id}) + "\n").encode()


def decode_line(line: bytes) -> Optional[dict]:
    line = line.strip()
    if not line:
        return None
    return json.loads(line.decode())
