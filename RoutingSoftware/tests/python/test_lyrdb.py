import xml.etree.ElementTree as ET
import substrate
from substrate.lyrdb import write_lyrdb
from substrate.units import um

def test_lyrdb_contains_violations(tmp_path):
    db = substrate.Design("t")
    with db.transaction("s"):
        l1 = db.add_layer("L1", "conductor")
        db.set_default_spacing(line_line=500)
        a, b = db.add_net("A"), db.add_net("B")
        db.add_cline(layer=l1, net=a, width=100, points=[(0,0),(10_000,0)])
        db.add_cline(layer=l1, net=b, width=100, points=[(0,500),(10_000,500)])
    v = db.drc.run()
    out = tmp_path / "drc.lyrdb"
    write_lyrdb(db, v, out, cell_name="t")
    root = ET.parse(out).getroot()
    assert root.tag == "report-database"
    cats = [c.findtext("name") for c in root.iter("category")]
    assert "spacing" in cats
    items = list(root.iter("item"))
    assert len(items) == 1
    # marker geometry is an edge in micrometers: "edge: (x1,y1;x2,y2)"
    val = items[0].findtext("./values/value")
    assert val.startswith("edge:")
