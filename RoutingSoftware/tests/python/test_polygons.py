import substrate
from substrate.units import um


def test_layer_polygons():
    db = substrate.Design("t")
    with db.transaction("s"):
        l1 = db.add_layer("L1", "conductor")
        n = db.add_net("N")
        db.add_cline(layer=l1, net=n, width=um(20), points=[(0, 0), (um(100), 0)])
    polys = db.layer_polygons("L1", tolerance=10.0)
    assert len(polys) == 1
    assert all(isinstance(pt, tuple) and len(pt) == 2 for pt in polys[0])
