import os
import random

import pytest

from src.framing import CRC, HEADER, MAX_PAYLOAD, SYNC, Frame, FrameParser, crc16


def test_crc16_known_vector():
    # CRC-16/CCITT-FALSE check value for "123456789"
    assert crc16(b"123456789") == 0x29B1


def test_roundtrip_single():
    f = Frame(seq=7, t_us=123_456_789, payload=b"\x01\x02\x03")
    p = FrameParser()
    out = list(p.feed(f.encode()))
    assert out == [f]
    assert p.stats()["frames_ok"] == 1


def test_roundtrip_byte_at_a_time():
    frames = [Frame(i, i * 1000, bytes([i % 256]) * (i % 40)) for i in range(50)]
    stream = b"".join(f.encode() for f in frames)
    p = FrameParser()
    out = []
    for b in stream:
        out.extend(p.feed(bytes([b])))
    assert out == frames


def test_roundtrip_random_chunking():
    rng = random.Random(1)
    frames = [Frame(i, i, os.urandom(rng.randrange(0, 200))) for i in range(300)]
    stream = b"".join(f.encode() for f in frames)
    p = FrameParser()
    out, i = [], 0
    while i < len(stream):
        n = rng.randrange(1, 97)
        out.extend(p.feed(stream[i:i + n]))
        i += n
    assert out == frames
    assert p.stats()["bytes_skipped"] == 0


def test_garbage_between_frames_is_skipped():
    a, b = Frame(1, 1, b"a"), Frame(2, 2, b"b")
    stream = b"\x00\xff\x55" + a.encode() + b"noise\x55\x55" + b.encode() + b"\xaa\x55"
    p = FrameParser()
    out = list(p.feed(stream))
    assert out == [a, b]
    assert p.stats()["bytes_skipped"] > 0


def test_corrupt_crc_is_isolated():
    frames = [Frame(i, i, b"x" * 8) for i in range(5)]
    enc = [bytearray(f.encode()) for f in frames]
    enc[2][10] ^= 0x01  # flip one payload bit in frame 2
    p = FrameParser()
    out = list(p.feed(b"".join(bytes(e) for e in enc)))
    assert [f.seq for f in out] == [0, 1, 3, 4]
    assert p.stats()["frames_bad_crc"] == 1


def test_corrupt_length_is_isolated():
    frames = [Frame(i, i, b"y" * 4) for i in range(3)]
    enc = [bytearray(f.encode()) for f in frames]
    # length field sits after SYNC(2) + seq(4) + t_us(8)
    off = len(SYNC) + 12
    enc[1][off:off + 2] = (MAX_PAYLOAD + 5).to_bytes(2, "little")
    p = FrameParser()
    out = list(p.feed(b"".join(bytes(e) for e in enc)))
    assert [f.seq for f in out] == [0, 2]
    assert p.stats()["frames_bad_length"] == 1


def test_payload_limit():
    with pytest.raises(ValueError):
        Frame(0, 0, b"z" * (MAX_PAYLOAD + 1)).encode()
    f = Frame(0, 0, b"z" * MAX_PAYLOAD)
    assert list(FrameParser().feed(f.encode())) == [f]


def test_sync_split_across_reads():
    f = Frame(9, 9, b"q")
    enc = f.encode()
    p = FrameParser()
    out = list(p.feed(b"\x55"))          # first sync byte alone
    out += list(p.feed(enc[1:]))         # rest arrives later
    assert out == [f]
