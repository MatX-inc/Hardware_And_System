#include "sdb/geom/export_polygons.h"
#include "sdb/geom/object_geometry.h"
#include "sdb/geom/kernel.h"
#include <algorithm>

namespace sdb {
namespace {

// Append the polygon(s) for one primitive to `out`.
void polygonize_prim(const Prim& p, double tol,
                     std::vector<std::vector<Point>>& out) {
    switch (p.kind) {
        case Prim::Kind::Capsule:
            out.push_back(geom::polygonize_capsule(p.a, p.b, p.width, tol));
            break;
        case Prim::Kind::Circle:
            out.push_back(geom::polygonize_circle(p.center, p.diameter, tol));
            break;
        case Prim::Kind::Rect:
            out.push_back({{p.center.x - p.w / 2, p.center.y - p.h / 2},
                           {p.center.x + p.w / 2, p.center.y - p.h / 2},
                           {p.center.x + p.w / 2, p.center.y + p.h / 2},
                           {p.center.x - p.w / 2, p.center.y + p.h / 2}});
            break;
        case Prim::Kind::Arc: {
            // Sample the centerline, then emit one capsule polygon per
            // polyline edge. The capsules overlap at the joints; that is
            // fine for rendering and GDS, where overlapping polygons on the
            // same layer union visually (no boolean ops in v1).
            auto line = geom::sample_arc(p.a, p.b, p.center, p.cw, tol);
            for (size_t i = 0; i + 1 < line.size(); ++i)
                out.push_back(geom::polygonize_capsule(line[i], line[i + 1],
                                                       p.width, tol));
            break;
        }
        case Prim::Kind::Poly:
            out.push_back(p.poly);   // shape outline passes through unchanged
            break;
    }
}

}  // namespace

std::vector<std::vector<Point>> export_polygons(const Design& d, Handle layer,
                                                double tol) {
    std::vector<std::vector<Point>> out;
    // Deterministic kind order; for_each_object visits each arena in handle
    // order, so the result is stable across runs.
    for (Kind k : {Kind::Cline, Kind::Via, Kind::Shape, Kind::Component}) {
        d.for_each_object(k, [&](Handle h) {
            for (const Prim& p : object_primitives(d, h, layer))
                polygonize_prim(p, tol, out);
            // object_primitives ignores shape voids (DRC sees the outline
            // only); for export, append each void after its outline as a
            // separate reversed-winding polygon. Downstream GDS treats them
            // as independent polygons in v1 — no boolean subtraction.
            if (k == Kind::Shape) {
                const Shape* s = d.shape(h);
                if (s && s->layer == layer && !s->outline.empty())
                    for (const auto& v : s->voids) {
                        std::vector<Point> rev(v.rbegin(), v.rend());
                        out.push_back(std::move(rev));
                    }
            }
        });
    }
    return out;
}

}  // namespace sdb
