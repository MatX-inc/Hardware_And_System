import substrate
from substrate.generators import jedec_name, bga_symbol, die_symbol
from substrate.units import um, mm


def test_jedec_names_skip_IOQSXZ():
    assert jedec_name(0) == "A"
    assert jedec_name(7) == "H"
    assert jedec_name(8) == "J"      # I skipped
    assert jedec_name(13) == "P"     # O skipped
    assert jedec_name(18) == "W"     # Q,S skipped
    assert jedec_name(19) == "Y"     # X skipped
    assert jedec_name(20) == "AA"    # Z skipped -> double letters
    assert jedec_name(29) == "AK"    # row 30 of a 30-row BGA


def test_bga_symbol_counts_and_pitch():
    db = substrate.Design("t")
    with db.transaction("gen"):
        l1 = db.add_layer("BOTTOM", "conductor")
        ps = db.add_padstack("BALL", layers={"BOTTOM": ("circle", um(400))})
        sym = bga_symbol(db, "BGA_30x30", rows=30, cols=30,
                         pitch=mm(0.8), padstack=ps)
    s = db.symbol(sym)
    assert len(s.pins) == 900
    nums = {p.number for p in s.pins}
    assert "A1" in nums and "AK30" in nums          # row 30 with skips = AK
    # pitch check: A1 at (0,0)-centered grid; A2 one pitch in +x
    a1 = next(p for p in s.pins if p.number == "A1")
    a2 = next(p for p in s.pins if p.number == "A2")
    assert a2.offset.x - a1.offset.x == mm(0.8)
    assert a1.offset.y == a2.offset.y


def test_bga_depopulation():
    db = substrate.Design("t")
    with db.transaction("gen"):
        db.add_layer("BOTTOM", "conductor")
        ps = db.add_padstack("BALL", layers={"BOTTOM": ("circle", um(400))})
        sym = bga_symbol(db, "B", rows=4, cols=4, pitch=mm(1), padstack=ps,
                         depopulate=["B2", "B3", "C2", "C3"])   # center 2x2 removed
    assert len(db.symbol(sym).pins) == 12


def test_die_symbol():
    db = substrate.Design("t")
    with db.transaction("gen"):
        db.add_layer("TOP", "conductor")
        ps = db.add_padstack("BUMP", layers={"TOP": ("circle", um(90))})
        sym = die_symbol(db, "DIE_20x20", rows=20, cols=20,
                         pitch=um(150), padstack=ps)
    s = db.symbol(sym)
    assert len(s.pins) == 400
    assert s.pins[0].number == "1"        # die pins numbered 1..N, row-major
    # generator params recorded for regeneration
    assert db.get_property(sym, "GEN") == "die_array"
    assert db.get_property(sym, "GEN_ROWS") == 20


def test_place_component_binds_pins():
    db = substrate.Design("t")
    with db.transaction("gen"):
        db.add_layer("TOP", "conductor")
        ps = db.add_padstack("BUMP", layers={"TOP": ("circle", um(90))})
        sym = die_symbol(db, "D", rows=2, cols=2, pitch=um(150), padstack=ps)
        nets = [db.add_net(f"N{i}") for i in range(4)]
        comp = db.place(sym, refdes="U1", at=(0, 0),
                        pin_nets={"1": nets[0], "2": nets[1], "3": nets[2], "4": nets[3]})
    assert db.component(comp).refdes == "U1"
    assert db.component(comp).pin_nets["3"] == nets[2]
