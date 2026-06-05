#include "sdb/constraints/constraints.h"
#include <algorithm>
namespace sdb {

namespace {
// Overlay `over` onto `base`: engaged fields of `over` win.
void overlay(CSet& base, const CSet& over) {
    if (over.width_min) base.width_min = over.width_min;
    if (over.width_max) base.width_max = over.width_max;
    for (size_t i = 0; i < over.spacing.v.size(); ++i)
        if (over.spacing.v[i]) base.spacing.v[i] = over.spacing.v[i];
}
}  // namespace

ConstraintResolver::ConstraintResolver(Design& d) : d_(&d) {
    sub_id_ = d_->subscribe([this](const CommitEvent& e) { invalidate_(e); });
}

ConstraintResolver::~ConstraintResolver() { d_->unsubscribe(sub_id_); }

void ConstraintResolver::invalidate_(const CommitEvent& e) {
    // Targeted invalidation: resolution depends only on CSets (values and
    // the system default), Nets (cset/net_class links), and NetClasses
    // (cset links). A system-cset switch surfaces its CSet handles in
    // created/modified, so it is covered here too.
    auto touches = [](const std::vector<Handle>& v) {
        return std::any_of(v.begin(), v.end(), [](Handle h) {
            return h.kind == Kind::CSet || h.kind == Kind::Net ||
                   h.kind == Kind::NetClass;
        });
    };
    if (touches(e.created) || touches(e.modified) || touches(e.deleted)) {
        resolved_.clear();
        max_clearance_.reset();
    }
}

const CSet& ConstraintResolver::resolve_(Handle net) const {
    if (auto it = resolved_.find(net); it != resolved_.end()) return it->second;
    CSet out;   // start empty, overlay system -> class -> net
    if (const CSet* sys = d_->cset(d_->system_cset())) overlay(out, *sys);
    if (const Net* n = d_->net(net)) {
        if (const NetClass* nc = d_->net_class(n->net_class))
            if (const CSet* c = d_->cset(nc->cset)) overlay(out, *c);
        if (const CSet* c = d_->cset(n->cset)) overlay(out, *c);
    }
    return resolved_.emplace(net, std::move(out)).first->second;
}

Coord ConstraintResolver::spacing(Handle net, ObjKind a, ObjKind b) const {
    return resolve_(net).spacing.get(a, b).value_or(0);
}

Coord ConstraintResolver::width_min(Handle net) const {
    return resolve_(net).width_min.value_or(0);
}

Coord ConstraintResolver::width_max(Handle net) const {
    return resolve_(net).width_max.value_or(0);
}

Coord ConstraintResolver::max_clearance() const {
    if (!max_clearance_) {
        Coord m = 0;
        d_->for_each_object(Kind::CSet, [&](Handle h) {
            const CSet* c = d_->cset(h);
            if (!c) return;
            for (const auto& cell : c->spacing.v)
                if (cell) m = std::max(m, *cell);
        });
        max_clearance_ = m;
    }
    return *max_clearance_;
}
}  // namespace sdb
