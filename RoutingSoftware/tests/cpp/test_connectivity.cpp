#include <catch2/catch_test_macros.hpp>
#include "sdb/connectivity/connectivity.h"
using namespace sdb;

namespace {
struct Fix {
    Design d{"t"};
    SpatialIndex idx{d};
    Connectivity conn{d, idx};
    Handle l1, l2, net;
    Fix() {
        auto t = d.begin("s");
        l1 = t.create_layer({.name="L1", .type=LayerType::Conductor});
        l2 = t.create_layer({.name="L2", .type=LayerType::Conductor});
        net = t.create_net({.name="N"});
        t.commit();
    }
    Handle cline(Handle lay, Coord x0, Coord y0, Coord x1, Coord y1, Coord w=100) {
        auto t = d.begin("c");
        Cline c{.layer=lay, .net=net};
        c.segs = {Segment::line({x0,y0},{x1,y1}, w)};
        Handle h = t.create_cline(c);
        t.commit();
        return h;
    }
};
}
TEST_CASE_METHOD(Fix, "touching same-layer clines form one cluster") {
    cline(l1, 0,0, 1000,0);
    cline(l1, 1000,0, 1000,1000);          // shares endpoint -> capsules overlap
    cline(l1, 5000,5000, 6000,5000);       // isolated
    auto st = conn.net_status(net);
    REQUIRE(st.cluster_count == 2);
    REQUIRE(st.open_pairs == 1);           // ratsnest MST edges = clusters-1
}
TEST_CASE_METHOD(Fix, "via joins layers") {
    cline(l1, 0,0, 1000,0);
    cline(l2, 1000,0, 2000,0);
    REQUIRE(conn.net_status(net).cluster_count == 2);   // different layers don't touch
    auto t = d.begin("v");
    Padstack ps{.name="V"};
    ps.pads[l1] = {PadShape::Circle, 300, 300};
    ps.pads[l2] = {PadShape::Circle, 300, 300};
    Handle hps = t.create_padstack(ps);
    t.create_via({.padstack=hps, .net=net, .pos={1000,0}, .from_layer=l1, .to_layer=l2});
    t.commit();
    REQUIRE(conn.net_status(net).cluster_count == 1);
}
TEST_CASE_METHOD(Fix, "ratsnest endpoints are nearest cluster representatives") {
    Handle a = cline(l1, 0,0, 1000,0);
    Handle b = cline(l1, 10'000,0, 11'000,0);
    auto rats = conn.ratsnest(net);
    REQUIRE(rats.size() == 1);
    // edge should connect the two nearest points of the clusters (1000,0)-(10000,0)
    REQUIRE(rats[0].a == Point{1000,0});
    REQUIRE(rats[0].b == Point{10'000,0});
    (void)a; (void)b;
}
TEST_CASE_METHOD(Fix, "via barrel connects intermediate layer without a pad") {
    // Add a third conductor layer inside this test.
    Handle l3;
    {
        auto t = d.begin("s");
        l3 = t.create_layer({.name="L3", .type=LayerType::Conductor});
        t.commit();
    }
    // Padstack with pads only on l1 and l3 — l2 will get a synthetic barrel.
    Handle hps;
    {
        auto t = d.begin("s");
        Padstack ps{.name="V3"};
        ps.drill = 200;
        ps.pads[l1] = {PadShape::Circle, 300, 300};
        ps.pads[l3] = {PadShape::Circle, 300, 300};
        hps = t.create_padstack(ps);
        t.commit();
    }
    // Cline on l2 crossing the via position (500,0)->(1500,0), width 100.
    cline(l2, 500,0, 1500,0);
    // Cline on l1 approaching the via pad: (0,0)->(900,0), width 100.
    // Gap to pad circle (center (1000,0), r=150):
    //   segment end at (900,0) + half-width 50 reaches (950,0);
    //   pad edge at (850,0); gap = 850 - 950 = -100 → overlapping → touch.
    cline(l1, 0,0, 900,0);
    // Before the via: clines on l1 and l2 are on different layers → 2 clusters.
    REQUIRE(conn.net_status(net).cluster_count == 2);
    // Place the via at (1000,0) spanning l1..l3.
    {
        auto t = d.begin("v");
        t.create_via({.padstack=hps, .net=net, .pos={1000,0},
                      .from_layer=l1, .to_layer=l3});
        t.commit();
    }
    // Via pad on l1 merges l1 cline; synthetic barrel on l2 merges l2 cline.
    REQUIRE(conn.net_status(net).cluster_count == 1);
}
TEST_CASE_METHOD(Fix, "incremental update on undo") {
    cline(l1, 0,0, 1000,0);
    cline(l1, 1000,0, 2000,0);
    REQUIRE(conn.net_status(net).cluster_count == 1);
    d.undo();
    REQUIRE(conn.net_status(net).cluster_count == 1);   // one cline left, one cluster
    d.undo();
    REQUIRE(conn.net_status(net).cluster_count == 0);
}
