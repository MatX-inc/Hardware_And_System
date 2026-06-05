"""GDS export and KLayout .lyp layer-properties writer."""
import json
import socket
import subprocess
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

import gdstk

_PALETTE = ["#ff0000", "#00ff00", "#0080ff", "#ffff00",
            "#ff00ff", "#00ffff", "#ff8000", "#8000ff"]


def gds_layer_map(db):
    """Conductor layers in stackup order -> (gds_layer, 0), starting at 1."""
    return {name: (i + 1, 0) for i, name in enumerate(db.conductor_layer_names())}


def export_gds(db, path, tolerance=10.0):
    """Write all conductor geometry of `db` to a GDSII file at `path`.

    The database unit is 1 nm (matching Coord), so raw dbu integer
    coordinates are written exactly (precision == unit means one db unit
    per nm; a finer precision would scale coordinates and overflow GDS
    int32 on package-sized designs). Returns the layer map
    {layer_name: (gds_layer, datatype)}.
    """
    lmap = gds_layer_map(db)
    lib = gdstk.Library(unit=1e-9, precision=1e-9)
    cell = lib.new_cell(db.name)
    for lname, (gl, dt) in lmap.items():
        for poly in db.layer_polygons(lname, tolerance):
            cell.add(gdstk.Polygon(poly, layer=gl, datatype=dt))
    lib.write_gds(str(path))
    return lmap


def write_lyp(db, path, layer_map):
    """Write a KLayout .lyp layer-properties file for `layer_map`
    ({layer_name: (gds_layer, datatype)}), cycling display colors."""
    rows = []
    for i, (name, (gl, dt)) in enumerate(layer_map.items()):
        color = _PALETTE[i % len(_PALETTE)]
        rows.append(f""" <properties>
  <frame-color>{color}</frame-color>
  <fill-color>{color}</fill-color>
  <dither-pattern>I{i % 16}</dither-pattern>
  <visible>true</visible>
  <name>{escape(name)}</name>
  <source>{gl}/{dt}@1</source>
 </properties>""")
    body = "\n".join(rows)
    path.write_text(
        f'<?xml version="1.0"?>\n<layer-properties>\n{body}\n</layer-properties>\n')


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
            try:
                subprocess.Popen(["klayout", str(gds), "-l", str(lyp)],
                                 start_new_session=True)
            except OSError:        # klayout binary missing / not launchable
                return False
            return True
        return False
