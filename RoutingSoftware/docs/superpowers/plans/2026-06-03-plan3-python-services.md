# Plan 3/3: Python Services (Generators, KLayout Viewing, Demo) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the Python service layer — BGA/die symbol generators, GDS export with `.lyp` layer mapping, KLayout live push, `.lyrdb` DRC marker export — and the v1 acceptance demo.

**Architecture:** Pure Python on top of the `substrate` package from Plans 1–2. The C++ kernel supplies polygonized geometry (`object_primitives` + `polygonize_*`, bound in Task 14); gdstk writes GDS. Spec: `docs/superpowers/specs/2026-06-03-substrate-db-foundation-design.md`.

**Tech Stack:** Python, gdstk, KLayout (external app, socket remote interface).

**Prerequisites:** Plans 1 and 2 complete. KLayout installed for manual live-view verification (automated tests stub the socket).

---

### Task 14: Polygon export support in bindings

**Files:**
- Modify: `bindings/module.cpp`
- Create: `src/sdb/geom/export_polygons.h`, `src/sdb/geom/export_polygons.cpp`
- Test: `tests/cpp/test_export_polygons.cpp`, `tests/python/test_polygons.py`

- [ ] **Step 1: Write failing tests**

`tests/cpp/test_export_polygons.cpp`:
```cpp
#include <catch2/catch_test_macros.hpp>
#include "sdb/geom/export_polygons.h"
#include "sdb/core/design.h"
using namespace sdb;

TEST_CASE("design polygons per layer cover all conductor objects") {
    Design d("t");
    auto t = d.begin("s");
    Handle l1 = t.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle n = t.create_net({.name="N"});
    Cline c{.layer=l1, .net=n};
    c.segs = {Segment::line({0,0},{10'000,0}, 2'000)};
    t.create_cline(c);
    Shape s{.layer=l1, .net=n};
    s.outline = {{20'000,0},{30'000,0},{30'000,10'000},{20'000,10'000}};
    t.create_shape(s);
    t.commit();
    auto polys = export_polygons(d, l1, /*tol=*/10.0);
    REQUIRE(polys.size() == 2);
    // capsule poly bbox
    REQUIRE(geom::poly_bbox(polys[0]) == Box{{-1'000,-1'000},{11'000,1'000}});
    // shape passes through unchanged
    REQUIRE(polys[1] == s.outline);
}
```

`tests/python/test_polygons.py`:
```python
import substrate
from substrate.units import um

def test_layer_polygons():
    db = substrate.Design("t")
    with db.transaction("s"):
        l1 = db.add_layer("L1", "conductor")
        n = db.add_net("N")
        db.add_cline(layer=l1, net=n, width=um(20), points=[(0,0),(um(100),0)])
    polys = db.layer_polygons("L1", tolerance=10.0)
    assert len(polys) == 1
    assert all(isinstance(pt, tuple) and len(pt) == 2 for pt in polys[0])
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement**

`src/sdb/geom/export_polygons.h`:
```cpp
#pragma once
#include "sdb/core/design.h"
namespace sdb {
// All conductor geometry on `layer` as polygons (outlines only; shape voids
// returned as separate reversed-winding polygons appended after their outline).
std::vector<std::vector<Point>> export_polygons(const Design&, Handle layer, double tol);
}
```
Implementation: iterate clines/vias/shapes/components on the layer (`for_each_object` + `object_primitives`), polygonize each prim (`polygonize_capsule`, `polygonize_circle`, arc prims via `sample_arc` then capsule outline around the polyline, rect prims as 4-corner polygons), pass shape outlines/voids through unchanged.

Binding + facade: bind `export_polygons`; in `python/substrate/design.py` add
```python
def layer_polygons(self, layer, tolerance=10.0):
    h = layer if not isinstance(layer, str) else self._d.layer_by_name(layer)
    return [[(p.x, p.y) for p in poly]
            for poly in _sdb.export_polygons(self._d, h, tolerance)]
```

- [ ] **Step 4: Run both suites, verify PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat: polygon export path for GDS/render"`

---

### Task 15: BGA and die symbol generators

**Files:**
- Create: `python/substrate/generators.py`
- Modify: `python/substrate/__init__.py` (export `generators`)
- Test: `tests/python/test_generators.py`

- [ ] **Step 1: Write failing tests**

