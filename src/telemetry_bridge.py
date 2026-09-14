"""Forwarder: drains the spool to the base station over TCP.

Runs on the node. Connects to the receiver, sends pending rows in batches,
waits for the ack, marks them acked in the spool, repeats. Any socket error
closes the connection and the loop reconnects with backoff. Nothing is marked
acked until the receiver says so, so a drop at any point only causes a
re-send.
"""
from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
from typing import Optional

from .protocol import decode_line, encode_flush, encode_record
from .spool import Spool

log = logging.getLogger("bridge")


class Forwarder:
    def __init__(
        self,
        spool: Spool,
        host: str,
        port: int,
        batch: int = 256,
        timeout: float = 5.0,
        backoff_min: float = 0.2,
        backoff_max: float = 10.0,
    ) -> None:
        self.spool = spool
        self.host = host
        self.port = port
        self.batch = batch
        self.timeout = timeout
        self.backoff_min = backoff_min
        self.backoff_max = backoff_max
        self.sock: Optional[socket.socket] = None
        self.rfile = None
        self.stop_event = threading.Event()
        self.reconnects = 0
        self.batches_acked = 0
        self.rows_acked = 0

    # --- connection -----------------------------------------------------
    def connect(self) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.rfile = self.sock.makefile("rb")
        log.info("connected to %s:%d", self.host, self.port)

    def disconnect(self) -> None:
        if self.rfile is not None:
            try:
                self.rfile.close()
            except OSError:
                pass
            self.rfile = None
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # --- one batch ------------------------------------------------------
    def send_batch(self) -> int:
        """Send one batch and wait for its ack. Returns rows acked (0 if idle).

        Raises OSError / ValueError on any link problem; caller reconnects.
        """
        rows = self.spool.pending(self.batch)
        if not rows:
            return 0
        assert self.sock is not None and self.rfile is not None
        last_id = rows[-1].id
        buf = b"".join(encode_record(r) for r in rows) + encode_flush(last_id)
        self.sock.sendall(buf)
        line = self.rfile.readline()
        if not line:
            raise ConnectionError("receiver closed connection before ack")
        msg = decode_line(line)
        if not msg or msg.get("ack") != last_id:
            raise ValueError(f"bad ack {msg!r} for flush {last_id}")
        n = self.spool.ack(last_id)
        self.batches_acked += 1
        self.rows_acked += n
        return n

    # --- loop -----------------------------------------------------------
    def run(self, idle_sleep: float = 0.05) -> None:
        backoff = self.backoff_min
        while not self.stop_event.is_set():
            try:
                if self.sock is None:
                    self.connect()
                    backoff = self.backoff_min
                n = self.send_batch()
                if n == 0:
                    time.sleep(idle_sleep)
            except (OSError, ValueError) as e:
                log.warning("link error: %s; reconnecting in %.1fs", e, backoff)
                self.disconnect()
                self.reconnects += 1
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, self.backoff_max)
        self.disconnect()

    def stop(self) -> None:
        self.stop_event.set()

    def stats(self) -> dict:
        return {
            "connected": self.sock is not None,
            "reconnects": self.reconnects,
            "batches_acked": self.batches_acked,
            "rows_acked": self.rows_acked,
            "pending": self.spool.pending_count(),
        }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Forward spooled frames to the base receiver")
    ap.add_argument("--spool", required=True, help="path to spool .db")
    ap.add_argument("--host", required=True, help="base receiver address (tailnet IP)")
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    fwd = Forwarder(Spool(args.spool), args.host, args.port, batch=args.batch)
    try:
        fwd.run()
    except KeyboardInterrupt:
        fwd.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
