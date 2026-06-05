#pragma once
#include "sdb/core/arena.h"
#include "sdb/core/objects.h"
#include "sdb/constraints/cset.h"
#include <functional>
#include <optional>
#include <variant>
#include <unordered_map>
namespace sdb {
using PropValue = std::variant<int64_t, double, std::string, bool, Handle>;
class Design;

// Notification payload delivered to subscribers on every commit/undo/redo.
struct CommitEvent {
    std::string name;
    std::vector<Handle> created, modified, deleted;
    std::map<Handle, Box> dirty_boxes;   // conductor layer -> union bbox of touched geometry
};
using CommitCallback = std::function<void(const CommitEvent&)>;

// ------------------------------------------------------------------- journal
// One journal op = handle + optional before/after snapshots.
// create:  before=nullopt, after=value     undo: free          redo: resurrect
// modify:  before=value,  after=value      undo: write before  redo: write after
// erase:   before=value,  after=nullopt    undo: resurrect     redo: free
using AnyObject = std::variant<Layer, Padstack, Symbol, Component, Net, NetClass,
                               Cline, Via, Shape, CSet>;
struct JournalOp {
    Handle h;
    std::optional<AnyObject> before, after;
};
// Property changes are journaled separately and replayed alongside object ops.
// Erasing an object journals each of its properties as a PropOp with
// after=nullopt, so undo restores both the object and its property map.
struct PropOp {
    Handle h;
    std::string key;
    std::optional<PropValue> before, after;
};
struct TxnRecord {
    std::string name;
    std::vector<JournalOp> ops;
    std::vector<PropOp> prop_ops;
    // set_system_cset is journaled as a (before, after) pair of the
    // Design::system_cset_ handle rather than an object op: there is no
    // object being mutated, just which CSet is the system default. Replayed
    // by apply_forward_/apply_inverse_; surfaced to subscribers by listing
    // both handles as modified in the CommitEvent. Last write in a txn wins.
    std::optional<std::pair<Handle, Handle>> system_cset_change;
};

class Transaction {
public:
    // creation — each returns the new handle
    Handle create_layer(Layer);
    Handle create_padstack(Padstack);
    Handle create_symbol(Symbol);
    Handle create_component(Component);
    Handle create_net(Net);
    Handle create_net_class(NetClass);
    Handle create_cline(Cline);
    Handle create_via(Via);
    Handle create_shape(Shape);
    Handle create_cset(CSet);
    // mutation
    void assign_pin(Handle component, const std::string& pin, Handle net);
    void set_property(Handle target, const std::string& key, PropValue value);
    void update_cline(Handle, Cline);        // whole-value replace (snapshot undo)
    void update_via(Handle, Via);
    void update_shape(Handle, Shape);
    void update_cset(Handle, CSet);
    void set_system_cset(Handle);            // journaled via TxnRecord::system_cset_change
    void erase(Handle);                       // any geometry/object kind
    void commit();
    void abort();
    ~Transaction();                            // aborts if not committed
    // Movable (needed to hand a Transaction across the language boundary);
    // never copyable — two owners of one open journal would double-abort.
    Transaction(Transaction&& o) noexcept
        : d_(o.d_), open_(o.open_), rec_(std::move(o.rec_)) { o.open_ = false; }
    Transaction& operator=(Transaction&&) = delete;
    Transaction(const Transaction&)            = delete;
    Transaction& operator=(const Transaction&) = delete;
private:
    friend class Design;
    explicit Transaction(Design& d, std::string name);
    Design* d_;
    bool open_ = true;
    TxnRecord rec_;
};

class Design {
public:
    explicit Design(std::string name);
    // Transaction and (later) engines hold Design* — Design must not move or be copied.
    Design(const Design&)            = delete;
    Design& operator=(const Design&) = delete;
    Design(Design&&)                 = delete;
    Design& operator=(Design&&)      = delete;
    Transaction begin(std::string txn_name);
    // typed read accessors (nullptr if stale/wrong kind)
    const Layer* layer(Handle) const;     const Padstack* padstack(Handle) const;
    const Symbol* symbol(Handle) const;   const Component* component(Handle) const;
    const Net* net(Handle) const;         const NetClass* net_class(Handle) const;
    const Cline* cline(Handle) const;
    const Via* via(Handle) const;         const Shape* shape(Handle) const;
    const CSet* cset(Handle) const;
    Handle system_cset() const { return system_cset_; }
    Handle layer_by_name(const std::string&) const;
    Handle net_by_name(const std::string&) const;
    std::vector<Handle> conductor_layers() const;    // in stackup order
    // Visit every live handle of one kind (bind-friendly listing for engines).
    void for_each_object(Kind, const std::function<void(Handle)>&) const;
    Point pin_position(Handle component, const std::string& pin) const;
    std::optional<double>      get_property_double(Handle, const std::string&) const;
    std::optional<std::string> get_property_string(Handle, const std::string&) const;
    std::optional<int64_t>     get_property_int(Handle, const std::string&) const;
    std::optional<bool>        get_property_bool(Handle, const std::string&) const;
    std::optional<Handle>      get_property_handle(Handle, const std::string&) const;
    // Generic getter: whatever type was stored, or nullopt if absent.
    std::optional<PropValue>   get_property(Handle, const std::string&) const;
    // journal / notifications
    // subscribe returns an id that the subscriber must pass to unsubscribe
    // before it is destroyed (engines do this from their destructors). notify_
    // iterates a copy of the subscriber list, so unsubscribing (or
    // subscribing) from within a callback is safe.
    using SubscriptionId = size_t;
    SubscriptionId subscribe(CommitCallback cb);
    void unsubscribe(SubscriptionId id);
    void undo();
    void redo();
    size_t undo_depth() const { return undo_.size(); }
    size_t redo_depth() const { return redo_.size(); }
private:
    friend class Transaction;
    // journal machinery (design.cpp)
    std::optional<AnyObject> snapshot_(Handle) const;
    void write_(Handle, const AnyObject&);       // overwrite live object
    void resurrect_(Handle, const AnyObject&);   // restore freed object
    void free_(Handle);                          // free arena slot (props untouched)
    void apply_forward_(const TxnRecord&);       // redo direction
    void apply_inverse_(const TxnRecord&);       // undo/abort direction (reverse order)
    CommitEvent make_event_(const TxnRecord&, bool reversed) const;
    void notify_(const CommitEvent&);

    std::string name_;
    Arena<Layer> layers_{Kind::Layer};
    Arena<Padstack> padstacks_{Kind::Padstack};
    Arena<Symbol> symbols_{Kind::Symbol};
    Arena<Component> components_{Kind::Component};
    Arena<Net> nets_{Kind::Net};
    Arena<NetClass> net_classes_{Kind::NetClass};
    Arena<Cline> clines_{Kind::Cline};
    Arena<Via> vias_{Kind::Via};
    Arena<Shape> shapes_{Kind::Shape};
    Arena<CSet> csets_{Kind::CSet};
    Handle system_cset_;                   // design-wide default constraint set
    std::unordered_map<Handle, std::map<std::string, PropValue>> props_;
    int next_layer_order_ = 0;
    std::vector<TxnRecord> undo_, redo_;
    std::vector<std::pair<SubscriptionId, CommitCallback>> subscribers_;
    SubscriptionId next_subscription_id_ = 0;
};
}
