import gdstk

from examples.demo_flipchip_bga import build_design


def test_demo_builds_and_checks(tmp_path):
    db, report = build_design()
    # 400 die bumps + 900 balls placed
    assert report["die_pins"] == 400
    assert report["bga_pins"] == 900
    # deliberate violations present
    assert report["drc_violations"] >= 2
    # connectivity stats present for the demo nets
    assert report["nets"] >= 4
    assert report["open_pairs"] >= 1
    # export runs
    from substrate.klayout_io import export_gds
    export_gds(db, tmp_path / "demo.gds")
    lib = gdstk.read_gds(str(tmp_path / "demo.gds"))
    assert len(lib.top_level()[0].polygons) > 1300   # bumps + balls + traces
