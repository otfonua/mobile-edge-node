"""End-to-end: spool -> forwarder -> TCP -> receiver, with the link cut mid-stream."""
import socket
import socketserver
import threading
import time

from src.base_receiver import Receiver, ReceiverServer, _Handler
from src.framing import Frame
from src.spool import Spool
from src.telemetry_bridge import Forwarder


class _CuttingHandler(_Handler):
    """Drops the connection instead of acking, for the first N flushes."""

    def handle(self):
        srv = self.server
        recv = srv.receiver
        for line in self.rfile:
            if b'"flush"' in line and srv.cuts_remaining > 0:
                srv.cuts_remaining -= 1
                srv.cuts_done += 1
                return  # close without ack: forwarder must resend this batch
            reply = recv.handle_line(line)
            if reply:
                self.wfile.write(reply)
                self.wfile.flush()


class CuttingServer(ReceiverServer):
    def __init__(self, addr, receiver, cuts):
        socketserver.ThreadingTCPServer.__init__(self, addr, _CuttingHandler)
        self.receiver = receiver
        self.cuts_remaining = cuts
        self.cuts_done = 0


def start_server(cuts):
    got = []
    recv = Receiver(sink=got.append)
    srv = CuttingServer(("127.0.0.1", 0), recv, cuts)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, recv, got


def wait_until(pred, timeout=10.0, step=0.01):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(step)
    return False


def test_drain_with_repeated_link_cuts(tmp_path):
    """Receiver drops the link on the first 5 flushes: batches were delivered but
    never acked, so the forwarder must reconnect and resend, and the receiver
    must drop the duplicates. Final sink content must be exact."""
    cuts = 5
    srv, recv, got = start_server(cuts)
    spool = Spool(str(tmp_path / "s.db"))
    total = 2000
    spool.append_many(Frame(i, i * 100, i.to_bytes(4, "little")) for i in range(total))

    fwd = Forwarder(spool, "127.0.0.1", srv.server_address[1], batch=64,
                    backoff_min=0.01, backoff_max=0.05)
    ft = threading.Thread(target=fwd.run, daemon=True)
    ft.start()

    assert wait_until(lambda: spool.pending_count() == 0), fwd.stats()
    fwd.stop()
    ft.join(timeout=5)
    srv.shutdown()
    srv.server_close()

    ids = [m["id"] for m in got]
    assert ids == list(range(1, total + 1)), "gaps or reordering after reconnect"
    assert recv.records == total
    assert srv.cuts_done == cuts
    assert fwd.reconnects >= cuts
    assert recv.duplicates >= cuts * 64 - 64, "resent batches should have been deduped"
    assert len(set(ids)) == len(ids)


def test_ack_only_after_receipt(tmp_path):
    """Frames arriving while the base is down stay pending, then drain in order."""
    spool = Spool(str(tmp_path / "s.db"))
    spool.append_many(Frame(i, i, b"a") for i in range(100))

    # No server listening yet: forwarder must keep everything pending.
    dead_port = _free_port()
    fwd = Forwarder(spool, "127.0.0.1", dead_port, batch=10, backoff_min=0.01, backoff_max=0.02)
    ft = threading.Thread(target=fwd.run, daemon=True)
    ft.start()
    time.sleep(0.2)
    assert spool.pending_count() == 100
    assert fwd.reconnects >= 1

    # Base comes up on that port; backlog drains.
    got = []
    srv = ReceiverServer(("127.0.0.1", dead_port), Receiver(sink=got.append))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    assert wait_until(lambda: spool.pending_count() == 0)
    fwd.stop()
    ft.join(timeout=5)
    srv.shutdown()
    srv.server_close()
    assert [m["seq"] for m in got] == list(range(100))


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
