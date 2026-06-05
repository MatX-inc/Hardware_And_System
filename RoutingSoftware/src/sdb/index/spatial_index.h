#pragma once
#include "sdb/core/design.h"
#include "sdb/geom/object_geometry.h"
#include <memory>
#include <vector>
namespace sdb {

// Incrementally-maintained per-layer R-tree over object bounding boxes plus a
// net -> objects index. Subscribes to the Design on construction and stays in
// sync through commits, undo, and redo. Indexed kinds: Cline, Via, Shape,
// Component. The destructor unsubscribes, so a SpatialIndex may be destroyed
// before the Design; the Design must still outlive it for queries
// (belt-and-braces: d_ is dereferenced on every commit and insert).
class SpatialIndex {
public:
    explicit SpatialIndex(Design& d);          // subscribes to d, bulk-loads existing
    ~SpatialIndex();                           // unsubscribes from d
    SpatialIndex(const SpatialIndex&)            = delete;
    SpatialIndex& operator=(const SpatialIndex&) = delete;

    // Handles of all objects whose bbox on `layer` intersects b (closed-box
    // semantics: touching counts). Sorted by handle for determinism.
    std::vector<Handle> query_box(Handle layer, Box b) const;
    // query_box with b inflated by `clearance` on all sides.
    std::vector<Handle> query_clearance(Handle layer, Box b, Coord clearance) const;
    // All indexed objects attached to `net`, sorted by handle.
    std::vector<Handle> net_objects(Handle net) const;
private:
    void on_commit(const CommitEvent&);
    void insert(Handle);
    void remove(Handle);
    Design* d_;
    Design::SubscriptionId sub_id_;
    struct Impl;
    std::unique_ptr<Impl> impl_;   // hides boost headers from users
};
}  // namespace sdb