`tests/python/test_generators.py`:
```python
import substrate
from substrate.generators import jedec_name, bga_symbol, die_symbol
from substrate.units import um, mm

def test_jedec_names_skip_IOQSXZ():
    assert jedec_name(0) == "A"
    assert jedec_name(7) == "H"
    assert jedec_name(8) == "J"      # I skipped
    assert jedec_name(13) == "P"     # O skipped
    assert jedec_name(19) == "W"     # Q,S skipped
    assert jedec_name(20) == "Y"     # X skipped
    assert jedec_name(21) == "AA"    # Z skipped -> double letters

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
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement**

`python/substrate/generators.py`:
```python
from .units import um

_ROW_LETTERS = [c for c in "ABCDEFGHJKLMNPRTUVWY"]   # JEDEC: skip I,O,Q,S,X,Z

def jedec_name(row_index):
    n = len(_ROW_LETTERS)
    if row_index < n:
        return _ROW_LETTERS[row_index]
    hi, lo = divmod(row_index - n, n)
    return _ROW_LETTERS[hi] + _ROW_LETTERS[lo]

def _grid(rows, cols, pitch):
    # centered grid, row 0 (A) at +y top, col 1 at -x left; y grows downward row by row
    x0 = -(cols - 1) * pitch // 2
    y0 = (rows - 1) * pitch // 2
    for r in range(rows):
        for c in range(cols):
            yield r, c, (x0 + c * pitch, y0 - r * pitch)

def bga_symbol(db, name, rows, cols, pitch, padstack, depopulate=()):
    dep = set(depopulate)
    pins = []
    for r, c, (x, y) in _grid(rows, cols, pitch):
        num = f"{jedec_name(r)}{c + 1}"
        if num in dep:
            continue
        pins.append((num, padstack, (x, y)))
    sym = db.add_symbol(name, pins)
    db.set_property(sym, "GEN", "bga_array")
    for k, v in (("GEN_ROWS", rows), ("GEN_COLS", cols), ("GEN_PITCH", pitch)):
        db.set_property(sym, k, v)
    return sym

def die_symbol(db, name, rows, cols, pitch, padstack):
    pins = [(str(r * cols + c + 1), padstack, (x, y))
            for r, c, (x, y) in _grid(rows, cols, pitch)]
    sym = db.add_symbol(name, pins)
    db.set_property(sym, "GEN", "die_array")
    for k, v in (("GEN_ROWS", rows), ("GEN_COLS", cols), ("GEN_PITCH", pitch)):
        db.set_property(sym, k, v)
    return sym
```

Facade additions to `python/substrate/design.py` (inside open transaction, via `self._t()`):
```python
def add_padstack(self, name, layers, drill=0):
    ps = _sdb.Padstack(); ps.name = name; ps.drill = drill
    pads = {}
    for lname, (shape, size) in layers.items():
        h = self._d.layer_by_name(lname)
        pd = _sdb.PadDef()
        pd.shape = {"circle": _sdb.PadShape.Circle, "rect": _sdb.PadShape.Rect}[shape]
        pd.w = pd.h = size
        pads[h] = pd
    ps.pads = pads
    return self._t().create_padstack(ps)

def add_symbol(self, name, pins):
    s = _sdb.Symbol(); s.name = name
    s.pins = [_sdb.SymbolPin(num, ps, _sdb.Point(*off)) for num, ps, off in pins]
    return self._t().create_symbol(s)

def place(self, symbol, refdes, at, rotation=0, pin_nets=None):
    c = _sdb.Component(); c.refdes = refdes; c.symbol = symbol
    c.origin = _sdb.Point(*at); c.rotation_cw_deg = rotation
    h = self._t().create_component(c)
    for pin, net in (pin_nets or {}).items():
        self._t().assign_pin(h, pin, net)
    return h

def symbol(self, h):     return self._d.symbol(h)
def component(self, h):  return self._d.component(h)
def add_via(self, padstack, net, at, from_layer, to_layer):
    v = _sdb.Via(); v.padstack = padstack; v.net = net
    v.pos = _sdb.Point(*at)
    v.from_layer = self._resolve_layer(from_layer)
    v.to_layer = self._resolve_layer(to_layer)
    return self._t().create_via(v)

def _resolve_layer(self, l):
    return self._d.layer_by_name(l) if isinstance(l, str) else l
