#include <catch2/catch_test_macros.hpp>
#include "sdb/core/design.h"
using namespace sdb;

static Design make() { return Design("demo"); }

TEST_CASE("layers are created in stackup order and findable by name") {
    Design d = make();
    auto txn = d.begin("setup");
    Handle l1 = txn.create_layer({.name="L1", .type=LayerType::Conductor, .thickness_um=15});
    Handle l2 = txn.create_layer({.name="L2", .type=LayerType::Conductor, .thickness_um=15});
    txn.commit();
    REQUIRE(d.layer_by_name("L1") == l1);
    REQUIRE(d.layer(l1)->order == 0);
    REQUIRE(d.layer(l2)->order == 1);
    REQUIRE(d.conductor_layers() == std::vector<Handle>{l1, l2});
}
TEST_CASE("padstack, symbol, component, net wiring") {
    Design d = make();
    auto txn = d.begin("setup");
    Handle l1 = txn.create_layer({.name="L1", .type=LayerType::Conductor});
    Padstack ps{.name="BALL400"};
    ps.pads[l1] = PadDef{PadShape::Circle, 400'000, 400'000};   // 400 um
    Handle hps = txn.create_padstack(ps);
    Symbol sym{.name="BGA4"};
    sym.pins = {{"A1", hps, {0,0}}, {"A2", hps, {800'000,0}}};
    Handle hsym = txn.create_symbol(sym);
    Handle net = txn.create_net({.name="VDD"});
    Handle comp = txn.create_component({.refdes="U1", .symbol=hsym, .origin={0,0}});
    txn.assign_pin(comp, "A1", net);
    txn.commit();
    REQUIRE(d.component(comp)->pin_nets.at("A1") == net);
    REQUIRE(d.net_by_name("VDD") == net);
    // pin_position applies component transform to pin offset
    REQUIRE(d.pin_position(comp, "A2") == Point{800'000, 0});
}
TEST_CASE("conductor geometry carries layer and net") {
    Design d = make();
    auto txn = d.begin("geo");
    Handle l1 = txn.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle net = txn.create_net({.name="N1"});
    Cline cl{.layer=l1, .net=net};
    cl.segs = {Segment::line({0,0},{1'000'000,0}, 20'000)};
    Handle hc = txn.create_cline(cl);
    Handle hv = txn.create_via({.padstack=kNullHandle, .net=net, .pos={0,0},
                                .from_layer=l1, .to_layer=l1});
    txn.commit();
    REQUIRE(d.cline(hc)->net == net);
    REQUIRE(d.via(hv)->pos == Point{0,0});
}
TEST_CASE("typed properties attach to any handle") {
    Design d = make();
    auto txn = d.begin("p");
    Handle net = txn.create_net({.name="N1"});
    txn.set_property(net, "ZDIFF", 85.0);
    txn.set_property(net, "BUS", std::string("DDR"));
    txn.commit();
    REQUIRE(d.get_property_double(net, "ZDIFF") == 85.0);
    REQUIRE(d.get_property_string(net, "BUS") == "DDR");
    REQUIRE_FALSE(d.get_property_double(net, "MISSING").has_value());
}
