#include "sdb/connectivity/connectivity.h"
#include "sdb/geom/object_geometry.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <map>
#include <set>

namespace sdb {
namespace {

bool geometric_kind(Kind k) {
    return k == Kind::Cline || k == Kind::Via ||
           k == Kind::Shape || k == Kind::Component;
}

// Kinds whose definitions feed object geometry indirectly: a change to one
// can move pads on ANY net, so the whole cache must be dropped.
bool definition_kind(Kind k) {
    return k == Kind::Padstack || k == Kind::Symbol || k == Kind::Layer;
}

// ----------------------------------------------------------------- union-find
struct UnionFind {
    std::vector<int> parent;
    explicit UnionFind(size_t n) : parent(n) {
        for (size_t i = 0; i < n; ++i) parent[i] = int(i);
    }
    int find(int x) {
        while (parent[x] != x) { parent[x] = parent[parent[x]]; x = parent[x]; }
        return x;
    }
    void unite(int a, int b) { parent[find(a)] = find(b); }
};

// Candidate points of a prim for ratsnest representatives: points that lie on
// the conductor (capsule/arc endpoints, circle/rect centers, poly vertices).
// An arc's geometric center is NOT a candidate — it is off the copper.
void candidate_points(const Prim& p, std::vector<Point>& out) {
    switch (p.kind) {
        case Prim::Kind::Capsule:
        case Prim::Kind::Arc:
            out.push_back(p.a);
            out.push_back(p.b);
            break;
        case Prim::Kind::Circle:
        case Prim::Kind::Rect:
            out.push_back(p.center);
            break;
        case Prim::Kind::Poly:
            out.insert(out.end(), p.poly.begin(), p.poly.end());
            break;
    }
}

double point_dist(Point a, Point b) {
    double dx = double(a.x - b.x), dy = double(a.y - b.y);
    return std::hypot(dx, dy);
}

}  // namespace

Connectivity::Connectivity(Design& d, SpatialIndex& idx) : d_(&d), idx_(&idx) {
    sub_id_ = d.subscribe([this](const CommitEvent& e) { on_commit_(e); });
}

Connectivity::~Connectivity() { d_->unsubscribe(sub_id_); }

// Invalidation policy (kept deliberately coarse for v1):
//   - Any deleted handle: the object's net is no longer readable through the
//     accessors, so its nets are unrecoverable -> drop the whole cache.
//   - Modified geometric object: the accessors only show the object's NEW
//     net — if the edit (or an undo of one) moved it between nets, the old
//     net is unrecoverable -> drop the whole cache. Recompute is lazy and
//     per-net, so only nets actually queried afterwards pay for this.
//   - Created geometric object: invalidate just its nets (for a component,
//     every pin net) — a freshly created object has no prior net to leak.
//   - Created/modified definition object (padstack/symbol/layer): drop the
//     whole cache — pad expansion and the via stackup span can change for
//     objects on any net.
// Net/NetClass/CSet changes never alter geometric connectivity.
void Connectivity::on_commit_(const CommitEvent& e) {
    if (!e.deleted.empty()) { cache_.clear(); return; }
    for (Handle h : e.modified)
        if (geometric_kind(h.kind) || definition_kind(h.kind)) {
            cache_.clear();
            return;
        }
    for (Handle h : e.created) {
        if (definition_kind(h.kind)) { cache_.clear(); return; }
        switch (h.kind) {
            case Kind::Cline: if (const Cline* c = d_->cline(h)) cache_.erase(c->net); break;
            case Kind::Via:   if (const Via* v = d_->via(h))     cache_.erase(v->net); break;
            case Kind::Shape: if (const Shape* s = d_->shape(h)) cache_.erase(s->net); break;
            case Kind::Component:
                if (const Component* c = d_->component(h))
                    for (const auto& [pin, net] : c->pin_nets) cache_.erase(net);
                break;
            default: break;
        }
    }
}

const Connectivity::Entry& Connectivity::get_(Handle net) {
    auto it = cache_.find(net);
    if (it == cache_.end()) it = cache_.emplace(net, recompute_(net)).first;
    return it->second;
}

Connectivity::Entry Connectivity::recompute_(Handle net) const {
    Entry out;
    std::vector<Handle> objects = idx_->net_objects(net);   // sorted; includes
    // components with any pin on `net` (SpatialIndex nets components under
    // every pin net).
    if (objects.empty()) return out;

    // ---- gather this net's primitives per (object, layer) -----------------
    // Pin-level membership: only prims whose net field is `net` participate,
    // so a component's pads on other nets never glue this net's clusters.
    struct LayerPrims { std::vector<Prim> prims; Box bbox = Box::empty(); };
    std::map<Handle, std::map<Handle, LayerPrims>> geo;  // object -> layer -> prims
    std::vector<Handle> conductors = d_->conductor_layers();
    auto is_conductor = [&](Handle l) {
        return std::find(conductors.begin(), conductors.end(), l) != conductors.end();
    };

    for (Handle h : objects) {
        auto& layers = geo[h];   // ensure entry even if no prims survive
        for (const auto& [layer, box] : object_bboxes(*d_, h)) {
            if (!is_conductor(layer)) continue;   // mask pads never connect
            for (Prim& p : object_primitives(*d_, h, layer)) {
                if (!(p.net == net)) continue;
                auto& lp = layers[layer];
                lp.bbox = lp.bbox.united(prim_bbox(p));
                lp.prims.push_back(std::move(p));
            }
        }
        // Via stackup span: a via participates on every conductor layer in
        // [from..to] (stackup order). Where the padstack defines no pad we
        // synthesize a barrel circle (drill diameter; possibly a point) at
        // the via position, so mid-span joins still register.
        if (h.kind == Kind::Via) {
            const Via* v = d_->via(h);
            if (!v) continue;
            auto fi = std::find(conductors.begin(), conductors.end(), v->from_layer);
            auto ti = std::find(conductors.begin(), conductors.end(), v->to_layer);
            if (fi == conductors.end() || ti == conductors.end()) continue;
            Coord drill = 0;
            if (const Padstack* ps = d_->padstack(v->padstack)) drill = ps->drill;
            auto [lo, hi] = std::minmax(fi, ti);
            for (auto it = lo; it <= hi; ++it) {
                if (layers.count(*it)) continue;     // real pad already there
                Prim p;
                p.kind = Prim::Kind::Circle;
                p.center = p.a = p.b = v->pos;
                p.diameter = drill;
                p.net = v->net;
                auto& lp = layers[*it];
                lp.bbox = prim_bbox(p);
                lp.prims.push_back(std::move(p));
            }
        }
    }

    // Objects contributing no geometry on this net (unresolvable padstack or
    // symbol, degenerate shape) are dropped: they cannot cluster and have no
    // representative point for the ratsnest.
    std::vector<Handle> live;
    for (const auto& [h, layers] : geo) {
        bool any = false;
        for (const auto& [l, lp] : layers) any |= !lp.prims.empty();
        if (any) live.push_back(h);
    }
    if (live.empty()) return out;
    std::sort(live.begin(), live.end());
    std::map<Handle, int> index_of;
    for (size_t i = 0; i < live.size(); ++i) index_of[live[i]] = int(i);

    // ---- union-find over touching pairs ------------------------------------
    // Candidate pairs come from the R-tree (query_box on each object's per-
    // layer prim bbox), not all-pairs. Discovery can be one-sided (a via's
    // synthetic mid-span prim is not in the R-tree, but the via queries the
    // tree from its side), so dedupe with a canonical pair set.
    UnionFind uf(live.size());
    std::set<std::pair<Handle, Handle>> checked;
    for (Handle h : live) {
        for (const auto& [layer, lp] : geo[h]) {
            if (lp.prims.empty()) continue;
            for (Handle c : idx_->query_box(layer, lp.bbox)) {
                if (c == h) continue;
                auto io = index_of.find(c);
                if (io == index_of.end()) continue;       // not on this net
                std::pair<Handle, Handle> key = std::minmax(h, c);
                if (!checked.insert(key).second) continue;
                // touch test: any prim pair with gap <= 0 on a shared layer
                // (arc gaps are conservative and may come back slightly
                // negative when touching; capsule gaps are clamped at 0)
                bool touch = false;
                const auto& a_layers = geo[h];
                const auto& b_layers = geo[c];
                for (const auto& [la, lpa] : a_layers) {
                    auto bit = b_layers.find(la);
                    if (bit == b_layers.end()) continue;
                    for (const Prim& pa : lpa.prims) {
                        for (const Prim& pb : bit->second.prims)
                            if (prim_gap(pa, pb) <= 0.0) { touch = true; break; }
                        if (touch) break;
                    }
                    if (touch) break;
                }
                if (touch) uf.unite(index_of[h], io->second);
            }
        }
    }

    // ---- dense cluster ids (order of first appearance over sorted objects) -
    std::map<int, int> root_to_id;
    for (size_t i = 0; i < live.size(); ++i) {
        int root = uf.find(int(i));
        auto [it, inserted] = root_to_id.try_emplace(root, int(root_to_id.size()));
        out.clusters.emplace_back(live[i], it->second);
    }
    out.cluster_count = int(root_to_id.size());

    // ---- ratsnest MST over clusters ----------------------------------------
    // Per-cluster candidate point sets; edge weight between two clusters is
    // the distance of the closest pair of candidate points, and the edge is
    // reported with exactly that pair (see header comment).
    int k = out.cluster_count;
    if (k > 1) {
        std::vector<std::vector<Point>> pts(k);
        for (size_t i = 0; i < live.size(); ++i) {
            int id = out.clusters[i].second;
            for (const auto& [layer, lp] : geo[live[i]])
                for (const Prim& p : lp.prims) candidate_points(p, pts[id]);
        }
        struct Best { double d = std::numeric_limits<double>::infinity(); Point a, b; };
        auto closest = [&](int ca, int cb) {
            Best r;
            for (Point pa : pts[ca])
                for (Point pb : pts[cb]) {
                    double dd = point_dist(pa, pb);
                    if (dd < r.d) r = {dd, pa, pb};
                }
            return r;
        };
        // Prim's algorithm: grow the tree from cluster 0; best[i] = closest
        // connection from non-tree cluster i to the tree so far.
        std::vector<bool> in_tree(k, false);
        std::vector<Best> best(k);
        std::vector<std::pair<Point, Point>> link(k);  // (tree pt, cluster pt)
        in_tree[0] = true;
        for (int i = 1; i < k; ++i) {
            Best b = closest(0, i);
            best[i] = b;
            link[i] = {b.a, b.b};
        }
        for (int added = 1; added < k; ++added) {
            int pick = -1;
            for (int i = 0; i < k; ++i)
                if (!in_tree[i] && (pick < 0 || best[i].d < best[pick].d)) pick = i;
            in_tree[pick] = true;
            out.rats.push_back({link[pick].first, link[pick].second});
            for (int i = 0; i < k; ++i) {
                if (in_tree[i]) continue;
                Best b = closest(pick, i);
                if (b.d < best[i].d) { best[i] = b; link[i] = {b.a, b.b}; }
            }
        }
    }
    return out;
}

NetStatus Connectivity::net_status(Handle net) {
    const Entry& e = get_(net);
    return {e.cluster_count, e.cluster_count > 0 ? e.cluster_count - 1 : 0};
}

std::vector<RatsEdge> Connectivity::ratsnest(Handle net) { return get_(net).rats; }

std::vector<std::pair<Handle, int>> Connectivity::clusters(Handle net) {
    return get_(net).clusters;
}

}  // namespace sdb
