#include <catch2/catch_test_macros.hpp>
#include "sdb/core/design.h"
using namespace sdb;

static std::pair<Handle,Handle> setup(Design& d) {  // returns (layer, net)
    auto t = d.begin("setup");
    Handle l = t.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle n = t.create_net({.name="N1"});
    t.commit();
    return {l, n};
}
static Cline mk_cline(Handle l, Handle n, Coord x0=0) {
    Cline c{.layer=l, .net=n};
    c.segs = {Segment::line({x0,0},{x0+1'000'000,0}, 20'000)};
    return c;
}

TEST_CASE("undo reverses a whole transaction; redo replays it") {
    Design d("t"); auto [l, n] = setup(d);
    auto t = d.begin("add two clines");
    Handle c1 = t.create_cline(mk_cline(l,n,0));
    Handle c2 = t.create_cline(mk_cline(l,n,2'000'000));
    t.commit();
    REQUIRE(d.cline(c1) != nullptr);
    d.undo();
    REQUIRE(d.cline(c1) == nullptr);
    REQUIRE(d.cline(c2) == nullptr);
    d.redo();
    REQUIRE(d.cline(c1) != nullptr);      // same handles after redo
    REQUIRE(d.cline(c2)->segs[0].a.x == 2'000'000);
}
TEST_CASE("undo restores modified and erased objects") {
    Design d("t"); auto [l, n] = setup(d);
    auto t1 = d.begin("add"); Handle c = t1.create_cline(mk_cline(l,n)); t1.commit();
    auto t2 = d.begin("widen");
    Cline mod = *d.cline(c); mod.segs[0].width = 50'000;
    t2.update_cline(c, mod); t2.commit();
    auto t3 = d.begin("del"); t3.erase(c); t3.commit();
    REQUIRE(d.cline(c) == nullptr);
    d.undo();  REQUIRE(d.cline(c)->segs[0].width == 50'000);
    d.undo();  REQUIRE(d.cline(c)->segs[0].width == 20'000);
}
TEST_CASE("abort leaves no trace") {
    Design d("t"); auto [l, n] = setup(d);
    {
        auto t = d.begin("oops");
        t.create_cline(mk_cline(l,n));
    }   // destructor aborts
    bool any = false;
    // undo stack must contain only the setup txn
    REQUIRE(d.undo_depth() == 1);
    (void)any;
}
TEST_CASE("new transaction clears redo stack") {
    Design d("t"); auto [l, n] = setup(d);
    auto t = d.begin("a"); t.create_cline(mk_cline(l,n)); t.commit();
    d.undo();
    auto t2 = d.begin("b"); t2.create_cline(mk_cline(l,n,5'000'000)); t2.commit();
    REQUIRE(d.redo_depth() == 0);
}
TEST_CASE("commit notifies subscribers with touched handles and dirty boxes") {
    Design d("t"); auto [l, n] = setup(d);
    CommitEvent seen;
    d.subscribe([&](const CommitEvent& e){ seen = e; });
    auto t = d.begin("add"); Handle c = t.create_cline(mk_cline(l,n)); t.commit();
    REQUIRE(seen.name == "add");
    REQUIRE(seen.created == std::vector<Handle>{c});
    // dirty box on layer l covers the cline including width/2 inflation
    REQUIRE(seen.dirty_boxes.at(l).intersects(Box{{0,-10'000},{1'000'000,10'000}}));
    d.undo();   // undo/redo notify through the same channel
    REQUIRE(seen.deleted == std::vector<Handle>{c});
}
TEST_CASE("create then erase in same txn is net-zero") {
    Design d("t"); auto [l, n] = setup(d);
    size_t base_depth = d.undo_depth();
    CommitEvent seen;
    d.subscribe([&](const CommitEvent& e){ seen = e; });
    auto t = d.begin("flash");
    Handle h = t.create_cline(mk_cline(l,n));
    t.erase(h);
    t.commit();
    REQUIRE(seen.created.empty());
    REQUIRE(seen.deleted.empty());
    REQUIRE(seen.modified.empty());
    REQUIRE(d.cline(h) == nullptr);
    d.undo();                              // net-zero ops replay to nothing
    REQUIRE(d.cline(h) == nullptr);
    REQUIRE(d.undo_depth() == base_depth);
    d.redo();
    REQUIRE(d.cline(h) == nullptr);
}
TEST_CASE("create then update in same txn reports created only") {
    Design d("t"); auto [l, n] = setup(d);
    CommitEvent seen;
    d.subscribe([&](const CommitEvent& e){ seen = e; });
    auto t = d.begin("add+widen");
    Handle h = t.create_cline(mk_cline(l,n));
    Cline mod = mk_cline(l,n); mod.segs[0].width = 50'000;
    t.update_cline(h, mod);
    t.commit();
    REQUIRE(seen.created == std::vector<Handle>{h});
    REQUIRE(seen.modified.empty());
    REQUIRE(seen.deleted.empty());
    d.undo();
    REQUIRE(d.cline(h) == nullptr);
    d.redo();
    REQUIRE(d.cline(h) != nullptr);
    REQUIRE(d.cline(h)->segs[0].width == 50'000);   // redo keeps the update
}
TEST_CASE("modify then erase in same txn reports deleted only") {
    Design d("t"); auto [l, n] = setup(d);
    auto t1 = d.begin("add"); Handle c = t1.create_cline(mk_cline(l,n)); t1.commit();
    CommitEvent seen;
    d.subscribe([&](const CommitEvent& e){ seen = e; });
    auto t2 = d.begin("widen+del");
    Cline mod = *d.cline(c); mod.segs[0].width = 50'000;
    t2.update_cline(c, mod);
    t2.erase(c);
    t2.commit();
    REQUIRE(seen.deleted == std::vector<Handle>{c});
    REQUIRE(seen.modified.empty());
    REQUIRE(seen.created.empty());
    d.undo();
    REQUIRE(d.cline(c) != nullptr);
    REQUIRE(d.cline(c)->segs[0].width == 20'000);   // original pre-txn value
}
TEST_CASE("double modify in one txn") {
    Design d("t"); auto [l, n] = setup(d);
    auto t1 = d.begin("add"); Handle c = t1.create_cline(mk_cline(l,n)); t1.commit();
    CommitEvent seen;
    d.subscribe([&](const CommitEvent& e){ seen = e; });
    auto t2 = d.begin("widen twice");
    Cline m1 = *d.cline(c); m1.segs[0].width = 30'000; t2.update_cline(c, m1);
    Cline m2 = *d.cline(c); m2.segs[0].width = 40'000; t2.update_cline(c, m2);
    t2.commit();
    REQUIRE(seen.modified == std::vector<Handle>{c});   // reported exactly once
    REQUIRE(seen.created.empty());
    REQUIRE(seen.deleted.empty());
    d.undo();
    REQUIRE(d.cline(c)->segs[0].width == 20'000);
    d.redo();
    REQUIRE(d.cline(c)->segs[0].width == 40'000);
}
TEST_CASE("undo past slot reuse keeps stale handles dead") {
    Design d("t"); auto [l, n] = setup(d);
    auto t1 = d.begin("add A");  Handle A = t1.create_cline(mk_cline(l,n));            t1.commit();
    auto t2 = d.begin("del A");  t2.erase(A);                                          t2.commit();
    auto t3 = d.begin("add B");  Handle B = t3.create_cline(mk_cline(l,n,2'000'000));  t3.commit();
    REQUIRE(B.index == A.index);            // slot reused
    d.undo();                               // undo t3: frees B
    d.undo();                               // undo t2: resurrects A (slot gen rolls back)
    REQUIRE(d.cline(A) != nullptr);
    REQUIRE(d.cline(B) == nullptr);
    // Redo stack is implicitly cleared by the next commit; re-mint in the slot.
    auto t4 = d.begin("del A again"); t4.erase(A); t4.commit();
    auto t5 = d.begin("add C");  Handle C = t5.create_cline(mk_cline(l,n,4'000'000));  t5.commit();
    REQUIRE(d.cline(C) != nullptr);
    REQUIRE(B != C);                        // generation must differ
    REQUIRE(d.cline(B) == nullptr);         // stale B must NOT dereference as C
}
