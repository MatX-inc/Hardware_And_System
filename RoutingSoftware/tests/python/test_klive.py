import json
import socket
import threading

import substrate
from substrate.klayout_io import show


def _fake_klive(received, port):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        received.append(conn.makefile().readline())
        conn.close()
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_show_pushes_json(tmp_path):
    db = substrate.Design("t")
    with db.transaction("s"):
        n = db.add_net("N")
        db.add_layer("TOP", "conductor")
        db.add_cline(layer=db.layer_by_name("TOP"), net=n,
                     width=100, points=[(0, 0), (1000, 0)])
    received = []
    t = _fake_klive(received, 18082)
    show(db, out_dir=tmp_path, port=18082, fallback_launch=False)
    t.join(timeout=5)
    msg = json.loads(received[0])
    assert msg["gds"].endswith("t.gds")
    assert msg["keep_position"] is True
    assert (tmp_path / "t.gds").exists()
    assert (tmp_path / "t.lyp").exists()


def test_show_no_server_no_fallback_returns_false(tmp_path):
    db = substrate.Design("t")
    with db.transaction("s"):
        db.add_layer("TOP", "conductor")
    assert show(db, out_dir=tmp_path, port=18099, fallback_launch=False) is False


def test_show_fallback_binary_missing_returns_false(tmp_path, monkeypatch):
    import substrate.klayout_io as kio

    def boom(*args, **kwargs):
        raise FileNotFoundError("klayout")

    monkeypatch.setattr(kio.subprocess, "Popen", boom)
    db = substrate.Design("t")
    with db.transaction("s"):
        db.add_layer("TOP", "conductor")
    assert show(db, out_dir=tmp_path, port=18099, fallback_launch=True) is False
