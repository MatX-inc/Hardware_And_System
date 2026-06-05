#include "sdb/geom/object_geometry.h"
#include "sdb/geom/kernel.h"
#include <algorithm>
#include <limits>

namespace sdb {
namespace {

// PadDef -> Prim centered at `pos`. RoundedRect is treated as a plain Rect
// in v1 (conservative for bbox; prim_gap already approximates rects).
Prim pad_prim(const PadDef& pad, Point pos, Handle net, std::string pin) {
    Prim p;
    p.center = pos;
    p.a = p.b = pos;             // reference point for containment checks
    p.net = net;
    p.pin = std::move(pin);
    if (pad.shape == PadShape::Circle) {
        p.kind = Prim::Kind::Circle;
        p.diameter = pad.w;
    } else {                     // Rect / RoundedRect
        p.kind = Prim::Kind::Rect;
        p.w = pad.w;
        p.h = pad.h;
    }
    return p;
}

}  // namespace

Box prim_bbox(const Prim& p) {
    switch (p.kind) {
        case Prim::Kind::Capsule:
            return Box::of(p.a, p.b).inflated(p.width / 2);
        case Prim::Kind::Arc:
            return geom::arc_bbox(p.a, p.b, p.center, p.cw)
                .inflated(p.width / 2);
        case Prim::Kind::Circle:
            return Box::of(p.center, p.center).inflated(p.diameter / 2);
        case Prim::Kind::Rect:
            return {{p.center.x - p.w / 2, p.center.y - p.h / 2},
                    {p.center.x + p.w / 2, p.center.y + p.h / 2}};
        case Prim::Kind::Poly:
            return geom::poly_bbox(p.poly);
    }
    return Box::empty();
}

namespace {

// Emit every primitive of `h`, paired with the layer it lives on.
// `layer_filter`: if valid, only primitives on that layer are emitted.
void collect_prims(const Design& d, Handle h, Handle layer_filter,
                   const std::function<void(Handle, Prim&&)>& emit) {
    auto wants = [&](Handle layer) {
        return !layer_filter.valid() || layer == layer_filter;
    };
    switch (h.kind) {
        case Kind::Cline: {
            const Cline* c = d.cline(h);
            if (!c || !wants(c->layer)) return;
            for (const Segment& s : c->segs) {
                Prim p;
                p.kind = s.is_arc ? Prim::Kind::Arc : Prim::Kind::Capsule;
                p.a = s.a; p.b = s.b;
                p.center = s.center; p.cw = s.cw;
                p.width = s.width;
                p.net = c->net;
                emit(c->layer, std::move(p));
            }
            break;
        }
        case Kind::Via: {
            const Via* v = d.via(h);
            if (!v) return;
            const Padstack* ps = d.padstack(v->padstack);
            if (!ps) return;     // unresolvable padstack: no primitives
            for (const auto& [layer, pad] : ps->pads)
                if (wants(layer))
                    emit(layer, pad_prim(pad, v->pos, v->net, {}));
            break;
        }
        case Kind::Shape: {
            const Shape* s = d.shape(h);
            if (!s || !wants(s->layer)) return;
            if (s->outline.empty()) return;   // degenerate shape: no primitives
            // Voids are ignored for primitive purposes in v1: gap queries see
            // the outline only (conservative — a void can only increase gaps).
            Prim p;
            p.kind = Prim::Kind::Poly;
            p.poly = s->outline;
            p.a = p.b = p.center = p.poly.front();
            p.net = s->net;
            emit(s->layer, std::move(p));
            break;
        }
        case Kind::Component: {
            const Component* c = d.component(h);
            if (!c) return;
            const Symbol* sym = d.symbol(c->symbol);
            if (!sym) return;
            for (const SymbolPin& pin : sym->pins) {
                const Padstack* ps = d.padstack(pin.padstack);
                if (!ps) continue;
                Point pos = d.pin_position(h, pin.number);  // mirror/rotate/translate
                Handle net = kNullHandle;
                if (auto it = c->pin_nets.find(pin.number); it != c->pin_nets.end())
                    net = it->second;
                for (const auto& [layer, pad] : ps->pads)
                    if (wants(layer))
                        emit(layer, pad_prim(pad, pos, net, pin.number));
            }
            break;
        }
        default: break;          // non-geometric kinds contribute nothing
    }
}

}  // namespace

std::map<Handle, Box> object_bboxes(const Design& d, Handle h) {
    std::map<Handle, Box> out;
    collect_prims(d, h, kNullHandle, [&](Handle layer, Prim&& p) {
        Box b = prim_bbox(p);
        if (b.is_empty()) return;
        auto [it, inserted] = out.try_emplace(layer, b);
        if (!inserted) it->second = it->second.united(b);
    });
    return out;
}

std::vector<Prim> object_primitives(const Design& d, Handle h, Handle layer) {
    std::vector<Prim> out;
    collect_prims(d, h, layer, [&](Handle, Prim&& p) {
        out.push_back(std::move(p));
    });
    return out;
}

// ------------------------------------------------------------------ prim_gap

namespace {

// Normalized form: everything that is not an Arc or Poly becomes a capsule.
//   Circle -> zero-length capsule at center with width = diameter.
//   Rect   -> circumscribed capsule containing the rect: endpoints span the
//             FULL longer dimension and width equals the shorter dimension,
//             so every rect corner lies on the capsule boundary. Conservative
//             for DRC (gap underestimated near the rounded ends, never
//             overestimated); exact rect support is v2.
struct Cap { Point a, b; Coord w; };

Cap as_capsule(const Prim& p) {
    switch (p.kind) {
        case Prim::Kind::Capsule:
            return {p.a, p.b, p.width};
        case Prim::Kind::Circle:
            return {p.center, p.center, p.diameter};
        case Prim::Kind::Rect: {
            if (p.w >= p.h) {
                Coord half = p.w / 2;
                return {{p.center.x - half, p.center.y},
                        {p.center.x + half, p.center.y}, p.h};
            }
            Coord half = p.h / 2;
            return {{p.center.x, p.center.y - half},
                    {p.center.x, p.center.y + half}, p.w};
        }
        default:
            return {p.a, p.b, 0};   // unreachable for Arc/Poly callers
    }
}

// Closed-outline edges of a poly prim, including the implicit last->first edge.
template <typename F>
void for_each_edge(const std::vector<Point>& poly, F&& f) {
    size_t n = poly.size();
    for (size_t i = 0; i < n; ++i) f(poly[i], poly[(i + 1) % n]);
}

double gap_poly_capsule(const Prim& poly, const Cap& c) {
    // Containment: if either capsule endpoint is inside the poly, they touch.
    if (geom::point_in_poly(c.a, poly.poly) ||
        geom::point_in_poly(c.b, poly.poly)) return 0.0;
    double g = std::numeric_limits<double>::infinity();
    for_each_edge(poly.poly, [&](Point ea, Point eb) {
        g = std::min(g, geom::gap_capsule_capsule(ea, eb, 0, c.a, c.b, c.w));
    });
    return g;
}

double gap_poly_arc(const Prim& poly, const Prim& arc) {
    // Containment: arc endpoints inside the poly => touching.
    if (geom::point_in_poly(arc.a, poly.poly) ||
        geom::point_in_poly(arc.b, poly.poly)) return 0.0;
    double g = std::numeric_limits<double>::infinity();
    for_each_edge(poly.poly, [&](Point ea, Point eb) {
        g = std::min(g, geom::gap_segment_arc(ea, eb, 0,
                                              arc.a, arc.b, arc.center, arc.cw,
                                              arc.width));
    });
    return g;
}

double gap_poly_poly(const Prim& p1, const Prim& p2) {
    // Containment both ways via first points. v1 simplification: this misses
    // pure edge-crossing overlap with no contained vertex, but edge×edge gaps
    // below return 0 in that case anyway.
    if (!p1.poly.empty() && !p2.poly.empty()) {
        if (geom::point_in_poly(p2.poly.front(), p1.poly) ||
            geom::point_in_poly(p1.poly.front(), p2.poly)) return 0.0;
    }
    double g = std::numeric_limits<double>::infinity();
    for_each_edge(p1.poly, [&](Point a1, Point b1) {
        for_each_edge(p2.poly, [&](Point a2, Point b2) {
            g = std::min(g, geom::gap_capsule_capsule(a1, b1, 0, a2, b2, 0));
        });
    });
    return g;
}

}  // namespace

double prim_gap(const Prim& x, const Prim& y) {
    // Poly handles everything (capsule-likes are normalized first).
    if (x.kind == Prim::Kind::Poly) {
        if (y.kind == Prim::Kind::Poly) return gap_poly_poly(x, y);
        if (y.kind == Prim::Kind::Arc)  return gap_poly_arc(x, y);
        return gap_poly_capsule(x, as_capsule(y));
    }
    if (y.kind == Prim::Kind::Poly) return prim_gap(y, x);

    if (x.kind == Prim::Kind::Arc) {
        if (y.kind == Prim::Kind::Arc)
            return geom::gap_arc_arc(x.a, x.b, x.center, x.cw, x.width,
                                     y.a, y.b, y.center, y.cw, y.width);
        Cap c = as_capsule(y);   // capsule/circle/rect (circle = degenerate seg)
        return geom::gap_segment_arc(c.a, c.b, c.w,
                                     x.a, x.b, x.center, x.cw, x.width);
    }
    if (y.kind == Prim::Kind::Arc) return prim_gap(y, x);

    // Both capsule-like.
    Cap c1 = as_capsule(x), c2 = as_capsule(y);
    return geom::gap_capsule_capsule(c1.a, c1.b, c1.w, c2.a, c2.b, c2.w);
}

}  // namespace sdb
