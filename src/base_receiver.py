"""Base-station receiver: accepts forwarder connections, writes CSV, acks.

Runs on the workstation. One record per line on the socket, one CSV row per
record on disk (or stdout). Duplicate ids after a reconnect are dropped but
still acknowledged, so the node can clear them from its spool.

CSV columns: id, seq, t_us, received_at, payload_hex
"""
from __future__ import annotations

import argparse
import logging
import socket
import socketserver
import sys
import threading
import time
from typing import Callable, Optional

from .protocol import decode_line, encode_ack

log = logging.getLogger("receiver")


class Receiver:
    """Holds receive state shared across connections."""

    def __init__(self, sink: Callable[[dict], None]) -> None:
        self.sink = sink
        self.last_id = 0
        self.records = 0
        self.duplicates = 0
        self.lock = threading.Lock()

    def handle_line(self, line: bytes) -> Optional[bytes]:
        msg = decode_line(line)
        if not msg:
            return None
        if "flush" in msg:
            return encode_ack(int(msg["flush"]))
        rid = int(msg["id"])
        with self.lock:
            if rid <= self.last_id:
                self.duplicates += 1
                return None
            self.last_id = rid
            self.records += 1
            msg["received_at"] = time.time()
            self.sink(msg)
        return None


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        recv: Receiver = self.server.receiver  # type: ignore[attr-defined]
        log.info("forwarder connected from %s", self.client_address[0])
        for line in self.rfile:
            reply = recv.handle_line(line)
            if reply:
                self.wfile.write(reply)
                self.wfile.flush()
        log.info("forwarder disconnected")


class ReceiverServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, receiver: Receiver) -> None:
        super().__init__(addr, _Handler)
        self.receiver = receiver


def csv_sink(out) -> Callable[[dict], None]:
    out.write("id,seq,t_us,received_at,payload_hex\n")
    out.flush()

    def write(msg: dict) -> None:
        out.write(f"{msg['id']},{msg['seq']},{msg['t_us']},{msg['received_at']:.6f},{msg['payload']}\n")
        out.flush()

    return write


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Receive forwarded frames and write CSV")
    ap.add_argument("--listen", default="0.0.0.0:9000", help="host:port to bind")
    ap.add_argument("--out", default="-", help="CSV path, or - for stdout")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    host, port = args.listen.rsplit(":", 1)
    out = sys.stdout if args.out == "-" else open(args.out, "a", buffering=1)
    srv = ReceiverServer((host, int(port)), Receiver(csv_sink(out)))
    log.info("listening on %s:%s", host, port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
