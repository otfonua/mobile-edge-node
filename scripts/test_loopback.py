#!/usr/bin/env python3
"""Hardware-free end-to-end check.

Generates frames at --rate Hz for --duration seconds through a simulated
serial byte stream (random chunking, injected corruption), ingests them into
a spool, forwards to a local receiver over TCP, and cuts the link every
--cut-every seconds. Exit 0 if every valid frame reached the receiver exactly
once and in order.

    python3 scripts/test_loopback.py --rate 100 --duration 5
"""
from __future__ import annotations

import argparse
import os
import random
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.base_receiver import Receiver, ReceiverServer  # noqa: E402
from src.framing import Frame, FrameParser  # noqa: E402
from src.spool import Spool  # noqa: E402
from src.telemetry_bridge import Forwarder  # noqa: E402


class Killable(ReceiverServer):
    def __init__(self, addr, receiver):
        super().__init__(addr, receiver)
        self.conns, self.lock = [], threading.Lock()

    def process_request(self, request, client_address):
        with self.lock:
            self.conns.append(request)
        super().process_request(request, client_address)

    def sever(self):
        with self.lock:
            for c in self.conns:
                try:
                    c.shutdown(socket.SHUT_RDWR)
                    c.close()
                except OSError:
                    pass
            self.conns.clear()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=100.0, help="frames per second")
    ap.add_argument("--duration", type=float, default=5.0, help="seconds")
    ap.add_argument("--cut-every", type=float, default=1.0, help="sever link every N s (0 = never)")
    ap.add_argument("--corrupt", type=float, default=0.01, help="fraction of frames to corrupt")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    got = []
    recv = Receiver(sink=got.append)
    srv = Killable(("127.0.0.1", 0), recv)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    with tempfile.TemporaryDirectory() as d:
        spool = Spool(os.path.join(d, "loop.db"))
        fwd = Forwarder(spool, "127.0.0.1", srv.server_address[1], batch=128,
                        backoff_min=0.05, backoff_max=0.5)
        threading.Thread(target=fwd.run, daemon=True).start()

        parser = FrameParser()
        n_total = int(args.rate * args.duration)
        period = 1.0 / args.rate
        sent_valid, corrupted = [], 0
        pending_bytes = b""
        t0 = time.time()
        next_cut = t0 + args.cut_every if args.cut_every > 0 else float("inf")
        for i in range(n_total):
            f = Frame(seq=i, t_us=int((time.time() - t0) * 1e6), payload=os.urandom(rng.randrange(4, 64)))
            enc = bytearray(f.encode())
            if rng.random() < args.corrupt:
                enc[rng.randrange(2, len(enc))] ^= 0xFF
                corrupted += 1
            else:
                sent_valid.append(f)
            pending_bytes += bytes(enc)
            # deliver in random chunk sizes like a real serial read
            while pending_bytes and rng.random() < 0.7:
                n = rng.randrange(1, 200)
                for fr in parser.feed(pending_bytes[:n]):
                    spool.append(fr)
                pending_bytes = pending_bytes[n:]
            now = time.time()
            if now >= next_cut:
                srv.sever()
                next_cut = now + args.cut_every
            sleep_for = t0 + (i + 1) * period - now
            if sleep_for > 0:
                time.sleep(sleep_for)
        for fr in parser.feed(pending_bytes):
            spool.append(fr)

        deadline = time.time() + 15
        while spool.pending_count() and time.time() < deadline:
            time.sleep(0.05)
        fwd.stop()
        pending_left = spool.pending_count()
        pstats, fstats = parser.stats(), fwd.stats()

    srv.shutdown()
    srv.server_close()

    ids = [m["id"] for m in got]
    seqs = [m["seq"] for m in got]
    ok = (
        pending_left == 0
        and ids == list(range(1, len(sent_valid) + 1))
        and seqs == [f.seq for f in sent_valid]
        and pstats["frames_bad_crc"] == corrupted
    )
    print(f"generated      {n_total}")
    print(f"corrupted      {corrupted}  (parser rejected {pstats['frames_bad_crc']})")
    print(f"valid spooled  {len(sent_valid)}")
    print(f"received       {recv.records}  duplicates dropped {recv.duplicates}")
    print(f"link cuts      {fwd.reconnects}")
    print(f"pending left   {pending_left}")
    print("RESULT         " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
