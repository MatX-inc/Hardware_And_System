#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>
#include "sdb/geom/object_geometry.h"
#include "sdb/core/design.h"
#include <cmath>
using namespace sdb;
using Catch::Matchers::WithinAbs;

namespace {
Prim capsule(Point a, Point b, Coord w) {
    Prim p; p.kind = Prim::Kind::Capsule; p.a = a; p.b = b; p.width = w;
    return p;
}
Prim circle(Point c, Coord d) {
    Prim p; p.kind = Prim::Kind::Circle; p.center = c; p.a = p.b = c;
    p.diameter = d;
    return p;
}
Prim rect(Point c, Coord w, Coord h) {
    Prim p; p.kind = Prim::Kind::Rect; p.center = c; p.a = p.b = c;
    p.w = w; p.h = h;
    return p;
}
Prim arc(Point a, Point b, Point c, bool cw, Coord w) {
    Prim p; p.kind = Prim::Kind::Arc; p.a = a; p.b = b; p.center = c;
    p.cw = cw; p.width = w;
    return p;
}
Prim poly(std::vector<Point> pts) {
    Prim p; p.kind = Prim::Kind::Poly; p.poly = std::move(pts);
    p.a = p.b = p.center = p.poly.front();
    return p;
}
}  // namespace

TEST_CASE("cline bbox includes width inflation") {
    Design d("t");
    auto t = d.begin("s");
    Handle l = t.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle n = t.create_net({.name="N"});
    Cline c{.layer=l, .net=n};
    c.segs = {Segment::line({0,0},{1000,0}, 200)};
    Handle hc = t.create_cline(c);
    t.commit();
    auto boxes = object_bboxes(d, hc);          // map<layer Handle, Box>
    REQUIRE(boxes.at(l) == Box{{-100,-100},{1100,100}});
}
TEST_CASE("via expands pads on both layers; component pins expand per padstack") {
    Design d("t");
    auto t = d.begin("s");
    Handle l1 = t.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle l2 = t.create_layer({.name="L2", .type=LayerType::Conductor});
    Padstack ps{.name="V1"};
    ps.pads[l1] = {PadShape::Circle, 400, 400};
    ps.pads[l2] = {PadShape::Circle, 600, 600};
    Handle hps = t.create_padstack(ps);
    Handle n = t.create_net({.name="N"});
    Handle hv = t.create_via({.padstack=hps, .net=n, .pos={1000,1000},
                              .from_layer=l1, .to_layer=l2});
    Symbol sym{.name="S"}; sym.pins = {{"1", hps, {0,0}}};
    Handle hs = t.create_symbol(sym);
    Handle comp = t.create_component({.refdes="U1", .symbol=hs, .origin={500,0}});
    t.commit();
    auto vb = object_bboxes(d, hv);
    REQUIRE(vb.at(l1) == Box{{800,800},{1200,1200}});
    REQUIRE(vb.at(l2) == Box{{700,700},{1300,1300}});
    auto prims = object_primitives(d, comp, l1);
    REQUIRE(prims.size() == 1);
    REQUIRE(prims[0].kind == Prim::Kind::Circle);
    REQUIRE(prims[0].center == Point{500,0});
    REQUIRE(prims[0].diameter == 400);
    REQUIRE(prims[0].net == kNullHandle);   // pin "1" has no net assigned
    REQUIRE(prims[0].pin == "1");
}
TEST_CASE("empty shape outline contributes no primitives or bboxes") {
    Design d("t");
    auto t = d.begin("s");
    Handle l = t.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle n = t.create_net({.name="N"});
    Handle hs = t.create_shape({.layer=l, .net=n, .outline={}});
    t.commit();
    REQUIRE(object_primitives(d, hs, l).empty());
    REQUIRE(object_bboxes(d, hs).empty());
}
TEST_CASE("cline arc segment: Arc prim and full-extent bbox for sweep > 180") {
    Design d("t");
    auto t = d.begin("s");
    Handle l = t.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle n = t.create_net({.name="N"});
    Cline c{.layer=l, .net=n};
    // 270-degree CCW arc (1000,0) -> (0,-1000) around (0,0), width 200:
    // passes through (0,1000) and (-1000,0).
    c.segs = {Segment::arc({1000,0},{0,-1000},{0,0},/*cw=*/false, 200)};
    Handle hc = t.create_cline(c);
    t.commit();
    auto prims = object_primitives(d, hc, l);
    REQUIRE(prims.size() == 1);
    REQUIRE(prims[0].kind == Prim::Kind::Arc);
    REQUIRE(prims[0].center == Point{0,0});
    REQUIRE_FALSE(prims[0].cw);
    auto boxes = object_bboxes(d, hc);
    REQUIRE(boxes.at(l) == Box{{-1100,-1100},{1100,1100}});
}

// ---------------------------------------------------------------- prim_gap

TEST_CASE("prim_gap capsule x capsule") {
    // parallel horizontal capsules, centerlines 1000 apart, widths 200/400
    Prim c1 = capsule({0,0},{10'000,0}, 200);
    Prim c2 = capsule({0,1000},{10'000,1000}, 400);
    REQUIRE_THAT(prim_gap(c1, c2), WithinAbs(700.0, 1e-9));
}
TEST_CASE("prim_gap rect x capsule never overestimates the corner gap") {
    // Rect 1000x400 at origin: corners at (+-500, +-200). Thin capsule just
    // off the (500,200) corner; true gap = dist((600,300),(500,200)) = 100*sqrt(2).
    Prim r = rect({0,0}, 1000, 400);
    Prim c = capsule({600,300},{600,1000}, 0);
    double true_gap = 100.0 * std::sqrt(2.0);
    double g = prim_gap(r, c);
    REQUIRE(g <= true_gap + 1e-9);   // circumscribed capsule: no overestimate
    REQUIRE(g > 0.0);                // and not spuriously touching
    // A capsule touching the rect corner exactly must report gap 0.
    Prim touch = capsule({500,200},{500,1000}, 0);
    REQUIRE(prim_gap(r, touch) == 0.0);
}
TEST_CASE("prim_gap circle x capsule") {
    Prim ci = circle({0,1000}, 400);
    Prim c = capsule({-1000,0},{1000,0}, 200);
    REQUIRE_THAT(prim_gap(ci, c), WithinAbs(700.0, 1e-9));
}
TEST_CASE("prim_gap arc x capsule") {
    // quarter CCW arc radius 1000 around origin vs horizontal capsule at
    // y=2000: centerline distance 1000, widths 0/200 -> gap ~900
    Prim a = arc({1000,0},{0,1000},{0,0},/*cw=*/false, 0);
    Prim c = capsule({-2000,2000},{2000,2000}, 200);
    REQUIRE_THAT(prim_gap(a, c), WithinAbs(900.0, 2.5));
}
TEST_CASE("prim_gap poly x capsule crossing reports zero") {
    // capsule centerline crosses the square, both endpoints outside
    Prim p = poly({{0,0},{1000,0},{1000,1000},{0,1000}});
    Prim c = capsule({-500,500},{1500,500}, 0);
    REQUIRE(prim_gap(p, c) == 0.0);
    REQUIRE(prim_gap(c, p) == 0.0);  // symmetric dispatch
}
TEST_CASE("prim_gap poly x poly '+' cross reports zero") {
    // horizontal and vertical bars crossing; all vertices mutually outside
    Prim hbar = poly({{-300,-100},{300,-100},{300,100},{-300,100}});
    Prim vbar = poly({{-100,-300},{100,-300},{100,300},{-100,300}});
    REQUIRE(prim_gap(hbar, vbar) == 0.0);
}
