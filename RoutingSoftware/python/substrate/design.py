from contextlib import contextmanager
from . import _sdb

_LAYER_TYPES = {"conductor": _sdb.LayerType.Conductor,
                "dielectric": _sdb.LayerType.Dielectric,
                "mask": _sdb.LayerType.Mask}

# set_default_spacing keyword -> SpacingMatrix cell.
_K = _sdb.ObjKind
_SPACING_KW = {
    "line_line": (_K.Line, _K.Line), "line_via": (_K.Line, _K.Via),
    "via_via": (_K.Via, _K.Via), "line_pad": (_K.Line, _K.Pad),
    "via_pad": (_K.Via, _K.Pad), "pad_pad": (_K.Pad, _K.Pad),
    "line_shape": (_K.Line, _K.Shape), "via_shape": (_K.Via, _K.Shape),
    "pad_shape": (_K.Pad, _K.Shape), "shape_shape": (_K.Shape, _K.Shape),
}


class _DrcFacade:
    def __init__(self, drc):
        self._drc = drc

    def run(self, region=None):
        if region is None:
            return self._drc.run_full()
        x0, y0, x1, y1 = region
        return self._drc.run_region(_sdb.Box(_sdb.Point(x0, y0), _sdb.Point(x1, y1)))


class _ConnFacade:
    def __init__(self, conn):
        self._conn = conn

    def status(self, net):
        return self._conn.net_status(net)

    def ratsnest(self, net):
        return self._conn.ratsnest(net)

    def clusters(self, net):
        return self._conn.clusters(net)


class ClineView:
    """Read-only convenience view over a raw _sdb.Cline snapshot."""
    def __init__(self, raw):
        self._raw = raw
        self.width = raw.segs[0].width if raw.segs else 0
        pts = [(raw.segs[0].a.x, raw.segs[0].a.y)] if raw.segs else []
        pts += [(s.b.x, s.b.y) for s in raw.segs]
        self.points = pts
        self.layer, self.net = raw.layer, raw.net


