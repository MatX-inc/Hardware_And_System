#include <catch2/catch_test_macros.hpp>
#include "sdb/index/spatial_index.h"
#include "sdb/core/design.h"
using namespace sdb;

static Cline mk(Handle l, Handle n, Coord x0, Coord y0) {
    Cline c{.layer=l, .net=n};
    c.segs = {Segment::line({x0,y0},{x0+1000,y0}, 100)};
    return c;
}
TEST_CASE("index tracks commits, undo, erase") {
    Design d("t");
    SpatialIndex idx(d);            // subscribes on construction
    auto t = d.begin("s");
    Handle l = t.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle n = t.create_net({.name="N"});
    t.commit();
    auto t2 = d.begin("add");
    Handle c1 = t2.create_cline(mk(l,n,0,0));
    Handle c2 = t2.create_cline(mk(l,n,10'000,0));
    t2.commit();
    auto hits = idx.query_box(l, Box{{-500,-500},{2000,500}});
    REQUIRE(hits == std::vector<Handle>{c1});
    REQUIRE(idx.query_box(l, Box{{-1,-1},{20'000,1}}).size() == 2);
    d.undo();
    REQUIRE(idx.query_box(l, Box{{-1,-1},{20'000,1}}).empty());
    d.redo();
    auto t3 = d.begin("del"); t3.erase(c2); t3.commit();
    REQUIRE(idx.query_box(l, Box{{-1,-1},{20'000,1}}) == std::vector<Handle>{c1});
}
TEST_CASE("query_clearance inflates the search box") {
    Design d("t");
    SpatialIndex idx(d);
    auto t = d.begin("s");
    Handle l = t.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle n = t.create_net({.name="N"});
    Handle c1 = t.create_cline(mk(l,n,0,0));
    t.commit();
    // object bbox is {{-50,-50},{1050,50}}; probe box 200 away with 300 clearance
    REQUIRE(idx.query_clearance(l, Box{{1250,0},{1300,10}}, 300).size() == 1);
    REQUIRE(idx.query_clearance(l, Box{{1250,0},{1300,10}}, 100).empty());
}
TEST_CASE("net index lists handles per net") {
    Design d("t");
    SpatialIndex idx(d);
    auto t = d.begin("s");
    Handle l = t.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle n1 = t.create_net({.name="A"});
    Handle n2 = t.create_net({.name="B"});
    Handle c1 = t.create_cline(mk(l,n1,0,0));
    t.create_cline(mk(l,n2,5000,0));
    t.commit();
    REQUIRE(idx.net_objects(n1) == std::vector<Handle>{c1});
}
TEST_CASE("index tracks update_cline") {
    Design d("t");
    SpatialIndex idx(d);
    auto t = d.begin("s");
    Handle l = t.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle n = t.create_net({.name="N"});
    Handle c1 = t.create_cline(mk(l,n,0,0));   // bbox ~{-50..1050}
    t.commit();
    auto t2 = d.begin("move");
    t2.update_cline(c1, mk(l,n,50'000,0));
    t2.commit();
    REQUIRE(idx.query_box(l, Box{{-500,-500},{2000,500}}).empty());        // old spot
    REQUIRE(idx.query_box(l, Box{{49'000,-500},{52'000,500}})
            == std::vector<Handle>{c1});                                    // new spot
}
TEST_CASE("index survives engine destruction") {
    // Exercises unsubscribe: a destroyed SpatialIndex must not be notified
    // by later commits (dangling-callback regression).
    Design d("t");
    Handle l, n;
    {
        auto t = d.begin("s");
        l = t.create_layer({.name="L1", .type=LayerType::Conductor});
        n = t.create_net({.name="N"});
        t.commit();
    }
    {
        SpatialIndex idx(d);   // subscribes...
    }                          // ...and unsubscribes here
    auto t = d.begin("after");
    t.create_cline(mk(l,n,0,0));
    t.commit();                // must not invoke the destroyed index's callback
    SUCCEED("commit after index destruction did not crash");
}
