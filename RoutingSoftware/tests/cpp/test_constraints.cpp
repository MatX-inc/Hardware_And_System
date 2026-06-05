#include <catch2/catch_test_macros.hpp>
#include "sdb/constraints/constraints.h"
#include "sdb/core/design.h"
using namespace sdb;

TEST_CASE("resolution walks net -> class -> system default") {
    Design d("t");
    ConstraintResolver res(d);
    auto t = d.begin("s");
    CSet sys; sys.name="SYS";
    sys.spacing.set(ObjKind::Line, ObjKind::Line, 50'000);
    sys.width_min = 20'000;
    Handle hsys = t.create_cset(sys);
    t.set_system_cset(hsys);
    CSet ddr; ddr.name="DDR";
    ddr.spacing.set(ObjKind::Line, ObjKind::Line, 30'000);  // width_min unset -> inherit
    Handle hddr = t.create_cset(ddr);
    Handle klass = t.create_net_class({.name="DDR_CLASS", .cset=hddr});
    Handle n1 = t.create_net({.name="DQ0", .net_class=klass});
    Handle n2 = t.create_net({.name="MISC"});
    t.commit();
    REQUIRE(res.spacing(n1, ObjKind::Line, ObjKind::Line) == 30'000);
    REQUIRE(res.spacing(n2, ObjKind::Line, ObjKind::Line) == 50'000);
    REQUIRE(res.width_min(n1) == 20'000);                 // inherited from SYS
    REQUIRE(res.max_clearance() == 50'000);
}
TEST_CASE("spacing matrix is symmetric and per-pair") {
    CSet c;
    c.spacing.set(ObjKind::Line, ObjKind::Via, 40'000);
    REQUIRE(c.spacing.get(ObjKind::Via, ObjKind::Line) == 40'000);
    REQUIRE_FALSE(c.spacing.get(ObjKind::Line, ObjKind::Line).has_value());
}
TEST_CASE("cache invalidates on constraint edits") {
    Design d("t");
    ConstraintResolver res(d);
    auto t = d.begin("s");
    CSet sys; sys.name="SYS"; sys.spacing.set(ObjKind::Line, ObjKind::Line, 50'000);
    Handle hsys = t.create_cset(sys);
    t.set_system_cset(hsys);
    Handle n = t.create_net({.name="N"});
    t.commit();
    REQUIRE(res.spacing(n, ObjKind::Line, ObjKind::Line) == 50'000);
    auto t2 = d.begin("edit");
    CSet upd = *d.cset(hsys);
    upd.spacing.set(ObjKind::Line, ObjKind::Line, 60'000);
    t2.update_cset(hsys, upd);
    t2.commit();
    REQUIRE(res.spacing(n, ObjKind::Line, ObjKind::Line) == 60'000);
}
