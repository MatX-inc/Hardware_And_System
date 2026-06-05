#include <catch2/catch_test_macros.hpp>
#include "sdb/geom/types.h"
using namespace sdb;

TEST_CASE("Box expand and union") {
    Box b{{0,0},{10,10}};
    REQUIRE(b.inflated(5) == Box{{-5,-5},{15,15}});
    REQUIRE(b.united(Box{{20,20},{30,30}}) == Box{{0,0},{30,30}});
    REQUIRE(b.intersects(Box{{10,10},{20,20}}));      // touching counts
    REQUIRE_FALSE(b.intersects(Box{{11,11},{20,20}}));
}
TEST_CASE("Box from points normalizes order") {
    REQUIRE(Box::of(Point{10,2}, Point{3,8}) == Box{{3,2},{10,8}});
}
TEST_CASE("empty Box unions as identity") {
    REQUIRE(Box::empty().united(Box{{1,2},{3,4}}) == Box{{1,2},{3,4}});
    REQUIRE(Box::empty().is_empty());
}
