import gdstk
import substrate
from substrate.klayout_io import export_gds, write_lyp
from substrate.units import um


def build():
    db = substrate.Design("t")
    with db.transaction("s"):
        db.add_layer("TOP", "conductor")
        db.add_layer("L2", "conductor")
        n = db.add_net("N")
        db.add_cline(layer=db.layer_by_name("TOP"), net=n,
                     width=um(20), points=[(0, 0), (um(100), 0)])
        db.add_cline(layer=db.layer_by_name("L2"), net=n,
                     width=um(20), points=[(0, um(50)), (um(100), um(50))])
    return db


def test_gds_layers_and_polygons(tmp_path):
    db = build()
    out = tmp_path / "t.gds"
    layer_map = export_gds(db, out, tolerance=10.0)
    assert layer_map == {"TOP": (1, 0), "L2": (2, 0)}   # gds layer = stackup order + 1
    lib = gdstk.read_gds(str(out))
    top = lib.top_level()[0]
    assert top.name == "t"
    layers = {p.layer for p in top.polygons}
    assert layers == {1, 2}
    # database unit is 1 nm
    assert abs(lib.unit - 1e-9) < 1e-15
    assert abs(lib.precision - 1e-9) < 1e-15


def test_lyp_lists_all_layers(tmp_path):
    db = build()
    lyp = tmp_path / "t.lyp"
    write_lyp(db, lyp, {"TOP": (1, 0), "L2": (2, 0)})
    text = lyp.read_text()
    assert "<name>TOP</name>" in text and "1/0" in text
    assert text.count("<properties>") == 2