```
(`set_property` int values: `GEN_ROWS` etc. arrive as Python ints → `int64_t` variant alternative; `get_property` returns them back as ints.)

- [ ] **Step 4: Run tests, verify PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat: BGA/die symbol generators with JEDEC naming and depopulation"`

---

### Task 16: GDS export and `.lyp` writer

**Files:**
- Create: `python/substrate/klayout_io.py`
- Test: `tests/python/test_gds_export.py`

- [ ] **Step 1: Write failing tests**

`tests/python/test_gds_export.py`:
```python
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
                     width=um(20), points=[(0,0),(um(100),0)])
        db.add_cline(layer=db.layer_by_name("L2"), net=n,
                     width=um(20), points=[(0,um(50)),(um(100),um(50))])
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

def test_lyp_lists_all_layers(tmp_path):
    db = build()
    lyp = tmp_path / "t.lyp"
    write_lyp(db, lyp, {"TOP": (1, 0), "L2": (2, 0)})
    text = lyp.read_text()
    assert "<name>TOP</name>" in text and "1/0" in text
    assert text.count("<properties>") == 2
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement**

`python/substrate/klayout_io.py` (export portion):
```python
import gdstk

_PALETTE = ["#ff0000", "#00ff00", "#0080ff", "#ffff00",
            "#ff00ff", "#00ffff", "#ff8000", "#8000ff"]

def gds_layer_map(db):
    """Conductor layers in stackup order -> (gds_layer, 0), starting at 1."""
    return {name: (i + 1, 0) for i, name in enumerate(db.conductor_layer_names())}

def export_gds(db, path, tolerance=10.0):
    lmap = gds_layer_map(db)
    lib = gdstk.Library(unit=1e-9, precision=1e-12)
    cell = lib.new_cell(db.name)
    for lname, (gl, dt) in lmap.items():
        for poly in db.layer_polygons(lname, tolerance):
            cell.add(gdstk.Polygon(poly, layer=gl, datatype=dt))
    lib.write_gds(str(path))
    return lmap

def write_lyp(db, path, layer_map):
    rows = []
    for i, (name, (gl, dt)) in enumerate(layer_map.items()):
        color = _PALETTE[i % len(_PALETTE)]
        rows.append(f"""\
 <properties>
  <frame-color>{color}</frame-color>
  <fill-color>{color}</fill-color>
  <dither-pattern>I{i % 16}</dither-pattern>
  <visible>true</visible>
  <name>{name}</name>
  <source>{gl}/{dt}@1</source>
 </properties>""")
    body = "\n".join(rows)
    path.write_text(f"<?xml version=\"1.0\"?>\n<layer-properties>\n{body}\n</layer-properties>\n")
```

Facade additions: `db.name` property and `db.conductor_layer_names()` (binds `Design::conductor_layers()` + name lookup; ordered by stackup).

Note on coordinates: gdstk takes float coordinate pairs interpreted in `unit`; passing raw dbu ints with `unit=1e-9` keeps values exact (ints up to 2^53 are exact doubles).

- [ ] **Step 4: Run tests, verify PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat: GDS export and .lyp layer-properties writer"`

---

### Task 17: KLayout live push

**Files:**
- Modify: `python/substrate/klayout_io.py`
- Test: `tests/python/test_klive.py`

Protocol (klive, as used by gdsfactory): KLayout runs the klive server salt package listening on TCP `localhost:8082`; a client sends one JSON line `{"gds": "<abs path>", "keep_position": true, "technology": ""}\n` and KLayout loads/reloads the file preserving the view. If the port is closed, fall back to launching `klayout <gds> -l <lyp>` detached.

- [ ] **Step 1: Write failing tests**

`tests/python/test_klive.py`:
```python
import json
import socket
import threading
import substrate
from substrate.klayout_io import show

def _fake_klive(received, port):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port)); srv.listen(1)
    def run():
        conn, _ = srv.accept()
        received.append(conn.makefile().readline())
        conn.close(); srv.close()
    t = threading.Thread(target=run, daemon=True); t.start()
    return t

def test_show_pushes_json(tmp_path):
    db = substrate.Design("t")
    with db.transaction("s"):
        n = db.add_net("N")
        db.add_layer("TOP", "conductor")
        db.add_cline(layer=db.layer_by_name("TOP"), net=n,
                     width=100, points=[(0,0),(1000,0)])
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
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement** (append to `klayout_io.py`)

```python
import json, socket, subprocess, tempfile
from pathlib import Path

