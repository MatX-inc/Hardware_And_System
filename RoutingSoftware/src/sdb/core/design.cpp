#include "sdb/core/design.h"
#include "sdb/geom/kernel.h"
#include <algorithm>
#include <cassert>
#include <utility>

namespace sdb {

// ---------------------------------------------------------------- Transaction

Transaction::Transaction(Design& d, std::string name) : d_(&d) {
    rec_.name = std::move(name);
}

Transaction::~Transaction() {
    if (open_) abort();
}

void Transaction::commit() {
    assert(open_);
    open_ = false;
    if (rec_.ops.empty() && rec_.prop_ops.empty() && !rec_.system_cset_change)
        return;  // empty txn: no journal entry
    d_->redo_.clear();
    CommitEvent e = d_->make_event_(rec_, /*reversed=*/false);
    d_->undo_.push_back(std::move(rec_));
    d_->notify_(e);
}

void Transaction::abort() {
    if (!open_) return;
    open_ = false;
    d_->apply_inverse_(rec_);   // roll back immediately; no notification
    rec_ = {};
}

Handle Transaction::create_layer(Layer l) {
    assert(open_);
    l.order = d_->next_layer_order_++;
    Handle h = d_->layers_.alloc(std::move(l));
    rec_.ops.push_back({h, std::nullopt, AnyObject(*d_->layers_.get(h))});
    return h;
}
Handle Transaction::create_padstack(Padstack p) {
    assert(open_);
    Handle h = d_->padstacks_.alloc(std::move(p));
    rec_.ops.push_back({h, std::nullopt, AnyObject(*d_->padstacks_.get(h))});
    return h;
}
Handle Transaction::create_symbol(Symbol s) {
    assert(open_);
    Handle h = d_->symbols_.alloc(std::move(s));
    rec_.ops.push_back({h, std::nullopt, AnyObject(*d_->symbols_.get(h))});
    return h;
}
Handle Transaction::create_component(Component c) {
    assert(open_);
    Handle h = d_->components_.alloc(std::move(c));
    rec_.ops.push_back({h, std::nullopt, AnyObject(*d_->components_.get(h))});
    return h;
}
Handle Transaction::create_net(Net n) {
    assert(open_);
    Handle h = d_->nets_.alloc(std::move(n));
    rec_.ops.push_back({h, std::nullopt, AnyObject(*d_->nets_.get(h))});
    return h;
}
Handle Transaction::create_net_class(NetClass nc) {
    assert(open_);
    Handle h = d_->net_classes_.alloc(std::move(nc));
    rec_.ops.push_back({h, std::nullopt, AnyObject(*d_->net_classes_.get(h))});
    return h;
}
Handle Transaction::create_cline(Cline c) {
    assert(open_);
    Handle h = d_->clines_.alloc(std::move(c));
    rec_.ops.push_back({h, std::nullopt, AnyObject(*d_->clines_.get(h))});
    return h;
}
Handle Transaction::create_via(Via v) {
    assert(open_);
    Handle h = d_->vias_.alloc(std::move(v));
    rec_.ops.push_back({h, std::nullopt, AnyObject(*d_->vias_.get(h))});
    return h;
}
Handle Transaction::create_shape(Shape s) {
    assert(open_);
    Handle h = d_->shapes_.alloc(std::move(s));
    rec_.ops.push_back({h, std::nullopt, AnyObject(*d_->shapes_.get(h))});
    return h;
}
Handle Transaction::create_cset(CSet c) {
    assert(open_);
    Handle h = d_->csets_.alloc(std::move(c));
    rec_.ops.push_back({h, std::nullopt, AnyObject(*d_->csets_.get(h))});
    return h;
}

void Transaction::assign_pin(Handle component, const std::string& pin, Handle net) {
    assert(open_);
    Component* c = d_->components_.get(component);
    assert(c != nullptr);
    if (!c) return;
    AnyObject before = *c;          // journal as a modify op on the Component
    c->pin_nets[pin] = net;
    rec_.ops.push_back({component, std::move(before), AnyObject(*c)});
}

void Transaction::set_property(Handle target, const std::string& key, PropValue value) {
    assert(open_);
    std::optional<PropValue> before;
    if (auto it = d_->props_.find(target); it != d_->props_.end()) {
        if (auto kit = it->second.find(key); kit != it->second.end()) before = kit->second;
    }
    rec_.prop_ops.push_back({target, key, std::move(before), value});
    d_->props_[target][key] = std::move(value);
}

void Transaction::update_cline(Handle h, Cline c) {
    assert(open_);
    Cline* p = d_->clines_.get(h);
    assert(p != nullptr);
    if (!p) return;
    AnyObject before = *p;
    *p = std::move(c);
    rec_.ops.push_back({h, std::move(before), AnyObject(*p)});
}
void Transaction::update_via(Handle h, Via v) {
    assert(open_);
    Via* p = d_->vias_.get(h);
    assert(p != nullptr);
    if (!p) return;
    AnyObject before = *p;
    *p = std::move(v);
    rec_.ops.push_back({h, std::move(before), AnyObject(*p)});
}
void Transaction::update_shape(Handle h, Shape s) {
    assert(open_);
    Shape* p = d_->shapes_.get(h);
    assert(p != nullptr);
    if (!p) return;
    AnyObject before = *p;
    *p = std::move(s);
    rec_.ops.push_back({h, std::move(before), AnyObject(*p)});
}
void Transaction::update_cset(Handle h, CSet c) {
    assert(open_);
    CSet* p = d_->csets_.get(h);
    assert(p != nullptr);
    if (!p) return;
    AnyObject before = *p;
    *p = std::move(c);
    rec_.ops.push_back({h, std::move(before), AnyObject(*p)});
}

void Transaction::set_system_cset(Handle h) {
    assert(open_);
    // Journal the (before, after) pair once per txn: keep the original
    // `before` so undo restores the pre-txn value, update `after` so the
    // last call in the txn wins on redo.
    if (!rec_.system_cset_change)
        rec_.system_cset_change = {d_->system_cset_, h};
    else
        rec_.system_cset_change->second = h;
    d_->system_cset_ = h;
}

void Transaction::erase(Handle h) {
    assert(open_);
    std::optional<AnyObject> before = d_->snapshot_(h);
    if (!before) return;            // stale/unknown handle: nothing to erase
    // Journal each property of the erased object as a PropOp (after=nullopt)
    // so undo restores both the object and its property map entry.
    if (auto it = d_->props_.find(h); it != d_->props_.end()) {
        for (const auto& [key, val] : it->second)
            rec_.prop_ops.push_back({h, key, val, std::nullopt});
        d_->props_.erase(it);
    }
    d_->free_(h);
    rec_.ops.push_back({h, std::move(before), std::nullopt});
}

// --------------------------------------------------------------------- Design

Design::Design(std::string name) : name_(std::move(name)) {}

Transaction Design::begin(std::string txn_name) {
    return Transaction(*this, std::move(txn_name));
}

const Layer*     Design::layer(Handle h) const      { return layers_.get(h); }
const Padstack*  Design::padstack(Handle h) const   { return padstacks_.get(h); }
const Symbol*    Design::symbol(Handle h) const     { return symbols_.get(h); }
const Component* Design::component(Handle h) const  { return components_.get(h); }
const Net*       Design::net(Handle h) const        { return nets_.get(h); }
const NetClass*  Design::net_class(Handle h) const  { return net_classes_.get(h); }
const Cline*     Design::cline(Handle h) const      { return clines_.get(h); }
const Via*       Design::via(Handle h) const        { return vias_.get(h); }
const Shape*     Design::shape(Handle h) const      { return shapes_.get(h); }
const CSet*      Design::cset(Handle h) const       { return csets_.get(h); }

Handle Design::layer_by_name(const std::string& name) const {
    Handle found = kNullHandle;
    layers_.for_each([&](Handle h, const Layer& l) {
        if (l.name == name && !found.valid()) found = h;
    });
    return found;
}

Handle Design::net_by_name(const std::string& name) const {
    Handle found = kNullHandle;
    nets_.for_each([&](Handle h, const Net& n) {
        if (n.name == name && !found.valid()) found = h;
    });
    return found;
}

std::vector<Handle> Design::conductor_layers() const {
    std::vector<std::pair<int, Handle>> v;
    layers_.for_each([&](Handle h, const Layer& l) {
        if (l.type == LayerType::Conductor) v.emplace_back(l.order, h);
    });
    std::sort(v.begin(), v.end(),
              [](const auto& a, const auto& b) { return a.first < b.first; });
    std::vector<Handle> out;
    out.reserve(v.size());
    for (const auto& [order, h] : v) out.push_back(h);
    return out;
}

void Design::for_each_object(Kind k, const std::function<void(Handle)>& f) const {
    auto visit = [&](const auto& arena) {
        arena.for_each([&](Handle h, const auto&) { f(h); });
    };
    switch (k) {
        case Kind::Layer:     visit(layers_); break;
        case Kind::Padstack:  visit(padstacks_); break;
        case Kind::Symbol:    visit(symbols_); break;
        case Kind::Component: visit(components_); break;
        case Kind::Net:       visit(nets_); break;
        case Kind::NetClass:  visit(net_classes_); break;
        case Kind::Cline:     visit(clines_); break;
        case Kind::Via:       visit(vias_); break;
        case Kind::Shape:     visit(shapes_); break;
        case Kind::CSet:      visit(csets_); break;
    }
}

Point Design::pin_position(Handle component, const std::string& pin) const {
    const Component* c = components_.get(component);
    if (!c) return {};
    const Symbol* s = symbols_.get(c->symbol);
    if (!s) return {};
    for (const SymbolPin& p : s->pins) {
        if (p.number != pin) continue;
        Point pt = p.offset;
        if (c->mirrored) pt.x = -pt.x;
        switch (((c->rotation_cw_deg % 360) + 360) % 360) {
            case 90:  pt = {pt.y, -pt.x}; break;
            case 180: pt = {-pt.x, -pt.y}; break;
            case 270: pt = {-pt.y, pt.x}; break;
            default: break;
        }
        return {pt.x + c->origin.x, pt.y + c->origin.y};
    }
    return {};
}

namespace {
template <typename T>
std::optional<T> get_prop(
    const std::unordered_map<Handle, std::map<std::string, PropValue>>& props,
    Handle h, const std::string& key) {
    auto it = props.find(h);
    if (it == props.end()) return std::nullopt;
    auto kit = it->second.find(key);
    if (kit == it->second.end()) return std::nullopt;
    if (const T* v = std::get_if<T>(&kit->second)) return *v;
    return std::nullopt;
}
}  // namespace

std::optional<double> Design::get_property_double(Handle h, const std::string& key) const {
    return get_prop<double>(props_, h, key);
}
std::optional<std::string> Design::get_property_string(Handle h, const std::string& key) const {
    return get_prop<std::string>(props_, h, key);
}
std::optional<int64_t> Design::get_property_int(Handle h, const std::string& key) const {
    return get_prop<int64_t>(props_, h, key);
}
std::optional<bool> Design::get_property_bool(Handle h, const std::string& key) const {
    return get_prop<bool>(props_, h, key);
}
std::optional<Handle> Design::get_property_handle(Handle h, const std::string& key) const {
    return get_prop<Handle>(props_, h, key);
}
std::optional<PropValue> Design::get_property(Handle h, const std::string& key) const {
    auto it = props_.find(h);
    if (it == props_.end()) return std::nullopt;
    auto kit = it->second.find(key);
    if (kit == it->second.end()) return std::nullopt;
    return kit->second;
}

// ----------------------------------------------------------- journal plumbing

std::optional<AnyObject> Design::snapshot_(Handle h) const {
    switch (h.kind) {
        case Kind::Layer:     if (const auto* p = layers_.get(h))      return AnyObject(*p); break;
        case Kind::Padstack:  if (const auto* p = padstacks_.get(h))   return AnyObject(*p); break;
        case Kind::Symbol:    if (const auto* p = symbols_.get(h))     return AnyObject(*p); break;
        case Kind::Component: if (const auto* p = components_.get(h))  return AnyObject(*p); break;
        case Kind::Net:       if (const auto* p = nets_.get(h))        return AnyObject(*p); break;
        case Kind::NetClass:  if (const auto* p = net_classes_.get(h)) return AnyObject(*p); break;
        case Kind::Cline:     if (const auto* p = clines_.get(h))      return AnyObject(*p); break;
        case Kind::Via:       if (const auto* p = vias_.get(h))        return AnyObject(*p); break;
        case Kind::Shape:     if (const auto* p = shapes_.get(h))      return AnyObject(*p); break;
        case Kind::CSet:      if (const auto* p = csets_.get(h))       return AnyObject(*p); break;
    }
    return std::nullopt;
}

void Design::write_(Handle h, const AnyObject& v) {
    switch (h.kind) {
        case Kind::Layer:     { auto* p = layers_.get(h);      assert(p); *p = std::get<Layer>(v); break; }
        case Kind::Padstack:  { auto* p = padstacks_.get(h);   assert(p); *p = std::get<Padstack>(v); break; }
        case Kind::Symbol:    { auto* p = symbols_.get(h);     assert(p); *p = std::get<Symbol>(v); break; }
        case Kind::Component: { auto* p = components_.get(h);  assert(p); *p = std::get<Component>(v); break; }
        case Kind::Net:       { auto* p = nets_.get(h);        assert(p); *p = std::get<Net>(v); break; }
        case Kind::NetClass:  { auto* p = net_classes_.get(h); assert(p); *p = std::get<NetClass>(v); break; }
        case Kind::Cline:     { auto* p = clines_.get(h);      assert(p); *p = std::get<Cline>(v); break; }
        case Kind::Via:       { auto* p = vias_.get(h);        assert(p); *p = std::get<Via>(v); break; }
        case Kind::Shape:     { auto* p = shapes_.get(h);      assert(p); *p = std::get<Shape>(v); break; }
        case Kind::CSet:      { auto* p = csets_.get(h);       assert(p); *p = std::get<CSet>(v); break; }
    }
}

void Design::resurrect_(Handle h, const AnyObject& v) {
    switch (h.kind) {
        case Kind::Layer:     layers_.resurrect(h, std::get<Layer>(v)); break;
        case Kind::Padstack:  padstacks_.resurrect(h, std::get<Padstack>(v)); break;
        case Kind::Symbol:    symbols_.resurrect(h, std::get<Symbol>(v)); break;
        case Kind::Component: components_.resurrect(h, std::get<Component>(v)); break;
        case Kind::Net:       nets_.resurrect(h, std::get<Net>(v)); break;
        case Kind::NetClass:  net_classes_.resurrect(h, std::get<NetClass>(v)); break;
        case Kind::Cline:     clines_.resurrect(h, std::get<Cline>(v)); break;
        case Kind::Via:       vias_.resurrect(h, std::get<Via>(v)); break;
        case Kind::Shape:     shapes_.resurrect(h, std::get<Shape>(v)); break;
        case Kind::CSet:      csets_.resurrect(h, std::get<CSet>(v)); break;
    }
}

void Design::free_(Handle h) {
    switch (h.kind) {
        case Kind::Layer:     layers_.free(h); break;
        case Kind::Padstack:  padstacks_.free(h); break;
        case Kind::Symbol:    symbols_.free(h); break;
        case Kind::Component: components_.free(h); break;
        case Kind::Net:       nets_.free(h); break;
        case Kind::NetClass:  net_classes_.free(h); break;
        case Kind::Cline:     clines_.free(h); break;
        case Kind::Via:       vias_.free(h); break;
        case Kind::Shape:     shapes_.free(h); break;
        case Kind::CSet:      csets_.free(h); break;
    }
}

void Design::apply_forward_(const TxnRecord& r) {
    for (const JournalOp& op : r.ops) {
        if (!op.before)       resurrect_(op.h, *op.after);   // create (slot generation preserved by free)
        else if (!op.after)   free_(op.h);                   // erase
        else                  write_(op.h, *op.after);       // modify
    }
    for (const PropOp& op : r.prop_ops) {
        if (op.after) {
            props_[op.h][op.key] = *op.after;
        } else if (auto it = props_.find(op.h); it != props_.end()) {
            it->second.erase(op.key);
            if (it->second.empty()) props_.erase(it);
        }
    }
    if (r.system_cset_change) system_cset_ = r.system_cset_change->second;
}

void Design::apply_inverse_(const TxnRecord& r) {
    if (r.system_cset_change) system_cset_ = r.system_cset_change->first;
    for (auto it = r.prop_ops.rbegin(); it != r.prop_ops.rend(); ++it) {
        if (it->before) {
            props_[it->h][it->key] = *it->before;
        } else if (auto pit = props_.find(it->h); pit != props_.end()) {
            pit->second.erase(it->key);
            if (pit->second.empty()) props_.erase(pit);
        }
    }
    for (auto it = r.ops.rbegin(); it != r.ops.rend(); ++it) {
        if (!it->before)      free_(it->h);                  // undo create
        else if (!it->after)  resurrect_(it->h, *it->before);// undo erase
        else                  write_(it->h, *it->before);    // undo modify
    }
}

namespace {
Box cline_box(const Cline& c) {
    Box b = Box::empty();
    for (const Segment& s : c.segs) {
        Box sb = s.is_arc ? geom::arc_bbox(s.a, s.b, s.center, s.cw)
                          : Box::of(s.a, s.b);
        b = b.united(sb.inflated(s.width / 2));
    }
    return b;
}
Box shape_box(const Shape& s) {
    Box b = Box::empty();
    for (const Point& p : s.outline) b = b.united(Box::of(p, p));
    return b;
}
void unite_dirty(std::map<Handle, Box>& m, Handle layer, const Box& b) {
    if (!layer.valid() || b.is_empty()) return;
    auto [it, inserted] = m.try_emplace(layer, b);
    if (!inserted) it->second = it->second.united(b);
}
// Conservative fallback half-dimension (100 um in nm dbu) for the dirty box of
// a via whose padstack handle does not resolve: we cannot know the real pad or
// drill extents, so over-approximate rather than emit a degenerate point box.
constexpr Coord kFallbackViaHalfDim = 100'000;
}  // namespace

CommitEvent Design::make_event_(const TxnRecord& r, bool reversed) const {
    CommitEvent e;
    e.name = r.name;
    auto contains = [](const std::vector<Handle>& v, Handle h) {
        return std::find(v.begin(), v.end(), h) != v.end();
    };
    // Collapse the NET effect of each handle across the whole transaction.
    // Pre-txn liveness comes from the first op's `before`; post-txn liveness
    // from the last op's `after`. Bucketing op-by-op would put a handle that
    // is, e.g., created and erased in the same txn into BOTH created and
    // deleted, handing subscribers stale handles.
    struct NetEffect { bool live_before, live_after; };
    std::vector<std::pair<Handle, NetEffect>> net;   // first-touch order
    std::unordered_map<Handle, size_t> net_idx;
    for (const JournalOp& op : r.ops) {
        auto [it, inserted] = net_idx.try_emplace(op.h, net.size());
        if (inserted)
            net.emplace_back(op.h, NetEffect{op.before.has_value(),
                                             op.after.has_value()});
        else
            net[it->second].second.live_after = op.after.has_value();
    }
    for (const auto& [h, eff] : net) {
        bool lb = eff.live_before, la = eff.live_after;
        if (reversed) std::swap(lb, la);   // undo swaps created/deleted roles
        if (!lb && la)      e.created.push_back(h);
        else if (lb && !la) e.deleted.push_back(h);
        else if (lb && la)  e.modified.push_back(h);
        // else: created+erased in the same txn — net-zero, no bucket (its
        // dirty boxes below are kept; redrawing the area is harmless).
    }
    for (const JournalOp& op : r.ops) {
        // dirty boxes: union geometry from both snapshots (covers moves)
        for (const std::optional<AnyObject>* snap : {&op.before, &op.after}) {
            if (!snap->has_value()) continue;
            const AnyObject& v = **snap;
            if (const Cline* c = std::get_if<Cline>(&v)) {
                unite_dirty(e.dirty_boxes, c->layer, cline_box(*c));
            } else if (const Shape* s = std::get_if<Shape>(&v)) {
                unite_dirty(e.dirty_boxes, s->layer, shape_box(*s));
            } else if (const Via* via = std::get_if<Via>(&v)) {
                Box b = Box::of(via->pos, via->pos);
                if (const Padstack* ps = padstacks_.get(via->padstack)) {
                    Coord dim = 0;
                    for (const auto& [layer, pad] : ps->pads)
                        dim = std::max({dim, pad.w, pad.h});
                    b = b.inflated(dim);                 // conservative: max pad dimension
                    for (const auto& [layer, pad] : ps->pads)
                        unite_dirty(e.dirty_boxes, layer, b);
                } else {
                    // Padstack handle is null or stale: pad/drill extents are
                    // unknowable (the drill lives on the padstack too), so
                    // inflate by a conservative fallback rather than emit a
                    // degenerate point box that no viewer/index would notice.
                    b = b.inflated(kFallbackViaHalfDim);
                    unite_dirty(e.dirty_boxes, via->from_layer, b);
                    unite_dirty(e.dirty_boxes, via->to_layer, b);
                }
            }
        }
    }
    for (const PropOp& op : r.prop_ops) {
        // Skip handles already bucketed by object ops, and net-zero handles
        // (in net_idx but no bucket): those are dead and must not be reported.
        if (net_idx.count(op.h)) continue;
        if (!contains(e.modified, op.h)) e.modified.push_back(op.h);
    }
    if (r.system_cset_change) {
        // Surface both CSet handles as modified so subscribers keyed on
        // Kind::CSet (e.g. ConstraintResolver) see the system-default switch.
        for (Handle h : {r.system_cset_change->first, r.system_cset_change->second}) {
            if (!h.valid() || net_idx.count(h)) continue;
            if (!contains(e.modified, h)) e.modified.push_back(h);
        }
    }
    return e;
}

void Design::notify_(const CommitEvent& e) {
    // Iterate a copy: a callback may unsubscribe (e.g. an engine being torn
    // down mid-notification) or subscribe without invalidating this loop.
    // Entries unsubscribed during notification are still invoked once from
    // the copy, but never again afterwards.
    auto snapshot = subscribers_;
    for (const auto& [id, cb] : snapshot) cb(e);
}

Design::SubscriptionId Design::subscribe(CommitCallback cb) {
    SubscriptionId id = next_subscription_id_++;
    subscribers_.emplace_back(id, std::move(cb));
    return id;
}

void Design::unsubscribe(SubscriptionId id) {
    subscribers_.erase(
        std::remove_if(subscribers_.begin(), subscribers_.end(),
                       [id](const auto& s) { return s.first == id; }),
        subscribers_.end());
}

void Design::undo() {
    if (undo_.empty()) return;
    TxnRecord r = std::move(undo_.back());
    undo_.pop_back();
    apply_inverse_(r);
    CommitEvent e = make_event_(r, /*reversed=*/true);
    redo_.push_back(std::move(r));
    notify_(e);
}

void Design::redo() {
    if (redo_.empty()) return;
    TxnRecord r = std::move(redo_.back());
    redo_.pop_back();
    apply_forward_(r);
    CommitEvent e = make_event_(r, /*reversed=*/false);
    undo_.push_back(std::move(r));
    notify_(e);
}

}  // namespace sdb
