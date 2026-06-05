"""KLayout report database (.lyrdb) export for DRC violations."""
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
