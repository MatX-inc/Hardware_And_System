#include "sdb/index/spatial_index.h"
#include <boost/geometry.hpp>
#include <boost/geometry/index/rtree.hpp>
#include <algorithm>
#include <map>
#include <unordered_map>

namespace bg  = boost::geometry;
namespace bgi = boost::geometry::index;

namespace sdb {
namespace {

using BgPoint = bg::model::point<int64_t, 2, bg::cs::cartesian>;
using BgBox   = bg::model::box<BgPoint>;
using Entry   = std::pair<BgBox, Handle>;

BgBox to_bg(const Box& b) {
    return BgBox{BgPoint{b.lo.x, b.lo.y}, BgPoint{b.hi.x, b.hi.y}};
}

// Kinds the index tracks: physical objects with per-layer footprints.
bool indexed_kind(Kind k) {
    return k == Kind::Cline || k == Kind::Via ||
           k == Kind::Shape || k == Kind::Component;
}

// Nets an object is attached to (a Component may touch several via its pins).
std::vector<Handle> nets_of(const Design& d, Handle h) {
    std::vector<Handle> nets;
    switch (h.kind) {
        case Kind::Cline: if (const Cline* c = d.cline(h)) nets.push_back(c->net); break;
        case Kind::Via:   if (const Via* v = d.via(h))     nets.push_back(v->net); break;
        case Kind::Shape: if (const Shape* s = d.shape(h)) nets.push_back(s->net); break;
        case Kind::Component:
            if (const Component* c = d.component(h))
                for (const auto& [pin, net] : c->pin_nets) nets.push_back(net);
            break;
        default: break;
    }
    std::sort(nets.begin(), nets.end());
    nets.erase(std::unique(nets.begin(), nets.end()), nets.end());
    nets.erase(std::remove_if(nets.begin(), nets.end(),
                              [](Handle n) { return !n.valid(); }),
               nets.end());
    return nets;
}

void erase_one(std::vector<Handle>& v, Handle h) {
    auto it = std::find(v.begin(), v.end(), h);
    if (it != v.end()) v.erase(it);
}

}  // namespace

struct SpatialIndex::Impl {
    // One R-tree per (layer, object kind). Splitting by kind keeps removal
    // and kind-filtered queries cheap; query_box unions across kinds.
    struct LayerKey {
        Handle layer;
        Kind kind;
        bool operator<(const LayerKey& o) const {
            if (!(layer == o.layer)) return layer < o.layer;
            return kind < o.kind;
        }
    };
    using Rtree = bgi::rtree<Entry, bgi::rstar<16>>;
    std::map<LayerKey, Rtree> trees;
    // object -> its indexed bbox per layer (exact removal of stale entries)
    std::unordered_map<Handle, std::map<Handle, Box>> bbox_cache;
    // net -> objects and object -> nets (kept mutually consistent)
    std::unordered_map<Handle, std::vector<Handle>> net_to_objects;
    std::unordered_map<Handle, std::vector<Handle>> object_nets;
};

SpatialIndex::SpatialIndex(Design& d) : d_(&d), impl_(std::make_unique<Impl>()) {
    sub_id_ = d.subscribe([this](const CommitEvent& e) { on_commit(e); });
    for (Kind k : {Kind::Cline, Kind::Via, Kind::Shape, Kind::Component})
        d.for_each_object(k, [this](Handle h) { insert(h); });
}

SpatialIndex::~SpatialIndex() { d_->unsubscribe(sub_id_); }

void SpatialIndex::on_commit(const CommitEvent& e) {
    for (Handle h : e.deleted)  if (indexed_kind(h.kind)) remove(h);
    for (Handle h : e.modified) if (indexed_kind(h.kind)) { remove(h); insert(h); }
    for (Handle h : e.created)  if (indexed_kind(h.kind)) insert(h);
}

void SpatialIndex::insert(Handle h) {
    if (!indexed_kind(h.kind)) return;
    std::map<Handle, Box> boxes = object_bboxes(*d_, h);
    for (const auto& [layer, box] : boxes)
        impl_->trees[{layer, h.kind}].insert({to_bg(box), h});
    if (!boxes.empty()) impl_->bbox_cache[h] = std::move(boxes);
    std::vector<Handle> nets = nets_of(*d_, h);
    for (Handle n : nets) impl_->net_to_objects[n].push_back(h);
    if (!nets.empty()) impl_->object_nets[h] = std::move(nets);
}

void SpatialIndex::remove(Handle h) {
    if (auto it = impl_->bbox_cache.find(h); it != impl_->bbox_cache.end()) {
        for (const auto& [layer, box] : it->second) {
            auto tit = impl_->trees.find({layer, h.kind});
            if (tit != impl_->trees.end()) {
                tit->second.remove({to_bg(box), h});
                if (tit->second.empty()) impl_->trees.erase(tit);
            }
        }
        impl_->bbox_cache.erase(it);
    }
    if (auto it = impl_->object_nets.find(h); it != impl_->object_nets.end()) {
        for (Handle n : it->second) {
            auto nit = impl_->net_to_objects.find(n);
            if (nit == impl_->net_to_objects.end()) continue;
            erase_one(nit->second, h);
            if (nit->second.empty()) impl_->net_to_objects.erase(nit);
        }
        impl_->object_nets.erase(it);
    }
}

std::vector<Handle> SpatialIndex::query_box(Handle layer, Box b) const {
    std::vector<Handle> out;
    BgBox probe = to_bg(b);
    for (const auto& [key, tree] : impl_->trees) {
        if (!(key.layer == layer)) continue;
        for (auto it = tree.qbegin(bgi::intersects(probe)); it != tree.qend(); ++it)
            out.push_back(it->second);
    }
    std::sort(out.begin(), out.end());
    return out;
}

std::vector<Handle> SpatialIndex::query_clearance(Handle layer, Box b,
                                                  Coord clearance) const {
    return query_box(layer, b.inflated(clearance));
}

std::vector<Handle> SpatialIndex::net_objects(Handle net) const {
    auto it = impl_->net_to_objects.find(net);
    if (it == impl_->net_to_objects.end()) return {};
    std::vector<Handle> out = it->second;
    std::sort(out.begin(), out.end());
    return out;
}

}  // namespace sdb
