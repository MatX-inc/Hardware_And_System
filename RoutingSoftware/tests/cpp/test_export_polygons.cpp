#include <catch2/catch_test_macros.hpp>
#include "sdb/geom/export_polygons.h"
#include "sdb/geom/kernel.h"
#include "sdb/core/design.h"
using namespace sdb;

TEST_CASE("design polygons per layer cover all conductor objects") {
    Design d("t");
    auto t = d.begin("s");
    Handle l1 = t.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle n = t.create_net({.name="N"});
    Cline c{.layer=l1, .net=n};
    c.segs = {Segment::line({0,0},{10'000,0}, 2'000)};
    t.create_cline(c);
    Shape s{.layer=l1, .net=n};
    s.outline = {{20'000,0},{30'000,0},{30'000,10'000},{20'000,10'000}};
    t.create_shape(s);
    t.commit();
    auto polys = export_polygons(d, l1, /*tol=*/10.0);
    REQUIRE(polys.size() == 2);
    // capsule poly bbox
    REQUIRE(geom::poly_bbox(polys[0]) == Box{{-1'000,-1'000},{11'000,1'000}});
    // shape passes through unchanged
    REQUIRE(polys[1] == s.outline);
}
