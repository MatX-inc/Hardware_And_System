import pytest
import substrate
from substrate.units import um, mm

def test_units():
    assert um(1) == 1_000          # 1 um = 1000 nm
    assert mm(1) == 1_000_000

def test_build_and_query():
    db = substrate.Design("demo")
    with db.transaction("setup"):
        l1 = db.add_layer("L1", "conductor", thickness_um=15)
        n1 = db.add_net("VDD")
        c = db.add_cline(layer=l1, net=n1, width=um(20),
                         points=[(0, 0), (mm(1), 0), (mm(1), mm(1))])
    cl = db.cline(c)
    assert cl.width == um(20)
    assert len(cl.points) == 3
    assert db.layer_by_name("L1") == l1
    assert db.net_by_name("VDD") == n1

def test_transaction_rolls_back_on_exception():
    db = substrate.Design("demo")
    with db.transaction("setup"):
        db.add_layer("L1", "conductor")
        n1 = db.add_net("VDD")
    with pytest.raises(RuntimeError):
        with db.transaction("bad"):
            db.add_net("N2")
            raise RuntimeError("boom")
    assert db.net_by_name("N2") is None

def test_undo_redo():
    db = substrate.Design("demo")
    with db.transaction("setup"):
        db.add_net("VDD")
    db.undo()
    assert db.net_by_name("VDD") is None
    db.redo()
    assert db.net_by_name("VDD") is not None

def test_mutation_outside_transaction_raises():
    db = substrate.Design("demo")
    with pytest.raises(RuntimeError):
        db.add_net("VDD")

def test_properties():
    db = substrate.Design("demo")
    with db.transaction("p"):
        n = db.add_net("VDD")
        db.set_property(n, "BUS", "DDR")
    assert db.get_property(n, "BUS") == "DDR"
    assert db.get_property(n, "MISSING") is None

def test_stale_handle_returns_none():
    db = substrate.Design("demo")
    with db.transaction("a"):
        n = db.add_net("VDD")
    db.undo()
    assert db.net(n) is None


def test_property_roundtrip_types():
    """Verify int, float, bool, and Handle properties round-trip through the C++ variant."""
    db = substrate.Design("demo")
    with db.transaction("p"):
        n = db.add_net("VDD")
        db.set_property(n, "I", 42)
        db.set_property(n, "F", 3.14)
        # bool is stored as bool in the C++ variant, but nanobind may map it
        # back to Python as True or 1 (both are acceptable: bool is a subclass
        # of int in Python, and variant<..., bool> ordering can promote to int).
        db.set_property(n, "B", True)
        db.set_property(n, "H", n)
    assert db.get_property(n, "I") == 42
    assert abs(db.get_property(n, "F") - 3.14) < 1e-9
    # Accept either True or 1 — both are valid representations of a C++ bool
    assert db.get_property(n, "B") in (True, 1)
    assert db.get_property(n, "H") == n


def test_transaction_survives_design_del():
    """Transaction must not crash after its parent Design is GC'd (keep_alive)."""
    import gc
    db = substrate.Design("x")
    t = db._d.begin("t")
    del db
    gc.collect()
    # If keep_alive<0,1> is wired correctly this must not segfault.
    t.abort()


def test_point_hashable():
    """Point must be usable as a dict/set key after adding __hash__."""
    p1 = substrate.Point(1, 2)
    p2 = substrate.Point(1, 2)
    p3 = substrate.Point(3, 4)
    assert p1 == p2
    assert hash(p1) == hash(p2)
    assert hash(p1) != hash(p3)  # Not strictly required, but a good sanity check
    s = {p1, p2, p3}
    assert len(s) == 2  # p1 and p2 are equal so only 2 distinct points


def test_box_eq():
    """Box.__eq__ must compare by value using the C++ operator==."""
    lo = substrate.Point(0, 0)
    hi = substrate.Point(100, 200)
    b1 = substrate.Box(lo, hi)
    b2 = substrate.Box(substrate.Point(0, 0), substrate.Point(100, 200))
    b3 = substrate.Box(substrate.Point(1, 0), substrate.Point(100, 200))
    assert b1 == b2
    assert not (b1 == b3)
