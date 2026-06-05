#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>
#include "sdb/geom/kernel.h"
#include <cmath>
using namespace sdb;
using Catch::Matchers::WithinAbs;

TEST_CASE("point-segment distance") {
    REQUIRE_THAT(geom::dist_point_segment({5,5},{0,0},{10,0}), WithinAbs(5.0, 1e-9));
    REQUIRE_THAT(geom::dist_point_segment({-3,4},{0,0},{10,0}), WithinAbs(5.0, 1e-9));
}
TEST_CASE("segment-segment distance, parallel and crossing") {
    REQUIRE_THAT(geom::dist_segment_segment({0,0},{10,0},{0,7},{10,7}), WithinAbs(7.0, 1e-9));
    REQUIRE_THAT(geom::dist_segment_segment({0,0},{10,10},{0,10},{10,0}), WithinAbs(0.0, 1e-9));
}
TEST_CASE("capsule gap subtracts half-widths") {
    // two horizontal traces, centerlines 1000 apart, widths 200 and 400
    double gap = geom::gap_capsule_capsule({0,0},{10'000,0},200, {0,1000},{10'000,1000},400);
    REQUIRE_THAT(gap, WithinAbs(1000.0 - 100.0 - 200.0, 1e-9));
    // overlapping capsules clamp to 0
    REQUIRE(geom::gap_capsule_capsule({0,0},{10,0},2000, {0,5},{10,5},2000) == 0.0);
}
TEST_CASE("arc sampled within sag tolerance") {
    // quarter arc ccw from (1000,0) to (0,1000) center (0,0), radius 1000, tol 1
    auto pts = geom::sample_arc({1000,0},{0,1000},{0,0},/*cw=*/false,/*tol=*/1.0);
    REQUIRE(pts.front() == Point{1000,0});
    REQUIRE(pts.back() == Point{0,1000});
    REQUIRE(pts.size() >= 3);
    for (auto& p : pts) {
        double r = std::hypot(double(p.x), double(p.y));
        REQUIRE_THAT(r, WithinAbs(1000.0, 1.5));   // tol + rounding
    }
}
TEST_CASE("arc gap via sampling is conservative-correct") {
    // straight seg y=2000 above the unit-ish arc; min gap = 2000-1000 = 1000, widths 0
    double gap = geom::gap_segment_arc({-2000,2000},{2000,2000},0,
                                       {1000,0},{0,1000},{0,0},false,0);
    REQUIRE_THAT(gap, WithinAbs(1000.0, 2.0));
}
TEST_CASE("gap_arc_arc concentric quarter arcs") {
    // two concentric CCW quarter arcs, center (0,0), radii 1000 and 3000,
    // zero widths -> true gap 2000 (sampled gap minus 2*kArcDrcTol bound)
    double gap = geom::gap_arc_arc({1000,0},{0,1000},{0,0},false,0,
                                   {3000,0},{0,3000},{0,0},false,0);
    REQUIRE_THAT(gap, WithinAbs(2000.0, 4.0));
    // touching/overlapping arcs clamp to 0: radii 1000 and 1001, widths 10
    double gap0 = geom::gap_arc_arc({1000,0},{0,1000},{0,0},false,10,
                                    {1001,0},{0,1001},{0,0},false,10);
    REQUIRE(gap0 == 0.0);
}
TEST_CASE("sample_arc full circle when endpoints equal") {
    auto pts = geom::sample_arc({1000,0},{1000,0},{0,0},/*cw=*/false,/*tol=*/1.0);
    REQUIRE(pts.front() == Point{1000,0});
    REQUIRE(pts.back() == Point{1000,0});
    REQUIRE(pts.size() > 8);
    for (auto& p : pts) {
        double r = std::hypot(double(p.x), double(p.y));
        REQUIRE_THAT(r, WithinAbs(1000.0, 1.5));
    }
}
TEST_CASE("sample_arc cw quarter") {
    auto pts = geom::sample_arc({0,1000},{1000,0},{0,0},/*cw=*/true,/*tol=*/1.0);
    REQUIRE(pts.front() == Point{0,1000});
    REQUIRE(pts.back() == Point{1000,0});
    REQUIRE(pts.size() >= 3);
    for (auto& p : pts) {
        double r = std::hypot(double(p.x), double(p.y));
        REQUIRE_THAT(r, WithinAbs(1000.0, 1.5));
    }
}
TEST_CASE("polygonize capsule produces closed outline containing endpoints") {
    auto poly = geom::polygonize_capsule({0,0},{10'000,0}, 2'000, /*tol=*/10.0);
    REQUIRE(poly.size() >= 8);
    Box bb = geom::poly_bbox(poly);
    REQUIRE(bb == Box{{-1'000,-1'000},{11'000,1'000}});
}
TEST_CASE("point in polygon") {
    std::vector<Point> sq = {{0,0},{10,0},{10,10},{0,10}};
    REQUIRE(geom::point_in_poly({5,5}, sq));
    REQUIRE_FALSE(geom::point_in_poly({15,5}, sq));
}
TEST_CASE("arc_bbox covers axis extrema for sweeps > 180 degrees") {
    // 270-degree CCW arc from (1000,0) to (0,-1000) around (0,0): passes
    // through (0,1000) and (-1000,0), which are far outside the endpoint hull.
    Box bb = geom::arc_bbox({1000,0},{0,-1000},{0,0},/*cw=*/false);
    REQUIRE(bb == Box{{-1000,-1000},{1000,1000}});
    // Same endpoints traversed CW is the complementary 90-degree arc in the
    // fourth quadrant: no axis extreme strictly inside, bbox is endpoint hull.
    Box bb_cw = geom::arc_bbox({1000,0},{0,-1000},{0,0},/*cw=*/true);
    REQUIRE(bb_cw == Box{{0,-1000},{1000,0}});
    // Quarter CCW arc (1000,0) -> (0,1000): bbox is exactly the quadrant.
    Box q = geom::arc_bbox({1000,0},{0,1000},{0,0},/*cw=*/false);
    REQUIRE(q == Box{{0,0},{1000,1000}});
}