def show(db, out_dir=None, port=8082, fallback_launch=True, tolerance=10.0):
    """Export GDS+lyp and hot-reload in a running KLayout (klive). Returns True on push."""
    out = Path(out_dir) if out_dir else Path(tempfile.gettempdir()) / "substrate_view"
    out.mkdir(parents=True, exist_ok=True)
    gds = out / f"{db.name}.gds"
    lyp = out / f"{db.name}.lyp"
    lmap = export_gds(db, gds, tolerance)
    write_lyp(db, lyp, lmap)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as s:
            s.sendall((json.dumps({"gds": str(gds.resolve()),
                                   "keep_position": True,
                                   "technology": ""}) + "\n").encode())
        return True
    except OSError:
        if fallback_launch:
            subprocess.Popen(["klayout", str(gds), "-l", str(lyp)],
                             start_new_session=True)
            return True
        return False
```

- [ ] **Step 4: Run tests, verify PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat: klive live push with launch fallback"`

---

### Task 18: `.lyrdb` DRC marker export

**Files:**
- Create: `python/substrate/lyrdb.py`
- Test: `tests/python/test_lyrdb.py`

- [ ] **Step 1: Write failing tests**

`tests/python/test_lyrdb.py`:
```python
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
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement**

`python/substrate/lyrdb.py`:
```python
from xml.sax.saxutils import escape

def write_lyrdb(db, violations, path, cell_name):
    """KLayout report database XML. Marker = edge between the two closest points,
    coordinates in micrometers (KLayout RDB convention)."""
    items = []
    for v in violations:
        x, y = v.location.x / 1000.0, v.location.y / 1000.0
        d = v.required / 1000.0
        items.append(f"""\
  <item>
   <category>'spacing'</category>
   <cell>{escape(cell_name)}</cell>
   <visited>false</visited>
   <multiplicity>1</multiplicity>
   <values>
    <value>edge: ({x:.3f},{y:.3f};{x + d:.3f},{y:.3f})</value>
    <value>text: 'gap {v.measured/1000:.3f} um &lt; required {v.required/1000:.3f} um'</value>
   </values>
  </item>""")
    body = "\n".join(items)
    path.write_text(f"""<?xml version="1.0"?>
<report-database>
 <description>substrate DRC</description>
 <categories>
  <category><name>spacing</name><description>spacing violations</description></category>
 </categories>
 <cells>
  <cell><name>{escape(cell_name)}</name></cell>
 </cells>
 <items>
{body}
 </items>
</report-database>
""")
```

- [ ] **Step 4: Run tests, verify PASS; manually open one `.lyrdb` in KLayout (Tools → Marker Browser) once during this task and confirm markers list and zoom — record the result in the commit message**

- [ ] **Step 5: Commit** — `git commit -m "feat: .lyrdb DRC report export for KLayout marker browser"`

---

### Task 19: Acceptance demo and integration test

**Files:**
- Create: `examples/demo_flipchip_bga.py`
- Test: `tests/python/test_demo_integration.py`

- [ ] **Step 1: Write the integration test (drives the same builder as the demo)**

`tests/python/test_demo_integration.py`:
```python
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
    lmap = export_gds(db, tmp_path / "demo.gds")
    lib = gdstk.read_gds(str(tmp_path / "demo.gds"))
    assert len(lib.top_level()[0].polygons) > 1300   # bumps + balls + traces
```

Make `examples/` importable: add empty `examples/__init__.py` and `[tool.pytest.ini_options] pythonpath = ["."]` to `pyproject.toml`.

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Write the demo**

`examples/demo_flipchip_bga.py`:
```python
"""v1 acceptance demo: flip-chip die on BGA substrate.

Run:  python examples/demo_flipchip_bga.py        (live view if KLayout running)
"""
import substrate
from substrate.generators import bga_symbol, die_symbol
from substrate.units import um, mm
from substrate.klayout_io import show
from substrate.lyrdb import write_lyrdb
from pathlib import Path


