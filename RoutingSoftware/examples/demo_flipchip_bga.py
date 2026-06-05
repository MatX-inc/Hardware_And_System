"""v1 acceptance demo: flip-chip die on BGA substrate.

Run:  python examples/demo_flipchip_bga.py        (live view if KLayout running)

Builds a 20x20-bump die (U1) on a 30x30-ball BGA (B1), sketches two legal
escape traces and two DELIBERATE DRC violations, then runs DRC and
connectivity and writes GDS / .lyp / .lyrdb view files.
"""
from pathlib import Path

import substrate
from substrate.generators import bga_symbol, die_symbol
from substrate.klayout_io import show
from substrate.lyrdb import write_lyrdb
from substrate.units import mm, um


def build_design():
    db = substrate.Design("demo_fcbga")
    with db.transaction("stackup"):
        db.add_layer("TOP", "conductor", thickness_um=15)
        db.add_layer("L2", "conductor", thickness_um=15)
        db.add_layer("BOTTOM", "conductor", thickness_um=15)
        db.set_default_spacing(line_line=um(20), line_via=um(15),
                               via_via=um(25), line_pad=um(15))

    with db.transaction("padstacks_symbols"):
        bump = db.add_padstack("BUMP90", layers={"TOP": ("circle", um(90))})
        ball = db.add_padstack("BALL400", layers={"BOTTOM": ("circle", um(400))})
        viaps = db.add_padstack("V100", drill=um(50), layers={
            "TOP": ("circle", um(100)), "L2": ("circle", um(100)),
            "BOTTOM": ("circle", um(100))})
        die = die_symbol(db, "DIE20", rows=20, cols=20, pitch=um(150), padstack=bump)
        bga = bga_symbol(db, "BGA30", rows=30, cols=30, pitch=um(800), padstack=ball)

    with db.transaction("nets_placement"):
        nets = {name: db.add_net(name) for name in
                ["VDD", "VSS", "DQ0", "DQ1", "DQ2", "DQ3"]}
        # Die pins are numbered row-major "1".."400"; the DQ pins sit in the
        # leftmost column (rows 0..3) so their west escapes clear the array.
        u1 = db.place(die, refdes="U1", at=(0, 0),
                      pin_nets={"1": nets["DQ0"], "21": nets["DQ1"],
                                "41": nets["DQ2"], "61": nets["DQ3"],
                                "210": nets["VDD"], "211": nets["VSS"]})
        b1 = db.place(bga, refdes="B1", at=(0, 0),
                      pin_nets={"A1": nets["DQ0"], "A2": nets["DQ1"],
                                "B1": nets["DQ2"], "B2": nets["DQ3"],
                                "P15": nets["VDD"], "P16": nets["VSS"]})

    with db.transaction("routing_sketch"):
        top_h = db.layer_by_name("TOP")
        # legal escape traces for DQ0/DQ1 from die pins toward the west edge
        p_dq0 = db.pin_position(u1, "1")
        p_dq1 = db.pin_position(u1, "21")
        db.add_cline(layer=top_h, net=nets["DQ0"], width=um(20),
                     points=[p_dq0, (p_dq0[0] - mm(3), p_dq0[1])])
        db.add_cline(layer=top_h, net=nets["DQ1"], width=um(20),
                     points=[p_dq1, (p_dq1[0] - mm(3), p_dq1[1])])
        # DELIBERATE VIOLATION 1: two traces with a 10 um edge gap
        # (centerlines 30 um apart, width 20 um) — under the 20 um rule.
        db.add_cline(layer=top_h, net=nets["DQ2"], width=um(20),
                     points=[(mm(2), mm(2)), (mm(4), mm(2))])
        db.add_cline(layer=top_h, net=nets["DQ3"], width=um(20),
                     points=[(mm(2), mm(2) + um(30)), (mm(4), mm(2) + um(30))])
        # DELIBERATE VIOLATION 2: via pad 5 um from the DQ3 trace edge —
        # under the 15 um line-via rule (and clear of the BGA balls below).
        db.add_via(viaps, nets["VDD"], at=(mm(3.2), mm(2) + um(95)),
                   from_layer="TOP", to_layer="BOTTOM")

    violations = db.drc.run()
    stats = {name: db.connectivity.status(h) for name, h in nets.items()}
    report = {
        "die_pins": len(db.symbol(die).pins),
        "bga_pins": len(db.symbol(bga).pins),
        "drc_violations": len(violations),
        "nets": len(nets),
        "open_pairs": sum(s.open_pairs for s in stats.values()),
    }
    return db, report


def main():
    db, report = build_design()
    out = Path("build/demo_view")
    out.mkdir(parents=True, exist_ok=True)
    pushed = show(db, out_dir=out)
    write_lyrdb(db, db.drc.run(), out / f"{db.name}.lyrdb", cell_name=db.name)
    print(f"die pins:        {report['die_pins']}")
    print(f"bga balls:       {report['bga_pins']}")
    print(f"drc violations:  {report['drc_violations']}")
    print(f"open rats pairs: {report['open_pairs']}")
    print(f"klayout push:    {'ok' if pushed else 'no viewer found'}")
    print(f"view files in:   {out}/  (load .lyrdb via Tools > Marker Browser)")


if __name__ == "__main__":
    main()
