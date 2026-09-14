"""Serial ingestion daemon: USB serial -> frame parser -> spool.

Two ways to open the device:

  --port /dev/ttyACM0      pyserial, works on Linux and rooted Android
  --fd                     read an already-open file descriptor passed by
                           termux-usb -e (Android, no root, no re-prompt)

Every valid frame is committed to the spool before the next read.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import BinaryIO, Iterator

from .framing import FrameParser
from .spool import Spool

log = logging.getLogger("ingest")


def open_pyserial(port: str, baud: int, timeout: float):
    try:
        import serial  # type: ignore
    except ImportError as e:
        raise SystemExit("pyserial is required for --port; pip install pyserial") from e
    return serial.Serial(port, baudrate=baud, timeout=timeout)


def open_fd(fd: int) -> BinaryIO:
    return os.fdopen(fd, "rb", buffering=0)


def read_chunks(dev, chunk: int) -> Iterator[bytes]:
    while True:
        data = dev.read(chunk)
        if data:
            yield data
        else:
            time.sleep(0.001)


def ingest(dev, spool: Spool, parser: FrameParser, chunk: int = 4096,
           report_every: float = 5.0, max_frames: int = 0) -> int:
    n = 0
    last_report = time.time()
    for data in read_chunks(dev, chunk):
        for frame in parser.feed(data):
            spool.append(frame)
            n += 1
            if max_frames and n >= max_frames:
                return n
        now = time.time()
        if now - last_report >= report_every:
            log.info("frames=%d parser=%s spool=%s", n, parser.stats(), spool.stats())
            last_report = now
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ingest framed serial data into the spool")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--port", help="serial device path, e.g. /dev/ttyACM0")
    src.add_argument("--fd", type=int, nargs="?", const=-1,
                     help="read from this fd; with no value, take it from argv[-1] as termux-usb -e passes it")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--spool", default="spool/field.db")
    ap.add_argument("--chunk", type=int, default=4096)
    ap.add_argument("-v", "--verbose", action="store_true")
    args, rest = ap.parse_known_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.port:
        dev = open_pyserial(args.port, args.baud, timeout=0.05)
    else:
        fd = args.fd if args.fd >= 0 else int(rest[-1]) if rest else -1
        if fd < 0:
            raise SystemExit("--fd needs a descriptor number (termux-usb -e appends it)")
        dev = open_fd(fd)

    os.makedirs(os.path.dirname(args.spool) or ".", exist_ok=True)
    spool = Spool(args.spool)
    parser = FrameParser()
    log.info("ingesting into %s", args.spool)
    try:
        ingest(dev, spool, parser, chunk=args.chunk)
    except KeyboardInterrupt:
        pass
    finally:
        log.info("final parser=%s spool=%s", parser.stats(), spool.stats())
        spool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