def build_design():
    db = substrate.Design("demo_fcbga")
    with db.transaction("stackup"):
        top = db.add_layer("TOP", "conductor", thickness_um=15)
        l2 = db.add_layer("L2", "conductor", thickness_um=15)
        bot = db.add_layer("BOTTOM", "conductor", thickness_um=15)
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
        u1 = db.place(die, refdes="U1", at=(0, 0),
                      pin_nets={"1": nets["DQ0"], "2": nets["DQ1"],
                                "21": nets["DQ2"], "22": nets["DQ3"],
                                "210": nets["VDD"], "211": nets["VSS"]})
        b1 = db.place(bga, refdes="B1", at=(0, 0),
                      pin_nets={"A1": nets["DQ0"], "A2": nets["DQ1"],
                                "B1": nets["DQ2"], "B2": nets["DQ3"],
                                "P15": nets["VDD"], "P16": nets["VSS"]})

    with db.transaction("routing_sketch"):
        top_h = db.layer_by_name("TOP")
        # legal escape traces for DQ0/DQ1 from die pins toward the west edge
        p_dq0 = db.pin_position(u1, "1")
        p_dq1 = db.pin_position(u1, "2")
        db.add_cline(layer=top_h, net=nets["DQ0"], width=um(20),
                     points=[p_dq0, (p_dq0[0] - mm(3), p_dq0[1])])
        db.add_cline(layer=top_h, net=nets["DQ1"], width=um(20),
                     points=[p_dq1, (p_dq1[0] - mm(3), p_dq1[1] + um(60))])
        # DELIBERATE VIOLATION 1: two traces 10 um apart (< 20 um rule)
        db.add_cline(layer=top_h, net=nets["DQ2"], width=um(20),
                     points=[(mm(2), mm(2)), (mm(4), mm(2))])
        db.add_cline(layer=top_h, net=nets["DQ3"], width=um(20),
                     points=[(mm(2), mm(2) + um(30)), (mm(4), mm(2) + um(30))])
        # DELIBERATE VIOLATION 2: via too close to a foreign trace
        db.add_via(viaps, nets["VDD"], at=(mm(3), mm(2) + um(80)),
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
    out = Path("build/demo_view"); out.mkdir(parents=True, exist_ok=True)
    pushed = show(db, out_dir=out)
    write_lyrdb(db, db.drc.run(), out / "demo_fcbga.lyrdb", cell_name=db.name)
    print(f"die pins:        {report['die_pins']}")
    print(f"bga balls:       {report['bga_pins']}")
    print(f"drc violations:  {report['drc_violations']}")
    print(f"open rats pairs: {report['open_pairs']}")
    print(f"klayout push:    {'ok' if pushed else 'no viewer found'}")
    print(f"view files in:   {out}/  (load .lyrdb via Tools > Marker Browser)")


if __name__ == "__main__":
    main()
```

Note: `db.pin_position(comp, pin)` facade returns a tuple — add to `design.py`:
```python
def pin_position(self, comp, pin):
    p = self._d.pin_position(comp, pin)
    return (p.x, p.y)
```
The exact pin numbers ("210", "P15", etc.) and expected violation count in the integration test must be adjusted to the real generated geometry during implementation — the *structure* (≥2 deliberate violations, ≥1 open ratsnest pair, all pins placed) is the contract.

- [ ] **Step 4: Run integration test, verify PASS; run the demo script manually with KLayout open and confirm: design appears, layers colored per `.lyp`, marker browser shows the deliberate violations**

Run: `python -m pytest tests/python/test_demo_integration.py -v && python examples/demo_flipchip_bga.py`

- [ ] **Step 5: Commit** — `git commit -m "feat: flip-chip-on-BGA acceptance demo with deliberate DRC violations"`

---

### Task 20: README and final tidy

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README** — short: what the tool is (one paragraph, link to spec), install (`pip install -e .[dev]`), run tests (both suites), run the demo, KLayout live-view setup (klive salt package or fallback). No marketing prose.

- [ ] **Step 2: Full suite run**

Run: `ctest --test-dir build --output-on-failure && python -m pytest tests/python -v`
Expected: everything green.

- [ ] **Step 3: Commit** — `git commit -m "docs: README with install, test, and demo instructions"`

---

## Plan-level acceptance (== v1 acceptance, from the spec)

`python examples/demo_flipchip_bga.py` with KLayout open: design renders with colored layers, deliberate violations appear in the marker browser, ratsnest/connectivity stats print, all automated tests green.
