#pragma once
#include "sdb/index/spatial_index.h"
#include <unordered_map>
#include <utility>
#include <vector>
namespace sdb {

// Connectivity summary for one net. open_pairs is the number of ratsnest
// (MST) edges still needed to join all clusters: cluster_count-1, or 0 for
// an empty net.
struct NetStatus { int cluster_count = 0; int open_pairs = 0; };

// One ratsnest edge: the closest pair of candidate points between two
// clusters that the MST connects.
struct RatsEdge { Point a, b; };

// Object-level union-find connectivity per net, with a ratsnest MST over the
// resulting clusters. Subscribes to the Design on construction; results are
// cached per net and recomputed lazily after any commit/undo/redo that may
// have changed geometry (see on_commit_ in the .cpp for the invalidation
// policy). The destructor unsubscribes. The Design and SpatialIndex must
// outlive queries.
//
// Joining rule: two of a net's objects are connected if they share a
// conductor layer on which their primitives touch or overlap (prim_gap == 0).
// Component participation is pin-level: only pad prims whose pin is assigned
// to THIS net take part, so a multi-net component never glues foreign
// clusters together. A via participates on every conductor layer in its
// from_layer..to_layer stackup span: with the real pad prim where the
// padstack defines one, else with a synthetic barrel circle (drill diameter)
// at the via position.
//
// Ratsnest representative points: per cluster we collect candidate points
// from the cluster's prims (capsule/arc endpoints, circle/rect centers, poly
// vertices — never an arc's center, which is off the conductor). An MST edge
// between two clusters is reported with the closest PAIR of candidate points
// between the clusters, and weighted by that distance. (A single fixed
// representative per cluster would misplace edges: the nearest endpoint of a
// long trace, not its first point, is where the open should be shown.)
class Connectivity {
public:
    Connectivity(Design& d, SpatialIndex& idx);   // subscribes
    ~Connectivity();                              // unsubscribes
    Connectivity(const Connectivity&)            = delete;
    Connectivity& operator=(const Connectivity&) = delete;

    NetStatus net_status(Handle net);
    std::vector<RatsEdge> ratsnest(Handle net);
    // (object, cluster id) for every object of the net, sorted by handle.
    // Cluster ids are dense, assigned in order of first appearance.
    std::vector<std::pair<Handle, int>> clusters(Handle net);
private:
    struct Entry {
        std::vector<std::pair<Handle, int>> clusters;
        std::vector<RatsEdge> rats;          // MST edges (cluster_count-1)
        int cluster_count = 0;
    };
    const Entry& get_(Handle net);           // cached or recomputed
    Entry recompute_(Handle net) const;
    void on_commit_(const CommitEvent&);
    Design* d_;
    SpatialIndex* idx_;
    Design::SubscriptionId sub_id_;
    std::unordered_map<Handle, Entry> cache_;
};
}  // namespace sdb
