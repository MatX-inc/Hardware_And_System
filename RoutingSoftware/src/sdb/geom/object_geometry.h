#pragma once
#include "sdb/core/design.h"
#include <map>
#include <string>
#include <vector>
namespace sdb {

// One flat geometric primitive contributed by a design object on one layer.
// Pads are expanded and component transforms applied before primitives are
// emitted, so consumers (R-tree, connectivity, DRC, GDS export) never need
// to know about padstacks or symbol transforms.
struct Prim {
    enum class Kind { Capsule, Circle, Rect, Poly, Arc } kind;
    // Capsule / Arc: a,b (and center,cw for Arc) + width.
    // Circle: center + diameter.  Rect: center + w/h.
    Point a, b, center;
    bool cw = false;
    Coord width = 0;
    Coord diameter = 0, w = 0, h = 0;
    std::vector<Point> poly;   // Kind::Poly outline (closed, implicit last->first)
    Handle net;                // owning net (kNullHandle for unassigned pins)
    std::string pin;           // non-empty if this prim came from a component pin
};

// Bounding box of one object on every conductor/mask layer it touches.
// Arc boxes are exact (sweep-aware axis extrema, width-inflated).
std::map<Handle, Box> object_bboxes(const Design&, Handle);

// Flat primitive list for one object on one layer (pads expanded, component
// transforms applied). Objects with unresolvable references (stale padstack,
// missing symbol) contribute no primitives. Shape voids are ignored in v1:
// gaps are measured against the outline only.
std::vector<Prim> object_primitives(const Design&, Handle, Handle layer);

// Bounding box of one primitive (width-inflated; arc boxes sweep-exact).
Box prim_bbox(const Prim&);

// Minimum gap between two primitives (width-aware), dispatched through the
// geometry kernel. Conservative simplifications (documented in the .cpp):
//   - Rect is treated as its circumscribed capsule (endpoints span the full
//     longer dimension, width = shorter dimension), so the gap is never
//     overestimated (exact rect support is v2).
//   - Poly edges are zero-width capsules; containment is checked via
//     point_in_poly of the other prim's reference point.
double prim_gap(const Prim&, const Prim&);

}  // namespace sdb
