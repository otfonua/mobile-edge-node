"""Binary frame format for microcontroller -> node serial links.

Layout (little endian), all fields after SYNC are covered by the CRC:

    SYNC     2 bytes   0x55 0xAA
    seq      u32       device frame counter
    t_us     u64       device timestamp, microseconds
    length   u16       payload byte count (0..MAX_PAYLOAD)
    payload  length bytes
    crc      u16       CRC-16/CCITT-FALSE over seq..payload

The parser is a byte-stream state machine: feed it whatever the serial port
returns and it yields complete, checksum-valid frames. Garbage between frames
and corrupted frames are skipped by resynchronising on the next SYNC.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator

SYNC = b"\x55\xaa"
HEADER = struct.Struct("<IQH")  # seq, t_us, length
CRC = struct.Struct("<H")
MAX_PAYLOAD = 1024


def crc16(data: bytes, init: int = 0xFFFF) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection)."""
    crc = init
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class Frame:
    seq: int
    t_us: int
    payload: bytes

    def encode(self) -> bytes:
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError(f"payload {len(self.payload)} exceeds {MAX_PAYLOAD}")
        body = HEADER.pack(self.seq, self.t_us, len(self.payload)) + self.payload
        return SYNC + body + CRC.pack(crc16(body))


class FrameParser:
    """Incremental parser. Call feed() with raw bytes, iterate the result."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.frames_ok = 0
        self.frames_bad_crc = 0
        self.frames_bad_length = 0
        self.bytes_skipped = 0

    def feed(self, data: bytes) -> Iterator[Frame]:
        self._buf.extend(data)
        while True:
            start = self._buf.find(SYNC)
            if start < 0:
                # keep a trailing 0x55 in case the 0xAA is still in flight
                keep = 1 if self._buf and self._buf[-1] == SYNC[0] else 0
                self.bytes_skipped += len(self._buf) - keep
                del self._buf[: len(self._buf) - keep]
                return
            if start:
                self.bytes_skipped += start
                del self._buf[:start]
            if len(self._buf) < len(SYNC) + HEADER.size:
                return
            seq, t_us, length = HEADER.unpack_from(self._buf, len(SYNC))
            if length > MAX_PAYLOAD:
                self.frames_bad_length += 1
                del self._buf[:1]
                continue
            total = len(SYNC) + HEADER.size + length + CRC.size
            if len(self._buf) < total:
                return
            body = bytes(self._buf[len(SYNC): total - CRC.size])
            (crc_rx,) = CRC.unpack_from(self._buf, total - CRC.size)
            if crc16(body) != crc_rx:
                self.frames_bad_crc += 1
                del self._buf[:1]
                continue
            payload = body[HEADER.size:]
            del self._buf[:total]
            self.frames_ok += 1
            yield Frame(seq, t_us, payload)

    def stats(self) -> dict:
        return {
            "frames_ok": self.frames_ok,
            "frames_bad_crc": self.frames_bad_crc,
            "frames_bad_length": self.frames_bad_length,
            "bytes_skipped": self.bytes_skipped,
            "buffered": len(self._buf),
        }
