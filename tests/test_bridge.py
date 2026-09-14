"""End-to-end: spool -> forwarder -> TCP -> receiver, with the link cut mid-stream."""
import socket
import threading
import time

from src.base_receiver import Receiver, ReceiverServer
from src.framing import Frame
from src.spool import Spool
from src.telemetry_bridge import Forwarder


class KillableServer(ReceiverServer):
    """Receiver whose live connections can be severed on demand."""

    def __init__(self, addr, receiver):
        super().__init__(addr, receiver)
        self.conns = []
        self.conn_lock = threading.Lock()

    def process_request(self, request, client_address):
        with self.conn_lock:
            self.conns.append(request)
        super().process_request(request, client_address)

    def sever_all(self):
        with self.conn_lock:
            for c in self.conns:
                try:
                    c.shutdown(socket.SHUT_RDWR)
                    c.close()
                except OSError:
                    pass
            self.conns.clear()


def start_server():
    got = []
    recv = Receiver(sink=got.append)
    srv = KillableServer(("127.0.0.1", 0), recv)
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
    srv, recv, got = start_server()
    spool = Spool(str(tmp_path / "s.db"))
    total = 2000
    spool.append_many(Frame(i, i * 100, i.to_bytes(4, "little")) for i in range(total))

    fwd = Forwarder(spool, "127.0.0.1", srv.server_address[1], batch=64,
                    backoff_min=0.01, backoff_max=0.05)
    ft = threading.Thread(target=fwd.run, daemon=True)
    ft.start()

    # Sever the link several times while it is draining.
    cuts = 0
    while recv.records < total * 0.8 and cuts < 6:
        time.sleep(0.03)
        srv.sever_all()
        cuts += 1

    assert wait_until(lambda: spool.pending_count() == 0), fwd.stats()
    fwd.stop()
    ft.join(timeout=5)
    srv.shutdown()
    srv.server_close()

    ids = [m["id"] for m in got]
    assert ids == list(range(1, total + 1)), "gaps or reordering after reconnect"
    assert recv.records == total
    assert fwd.reconnects >= 1, "test did not actually cut the link"
    # duplicates are allowed on the wire but must not reach the sink
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
