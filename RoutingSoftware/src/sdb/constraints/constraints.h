#pragma once
#include "sdb/constraints/cset.h"
#include "sdb/core/design.h"
#include <optional>
#include <unordered_map>
namespace sdb {

// Resolves the effective constraints for a net by overlaying constraint sets
// in hierarchy order: system default -> net-class cset -> net's own cset.
// Later levels override only the fields they engage. Results are cached per
// net; the cache is invalidated on any commit/undo/redo that touches a CSet,
// Net, or NetClass handle (a system-cset change journals the CSet handles as
// modified, so it is covered by the same check). Subscribes to the Design on
// construction and unsubscribes in the destructor (same pattern as
// SpatialIndex); the Design must outlive queries.
class ConstraintResolver {
public:
    explicit ConstraintResolver(Design& d);
    ~ConstraintResolver();
    ConstraintResolver(const ConstraintResolver&)            = delete;
    ConstraintResolver& operator=(const ConstraintResolver&) = delete;

    // Resolved minimum spacing between object kinds a and b for `net`.
    // 0 if no level of the hierarchy engages the cell.
    Coord spacing(Handle net, ObjKind a, ObjKind b) const;
    Coord width_min(Handle net) const;           // 0 if nothing set
    Coord width_max(Handle net) const;           // 0 if nothing set
    // Max engaged spacing value across ALL live CSets — the safe R-tree
    // query inflation for clearance checks.
    Coord max_clearance() const;
private:
    const CSet& resolve_(Handle net) const;
    void invalidate_(const CommitEvent&);
    Design* d_;
    Design::SubscriptionId sub_id_;
    mutable std::unordered_map<Handle, CSet> resolved_;  // net -> fully resolved
    mutable std::optional<Coord> max_clearance_;
};
}  // namespace sdb
