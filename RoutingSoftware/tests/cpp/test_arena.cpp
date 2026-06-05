#include <catch2/catch_test_macros.hpp>
#include "sdb/core/arena.h"
#include <string>
using namespace sdb;

TEST_CASE("alloc/get round trip") {
    Arena<std::string> a{Kind::Net};
    Handle h = a.alloc("hello");
    REQUIRE(h.kind == Kind::Net);
    REQUIRE(*a.get(h) == "hello");
    REQUIRE(a.size() == 1);
}
TEST_CASE("stale handle after free returns nullptr") {
    Arena<std::string> a{Kind::Net};
    Handle h = a.alloc("x");
    a.free(h);
    REQUIRE(a.get(h) == nullptr);
    Handle h2 = a.alloc("y");   // reuses slot 0
    REQUIRE(h2.index == h.index);
    REQUIRE(h2.generation == h.generation + 1);
    REQUIRE(a.get(h) == nullptr);          // old handle still dead
    REQUIRE(*a.get(h2) == "y");
}
TEST_CASE("kind mismatch returns nullptr") {
    Arena<std::string> a{Kind::Net};
    Handle h = a.alloc("x");
    Handle wrong = h; wrong.kind = Kind::Cline;
    REQUIRE(a.get(wrong) == nullptr);
}
TEST_CASE("iteration visits only live slots") {
    Arena<std::string> a{Kind::Net};
    Handle h1 = a.alloc("a");
    Handle h2 = a.alloc("b");
    a.free(h1);
    int n = 0;
    a.for_each([&](Handle h, const std::string& v){ n++; REQUIRE(v == "b"); REQUIRE(h == h2); });
    REQUIRE(n == 1);
}
TEST_CASE("resurrect after slot reuse never re-mints stale generations") {
    Arena<std::string> a{Kind::Net};
    Handle h1 = a.alloc("A");           // slot 0, gen 1
    REQUIRE(h1.generation == 1);
    a.free(h1);
    Handle h2 = a.alloc("B");           // slot 0 reused, gen 2
    REQUIRE(h2.index == h1.index);
    REQUIRE(h2.generation == 2);
    a.free(h2);
    a.resurrect(h1, "A");               // slot generation rolls back to 1 for lookup
    REQUIRE(a.get(h1) != nullptr);
    REQUIRE(*a.get(h1) == "A");
    REQUIRE(a.get(h2) == nullptr);      // B stays dead
    a.free(h1);
    Handle h3 = a.alloc("C");           // must mint from high water: gen 3, NOT 2
    REQUIRE(h3.index == h1.index);
    REQUIRE(h3.generation == 3);
    REQUIRE(a.get(h2) == nullptr);      // B stays dead forever
    REQUIRE(*a.get(h3) == "C");
}