class Design:
    """Pythonic facade over the C++ substrate database."""

    def __init__(self, name):
        self._name = name
        self._d = _sdb.Design(name)
        self._txn = None
        # Engine construction order matters: index first (subscribes), then
        # resolver, then connectivity, then drc. C++ keep_alives tie each
        # engine's lifetime to the Design / sibling engines it points at.
        self._idx = _sdb.SpatialIndex(self._d)
        self._res = _sdb.ConstraintResolver(self._d)
        self._conn = _sdb.Connectivity(self._d, self._idx)
        self._drc = _sdb.Drc(self._d, self._idx, self._res)
        self.drc = _DrcFacade(self._drc)
        self.connectivity = _ConnFacade(self._conn)

    # ------------------------------------------------------------ transactions
    @contextmanager
    def transaction(self, name):
        if self._txn is not None:
            raise RuntimeError("nested transactions not supported")
        self._txn = self._d.begin(name)
        try:
            yield self
        except BaseException:
            self._txn.abort()
            self._txn = None
            raise
        else:
            try:
                self._txn.commit()
            finally:
                self._txn = None

    def _t(self):
        if self._txn is None:
            raise RuntimeError("mutation requires an open transaction")
        return self._txn

    # ----------------------------------------------------------------- builders
    def add_layer(self, name, layer_type, thickness_um=0.0):
        lay = _sdb.Layer()
        lay.name = name
        if layer_type not in _LAYER_TYPES:
            raise ValueError(
                f"unknown layer type {layer_type!r}; expected one of {sorted(_LAYER_TYPES)}"
            )
        lay.type = _LAYER_TYPES[layer_type]
        lay.thickness_um = thickness_um
        return self._t().create_layer(lay)

    def add_net(self, name):
        n = _sdb.Net()
        n.name = name
        return self._t().create_net(n)

    def add_cline(self, layer, net, width, points):
        c = _sdb.Cline()
        c.layer = layer
        c.net = net
        segs = []
        for a, b in zip(points, points[1:]):
            segs.append(_sdb.Segment.line(_sdb.Point(*a), _sdb.Point(*b), width))
        c.segs = segs
        return self._t().create_cline(c)

    def add_padstack(self, name, layers, drill=0):
        """Create a padstack. `layers` maps layer name (or handle) to a
        (shape, size) pair where shape is "circle" or "rect" and size is the
        pad diameter / side in Coords (nm)."""
        ps = _sdb.Padstack()
        ps.name = name
        ps.drill = drill
        shapes = {"circle": _sdb.PadShape.Circle, "rect": _sdb.PadShape.Rect}
        pads = {}
        for lname, (shape, size) in layers.items():
            if shape not in shapes:
                raise ValueError(
                    f"unknown pad shape {shape!r}; expected one of {sorted(shapes)}")
            pd = _sdb.PadDef()
            pd.shape = shapes[shape]
            pd.w = pd.h = size
            pads[self._resolve_layer(lname)] = pd
        ps.pads = pads
        return self._t().create_padstack(ps)

    def add_symbol(self, name, pins):
        """Create a symbol from (pin_number, padstack_handle, (x, y)) tuples."""
        s = _sdb.Symbol()
        s.name = name
        s.pins = [_sdb.SymbolPin(num, ps, _sdb.Point(*off))
                  for num, ps, off in pins]
        return self._t().create_symbol(s)

    def place(self, symbol, refdes, at, rotation=0, pin_nets=None):
        """Place a component instance of `symbol` at `at`=(x, y). `pin_nets`
        maps pin number -> net handle."""
        c = _sdb.Component()
        c.refdes = refdes
        c.symbol = symbol
        c.origin = _sdb.Point(*at)
        c.rotation_cw_deg = rotation
        h = self._t().create_component(c)
        for pin, net in (pin_nets or {}).items():
            self._t().assign_pin(h, pin, net)
        return h

    def add_via(self, padstack, net, at, from_layer, to_layer):
        v = _sdb.Via()
        v.padstack = padstack
        v.net = net
        v.pos = _sdb.Point(*at)
        v.from_layer = self._resolve_layer(from_layer)
        v.to_layer = self._resolve_layer(to_layer)
        return self._t().create_via(v)

    # -------------------------------------------------------------- constraints
    def set_default_spacing(self, **kwargs):
        """Set cells of the system-default spacing matrix (creates the system
        CSet if none exists, otherwise updates it). Keywords: line_line,
        line_via, via_via, line_pad, via_pad, pad_pad, line_shape, via_shape,
        pad_shape, shape_shape. Values are Coords (nm); only provided keywords
        are set."""
        t = self._t()
        unknown = set(kwargs) - set(_SPACING_KW)
        if unknown:
            raise ValueError(
                f"unknown spacing keyword(s) {sorted(unknown)}; "
                f"expected one of {sorted(_SPACING_KW)}"
            )
        sys_h = self._d.system_cset()
        cset = self._d.cset(sys_h) if sys_h.valid() else None
        if cset is None:
            cset = _sdb.CSet()
            cset.name = "system_default"
        # `cset.spacing` returns a copy; mutate it and write it back.
        spacing = cset.spacing
        for kw, value in kwargs.items():
            if value is None:
                continue
            a, b = _SPACING_KW[kw]
            spacing.set(a, b, value)
        cset.spacing = spacing
        if sys_h.valid():
            t.update_cset(sys_h, cset)
        else:
            t.set_system_cset(t.create_cset(cset))

    # --------------------------------------------------------------- properties
    @property
    def name(self):
        return self._name

    def set_property(self, h, key, value):
        self._t().set_property(h, key, value)

    def get_property(self, h, key):
        return self._d.get_property(h, key)

    # ------------------------------------------------------------------ queries
    def _resolve_layer(self, layer):
        if isinstance(layer, str):
            h = self._d.layer_by_name(layer)
            if not h.valid():
                raise ValueError(f"no layer named {layer!r}")
            return h
        return layer

    def pin_position(self, comp, pin):
        """Placed position of `pin` of component `comp` as an (x, y) tuple."""
        p = self._d.pin_position(comp, pin)
        return (p.x, p.y)

    def query_box(self, box, layer):
        """Handles of objects whose bbox on `layer` (name or handle)
        intersects box=(x0, y0, x1, y1)."""
        x0, y0, x1, y1 = box
        return self._idx.query_box(
            self._resolve_layer(layer),
            _sdb.Box(_sdb.Point(x0, y0), _sdb.Point(x1, y1)))

    def layer_polygons(self, layer, tolerance=10.0):
        """All conductor geometry on `layer` (name or handle) as polygons:
        a list of [(x, y), ...] point lists (closed, implicit last->first
        edge). Arcs become chains of overlapping capsule polygons; shape
        voids follow their outline as separate reversed-winding polygons."""
        h = self._resolve_layer(layer)
        return [[(p.x, p.y) for p in poly]
                for poly in _sdb.export_polygons(self._d, h, tolerance)]

    def cline(self, h):
        raw = self._d.cline(h)
        return ClineView(raw) if raw is not None else None

    def layer(self, h):
        return self._d.layer(h)

    def symbol(self, h):
        return self._d.symbol(h)

    def component(self, h):
        return self._d.component(h)

    def net(self, h):
        return self._d.net(h)

    def layer_by_name(self, name):
        h = self._d.layer_by_name(name)
        return h if h.valid() else None

    def conductor_layer_names(self):
        """Names of conductor layers in stackup order."""
        return [self._d.layer(h).name for h in self._d.conductor_layers()]

    def net_by_name(self, name):
        h = self._d.net_by_name(name)
        return h if h.valid() else None

    # -------------------------------------------------------------- undo / redo
    def undo(self):
        self._d.undo()

    def redo(self):
        self._d.redo()
