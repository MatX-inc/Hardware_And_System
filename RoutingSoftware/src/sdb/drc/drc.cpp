#include "sdb/drc/drc.h"
#include "sdb/geom/object_geometry.h"
#include <algorithm>
#include <unordered_set>
namespace sdb {

namespace {

// Canonical pair/sort order: kind then index. Generation is deliberately
// ignored — two live handles never share (kind,index).
bool canon_less(Handle a, Handle b) {
    if (a.kind != b.kind) return a.kind < b.kind;
    return a.index < b.index;
}

// Packed (kind,index) of both handles for pair dedup.
struct PairKey {
    uint64_t a, b;
    bool operator==(const PairKey&) const = default;
};
struct PairKeyHash {
    size_t operator()(const PairKey& k) const {
        return std::hash<uint64_t>{}(k.a * 0x9e3779b97f4a7c15ull ^ k.b);
    }
};
uint64_t pack(Handle h) { return (uint64_t(h.kind) << 32) | h.index; }

// DRC spacing-matrix axis for a prim, derived from the owning object's
// arena kind (Prim does not carry its source kind). Component prims are
// expanded pin pads -> Pad.
ObjKind obj_kind_of(Handle owner) {
    switch (owner.kind) {
        case Kind::Cline:     return ObjKind::Line;
        case Kind::Via:       return ObjKind::Via;
        case Kind::Component: return ObjKind::Pad;
        default:              return ObjKind::Shape;
    }
}

// Reference point used to anchor the violation marker.
Point ref_point(const Prim& p) {
    switch (p.kind) {
        case Prim::Kind::Circle:
        case Prim::Kind::Rect: return p.center;
        case Prim::Kind::Poly: return p.poly.empty() ? p.a : p.poly.front();
        default:               return p.a;   // Capsule / Arc
    }
}
}  // namespace

Drc::Drc(Design& d, SpatialIndex& idx, ConstraintResolver& res)
    : d_(&d), idx_(&idx), res_(&res) {}

std::vector<Violation> Drc::run_full() {
    std::vector<Violation> out;
    for (Handle layer : d_->conductor_layers()) {
        auto v = check_layer(layer, std::nullopt);
        out.insert(out.end(), v.begin(), v.end());
    }
    return out;
}

std::vector<Violation> Drc::run_region(Box window) {
    std::vector<Violation> out;
    for (Handle layer : d_->conductor_layers()) {
        auto v = check_layer(layer, window);
        out.insert(out.end(), v.begin(), v.end());
    }
    return out;
}

std::vector<Violation> Drc::check_layer(Handle layer, std::optional<Box> window) {
    const Coord maxc = res_->max_clearance();

    // Enumerate the objects to seed pair checks from. Window mode asks the
    // R-tree for everything near the (clearance-inflated) window; full mode
    // walks all geometry-bearing kinds and keeps those whose bbox map touches
    // this layer — no magic "huge box" query needed.
    std::vector<Handle> objs;
    if (window) {
        objs = idx_->query_box(layer, window->inflated(maxc));
    } else {
        for (Kind k : {Kind::Cline, Kind::Via, Kind::Shape, Kind::Component})
            d_->for_each_object(k, [&](Handle h) {
                if (object_bboxes(*d_, h).count(layer)) objs.push_back(h);
            });
    }

    std::vector<Violation> out;
    std::unordered_set<PairKey, PairKeyHash> seen;
    for (Handle a : objs) {
        auto bba = object_bboxes(*d_, a);
        auto ita = bba.find(layer);
        if (ita == bba.end()) continue;
        const Box& boxa = ita->second;

        for (Handle b : idx_->query_clearance(layer, boxa, maxc)) {
            if (b == a) continue;
            Handle lo = a, hi = b;
            if (canon_less(hi, lo)) std::swap(lo, hi);
            if (!seen.insert({pack(lo), pack(hi)}).second) continue;

            // Region mode: only report pairs whose combined extent touches
            // the window itself (the inflated query above is just a net to
            // catch partners straddling the edge).
            if (window) {
                auto bbb = object_bboxes(*d_, b);
                auto itb = bbb.find(layer);
                if (itb == bbb.end()) continue;
                if (!boxa.united(itb->second).intersects(*window)) continue;
            }

            auto prims_lo = object_primitives(*d_, lo, layer);
            auto prims_hi = object_primitives(*d_, hi, layer);
            const ObjKind klo = obj_kind_of(lo), khi = obj_kind_of(hi);

            // Scan prim pairs; track the violating pair with the smallest
            // gap. Same-net pairs are skipped only when BOTH nets are valid
            // — null-net prims (unassigned pins) are checked against
            // everything. required is the stricter of the two nets' rules.
            bool found = false;
            double best_gap = 0;
            Coord best_req = 0;
            Point best_loc{};
            for (const Prim& pa : prims_lo) {
                for (const Prim& pb : prims_hi) {
                    if (pa.net.valid() && pb.net.valid() && pa.net == pb.net)
                        continue;                       // same net: v1 skips
                    Coord req = std::max(res_->spacing(pa.net, klo, khi),
                                         res_->spacing(pb.net, klo, khi));
                    if (req <= 0) continue;             // no rule engaged
                    double gap = prim_gap(pa, pb);
                    if (gap >= double(req)) continue;
                    if (!found || gap < best_gap) {
                        found = true;
                        best_gap = gap;
                        best_req = req;
                        Point ra = ref_point(pa), rb = ref_point(pb);
                        best_loc = {(ra.x + rb.x) / 2, (ra.y + rb.y) / 2};
                    }
                }
            }
            // prim_gap is conservative and may dip slightly negative when
            // prims touch/overlap; clamp for reporting only (the raw value
            // picked the minimizing pair).
            if (found)
                out.push_back({lo, hi, layer, std::max(best_gap, 0.0),
                               best_req, best_loc});
        }
    }
    std::sort(out.begin(), out.end(), [](const Violation& x, const Violation& y) {
        if (x.layer != y.layer) return canon_less(x.layer, y.layer);
        if (x.a != y.a) return canon_less(x.a, y.a);
        return canon_less(x.b, y.b);
    });
    return out;
}
}  // namespace sdb
