#include <catch2/catch_test_macros.hpp>
#include "sdb/drc/drc.h"
using namespace sdb;

namespace {
struct Fix {
    Design d{"t"};
    SpatialIndex idx{d};
    ConstraintResolver res{d};
    Drc drc{d, idx, res};
    Handle l1, n1, n2;
    Fix() {
        auto t = d.begin("s");
        l1 = t.create_layer({.name="L1", .type=LayerType::Conductor});
        CSet sys; sys.name="SYS";
        sys.spacing.set(ObjKind::Line, ObjKind::Line, 500);
        sys.spacing.set(ObjKind::Line, ObjKind::Via, 400);
        Handle hs = t.create_cset(sys);
        t.set_system_cset(hs);
        n1 = t.create_net({.name="A"});
        n2 = t.create_net({.name="B"});
        t.commit();
    }
    Handle cline(Handle net, Coord y, Coord w=100) {
        auto t = d.begin("c");
        Cline c{.layer=l1, .net=net};
        c.segs = {Segment::line({0,y},{10'000,y}, w)};
        Handle h = t.create_cline(c); t.commit(); return h;
    }
};
}
TEST_CASE_METHOD(Fix, "violation when gap below required") {
    Handle a = cline(n1, 0);          // capsule edge at y=50
    Handle b = cline(n2, 500);        // capsule edge at y=450 -> gap 400 < 500
    auto v = drc.run_full();
    REQUIRE(v.size() == 1);
    REQUIRE(v[0].a == std::min(a,b,[](auto x,auto y){return x.index<y.index;}));
    REQUIRE(v[0].measured == 400);
    REQUIRE(v[0].required == 500);
    REQUIRE(v[0].layer == l1);
}
TEST_CASE_METHOD(Fix, "no violation when gap sufficient, pair deduped, same net skipped") {
    cline(n1, 0);
    cline(n2, 700);                   // gap 600 >= 500 -> clean
    REQUIRE(drc.run_full().empty());
    // Same-net pair, gap 100 -> still clean in v1. Placed at y>=1400 so the
    // first line of the pair is >= 600 clear of the n2 line at y=700
    // (y=1200 would sit only 400 from it -- a real cross-net violation).
    cline(n1, 1400); cline(n1, 1600);
    REQUIRE(drc.run_full().empty());
}
TEST_CASE_METHOD(Fix, "region mode only checks the window") {
    cline(n1, 0); cline(n2, 450);             // violating pair near y~0
    cline(n1, 100'000); cline(n2, 100'450);   // violating pair near y~100k
    auto v = drc.run_region(Box{{-1000,-1000},{20'000,2000}});
    REQUIRE(v.size() == 1);
}

TEST_CASE_METHOD(Fix, "via to line spacing violation") {
    // n1 cline at y=0, width=100: capsule half-width=50, nearest edge at y=50.
    // Padstack: circle pad diameter=300 (radius=150) on l1.
    // Via (net n2) at (5000, 460): pad edge at y = 460-150 = 310.
    // Gap = 310 - 50 = 260 < 400 (Line-Via rule) → one violation.
    auto t = d.begin("setup");
    Padstack ps; ps.name = "VIA300";
    ps.pads[l1] = PadDef{PadShape::Circle, 300, 0};
    Handle hps = t.create_padstack(ps);
    Handle hvia = t.create_via(Via{.padstack=hps, .net=n2, .pos={5000,460}});
    t.commit();

    Handle hcl = cline(n1, 0);   // capsule edge at y=50

    auto v = drc.run_full();
    REQUIRE(v.size() == 1);
    REQUIRE(v[0].measured == 260.0);
    REQUIRE(v[0].required == 400);
    REQUIRE(v[0].layer == l1);

    // Via placed far enough away: no violation.
    auto t2 = d.begin("move");
    t2.update_via(hvia, Via{.padstack=hps, .net=n2, .pos={5000,1000}});
    t2.commit();
    // pad edge at y = 1000-150 = 850, gap = 850-50 = 800 >= 400 → clean
    REQUIRE(drc.run_full().empty());
}

TEST_CASE_METHOD(Fix, "component pad checked including unassigned pin") {
    // Add Line-Pad=400 to the system CSet (fixture only sets Line-Line and
    // Line-Via).
    {
        auto t = d.begin("add-line-pad");
        Handle hs = d.system_cset();
        CSet sys = *d.cset(hs);
        sys.spacing.set(ObjKind::Line, ObjKind::Pad, 400);
        t.update_cset(hs, sys);
        t.commit();
    }

    // Padstack: circle diameter=300 on l1.
    Handle hps;
    {
        auto t = d.begin("padstack");
        Padstack ps; ps.name = "PAD300";
        ps.pads[l1] = PadDef{PadShape::Circle, 300, 0};
        hps = t.create_padstack(ps);
        t.commit();
    }

    // Symbol with two pins (offsets {0,0} and {1000,0}).
    Handle hsym;
    {
        auto t = d.begin("symbol");
        Symbol sym; sym.name = "SYM";
        sym.pins.push_back(SymbolPin{.number="1", .padstack=hps, .offset={0,0}});
        sym.pins.push_back(SymbolPin{.number="2", .padstack=hps, .offset={1000,0}});
        hsym = t.create_symbol(sym);
        t.commit();
    }

    // Component at (0,460) with NO pin-net assignments (null-net path).
    // pin1 pad center (0,460), pin2 pad center (1000,460).
    // Both pads: edge at y = 460-150 = 310; capsule (cline y=0 w=100) edge at y=50.
    // Gap = 310-50 = 260 < 400 → 1 violation (one component vs one cline pair).
    {
        auto t = d.begin("comp");
        t.create_component(Component{.refdes="U1", .symbol=hsym, .origin={0,460}});
        t.commit();
    }

    Handle hcl = cline(n1, 0);   // capsule edge at y=50

    auto v = drc.run_full();
    REQUIRE(v.size() == 1);
    REQUIRE(v[0].measured == 260.0);
    REQUIRE(v[0].required == 400);
    REQUIRE(v[0].layer == l1);
}
