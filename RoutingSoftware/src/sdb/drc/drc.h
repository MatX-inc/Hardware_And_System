#pragma once
#include "sdb/index/spatial_index.h"
#include "sdb/constraints/constraints.h"
#include <optional>
#include <vector>
namespace sdb {

// One spacing violation between two objects on one layer.
struct Violation {
    Handle a, b;            // canonical: (a.kind,a.index) < (b.kind,b.index)
    Handle layer;
    double measured = 0;    // dbu; min gap over contributing prim pairs (>= 0)
    Coord required = 0;
    Point location;         // midpoint between the closest prims (marker anchor)
};

// Spacing DRC over the current design state. Stateless: each run reads the
// design through the SpatialIndex and ConstraintResolver passed in, so Drc
// does not subscribe to commits. Same-net prim pairs are skipped (v1);
// prims with a null net (unassigned component pins) are treated as
// distinct-from-everything and always checked.
class Drc {
public:
    Drc(Design& d, SpatialIndex& idx, ConstraintResolver& res);
    std::vector<Violation> run_full();
    std::vector<Violation> run_region(Box window);
private:
    std::vector<Violation> check_layer(Handle layer, std::optional<Box> window);
    Design* d_; SpatialIndex* idx_; ConstraintResolver* res_;
};
}  // namespace sdb
