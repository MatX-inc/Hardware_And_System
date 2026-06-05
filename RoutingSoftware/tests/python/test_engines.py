import substrate
from substrate.units import um, mm


def make_db():
    db = substrate.Design("t")
    with db.transaction("setup"):
        db.add_layer("L1", "conductor")
        db.set_default_spacing(line_line=um(0.5))     # 500 nm
        db.add_net("A"); db.add_net("B")
    return db


def test_query_box():
    db = make_db()
    with db.transaction("add"):
        a = db.add_cline(layer=db.layer_by_name("L1"), net=db.net_by_name("A"),
                         width=um(0.1), points=[(0, 0), (um(10), 0)])
    hits = db.query_box((0, -um(1), um(10), um(1)), layer="L1")
    assert hits == [a]


def test_drc_finds_violation():
    db = make_db()
    l1 = db.layer_by_name("L1")
    with db.transaction("add"):
        db.add_cline(layer=l1, net=db.net_by_name("A"), width=100, points=[(0, 0), (10_000, 0)])
        db.add_cline(layer=l1, net=db.net_by_name("B"), width=100, points=[(0, 500), (10_000, 500)])
    v = db.drc.run()
    assert len(v) == 1
    assert v[0].measured == 400 and v[0].required == 500


def test_connectivity_stats():
    db = make_db()
    l1 = db.layer_by_name("L1")
    n = db.net_by_name("A")
    with db.transaction("add"):
        db.add_cline(layer=l1, net=n, width=100, points=[(0, 0), (1000, 0)])
        db.add_cline(layer=l1, net=n, width=100, points=[(5000, 0), (6000, 0)])
    st = db.connectivity.status(n)
    assert st.cluster_count == 2 and st.open_pairs == 1
    rats = db.connectivity.ratsnest(n)
    assert len(rats) == 1


def test_clusters_facade():
    db = make_db()
    l1 = db.layer_by_name("L1")
    n = db.net_by_name("A")
    with db.transaction("add"):
        db.add_cline(layer=l1, net=n, width=100, points=[(0, 0), (1000, 0)])
        db.add_cline(layer=l1, net=n, width=100, points=[(5000, 0), (6000, 0)])
    cl = db.connectivity.clusters(n)
    assert isinstance(cl, list)
    assert len(cl) == 2
    # Each element is a (Handle, int) pair
    assert cl[0][0] is not None and cl[1][0] is not None
    assert cl[0][0].valid() and cl[1][0].valid()
    # Two distinct cluster ids
    assert cl[0][1] != cl[1][1]


def test_drc_region_facade():
    db = make_db()
    l1 = db.layer_by_name("L1")
    with db.transaction("add"):
        # Violating pair near origin (within region)
        db.add_cline(layer=l1, net=db.net_by_name("A"), width=100, points=[(0, 0), (10_000, 0)])
        db.add_cline(layer=l1, net=db.net_by_name("B"), width=100, points=[(0, 500), (10_000, 500)])
        # Violating pair far away at y≈mm(10) (outside region)
        db.add_cline(layer=l1, net=db.net_by_name("A"), width=100, points=[(0, mm(10)), (10_000, mm(10))])
        db.add_cline(layer=l1, net=db.net_by_name("B"), width=100, points=[(0, mm(10) + 500), (10_000, mm(10) + 500)])
    v = db.drc.run(region=(-1000, -1000, 20_000, 2_000))
    assert len(v) == 1
