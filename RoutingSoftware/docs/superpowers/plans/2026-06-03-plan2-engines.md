# Plan 2/3: Engines (Geometry Kernel, R-tree, Constraints, Connectivity, DRC) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the analysis engines on top of the Plan-1 core: width-aware geometry predicates, incremental R-tree spatial index, hierarchical constraint system with resolved cache, union-find connectivity + ratsnest, and spacing DRC — all bound to Python.

**Architecture:** Engines subscribe to `Design` commit notifications (Plan 1, Task 5) and maintain derived state incrementally. DRC composes: R-tree clearance query → geometry-kernel exact distance → constraint-resolver required gap. Spec: `docs/superpowers/specs/2026-06-03-substrate-db-foundation-design.md`.

**Tech Stack:** boost (header-only, `boost::geometry::index::rtree`) added via system package or FetchContent of boostorg/geometry + deps; everything else as Plan 1.

**Prerequisite:** Plan 1 complete (all tasks committed, tests green).

**Worker notes:** same commands and TDD discipline as Plan 1. New C++ files must be added under `src/sdb/` (globbed automatically).

---

### Task 7: Geometry kernel — width-aware distance and polygonization

**Files:**
- Create: `src/sdb/geom/kernel.h`, `src/sdb/geom/kernel.cpp`
- Test: `tests/cpp/test_kernel.cpp`

- [ ] **Step 1: Write failing tests**

`tests/cpp/test_kernel.cpp`:
```cpp
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>
#include "sdb/geom/kernel.h"
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
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement**

`src/sdb/geom/kernel.h`:
```cpp
#pragma once
#include "sdb/geom/types.h"
#include <vector>
#include <cmath>
namespace sdb::geom {
double dist_point_segment(Point p, Point a, Point b);
double dist_segment_segment(Point a1, Point b1, Point a2, Point b2);
// gap between two width-carrying centerline segments (capsules); clamped >= 0
double gap_capsule_capsule(Point a1, Point b1, Coord w1, Point a2, Point b2, Coord w2);
// arc handled by sampling at tolerance `kArcDrcTol` (1.0 dbu) into a polyline,
// then min over segment pairs; conservative within 2*tol
double gap_segment_arc(Point a1, Point b1, Coord w1,
                       Point arc_a, Point arc_b, Point center, bool cw, Coord w2);
double gap_arc_arc(Point a1, Point b1, Point c1, bool cw1, Coord w1,
                   Point a2, Point b2, Point c2, bool cw2, Coord w2);
std::vector<Point> sample_arc(Point a, Point b, Point center, bool cw, double tol);
std::vector<Point> polygonize_capsule(Point a, Point b, Coord width, double tol);
std::vector<Point> polygonize_circle(Point center, Coord diameter, double tol);
Box poly_bbox(const std::vector<Point>& poly);
bool point_in_poly(Point p, const std::vector<Point>& poly);
inline constexpr double kArcDrcTol = 1.0;   // dbu
}
```

Implementation notes (`kernel.cpp`):
- All distance math in `double` on int64 inputs (53-bit mantissa is fine because v1 designs are << 2^50 dbu; document this).
- `dist_segment_segment`: if segments properly intersect (orientation test via `double` cross products), return 0; else min of the four point–segment distances.
- `sample_arc`: angle sweep from `atan2(a-center)` to `atan2(b-center)` in the cw/ccw direction (cw = decreasing angle); chord count `n = ceil(sweep / (2*acos(1 - tol/r)))`, min 2; emit exact endpoints, round interior points to integer.
- `gap_segment_arc` / `gap_arc_arc`: sample arcs at `kArcDrcTol`, min pairwise `dist_segment_segment` over polyline edges, subtract half-widths, clamp ≥ 0.
- `polygonize_capsule`: rectangle plus two semicircular caps sampled at `tol`; counter-clockwise winding.
- `point_in_poly`: standard even-odd ray cast with integer arithmetic.

- [ ] **Step 4: Run tests, verify PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat: geometry kernel with capsule gaps, arc sampling, polygonization"`

---

### Task 8: Object bounding boxes and footprint expansion

**Files:**
- Create: `src/sdb/geom/object_geometry.h`, `src/sdb/geom/object_geometry.cpp`
- Test: `tests/cpp/test_object_geometry.cpp`

Purpose: one place that answers, for any conductor handle, (a) its bbox per layer and (b) its primitive list (capsules/circles/polys) per layer — consumed by the R-tree, connectivity, DRC, and (Plan 3) GDS export. Component pins expand to pad primitives at `pin_position` on each padstack layer.

- [ ] **Step 1: Write failing tests**

`tests/cpp/test_object_geometry.cpp`:
```cpp
#include <catch2/catch_test_macros.hpp>
#include "sdb/geom/object_geometry.h"
#include "sdb/core/design.h"
using namespace sdb;

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
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement**

`src/sdb/geom/object_geometry.h`:
```cpp
#pragma once
#include "sdb/core/design.h"
#include <map>
namespace sdb {
struct Prim {
    enum class Kind { Capsule, Circle, Rect, Poly, Arc } kind;
    // Capsule / Arc: a,b(,center,cw) + width.  Circle/Rect: center + diameter or w/h.
    Point a, b, center; bool cw = false; Coord width = 0;
    Coord diameter = 0, w = 0, h = 0;
    std::vector<Point> poly;                   // Kind::Poly outline
    Handle net;                                // owning net (null for unassigned pins)
    std::string pin;                           // non-empty if from a component pin
};
// bbox of one object per conductor layer it touches
std::map<Handle, Box> object_bboxes(const Design&, Handle);
// flat primitive list for one object on one layer (pads expanded, transforms applied)
std::vector<Prim> object_primitives(const Design&, Handle, Handle layer);
// min gap between two prims (width-aware), via geom kernel dispatch
double prim_gap(const Prim&, const Prim&);
}
```

Implementation notes:
- Dispatch on `Handle::kind`: Cline → per-segment capsule/arc prims; Via → circle/rect pad per padstack layer entry; Shape → poly prim; Component → for each symbol pin, transform offset by `pin_position` logic, one prim per padstack layer; net = `pin_nets` lookup (null handle if unassigned).
- `prim_gap`: pairwise dispatch — capsule×capsule = `gap_capsule_capsule`; circle×X = capsule with `a==b` and `width=diameter`; rect×X in v1 = treat as capsule spanning the longer axis with width = shorter axis (conservative; exact rect support is v2 — document in code with the reason); arc×X = `gap_segment_arc`/`gap_arc_arc`; poly×X = min over poly edges as zero-width segments (capsule edges), and if one prim's point is inside the poly the gap is 0 (`point_in_poly` check both directions).
- `Design` gains read iteration: `for_each_object(Kind, fn(Handle))` — bind-friendly listing used by engines (add to `design.h/.cpp` here).

- [ ] **Step 4: Run tests, verify PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat: per-layer object bboxes and primitive expansion"`

---

### Task 9: R-tree spatial index with incremental maintenance

**Files:**
- Modify: `CMakeLists.txt` (boost dependency)
- Create: `src/sdb/index/spatial_index.h`, `src/sdb/index/spatial_index.cpp`
- Test: `tests/cpp/test_spatial_index.cpp`

- [ ] **Step 1: Add boost**

In root `CMakeLists.txt` before `sdbcore`:
```cmake
find_package(Boost 1.74 QUIET)
if(NOT Boost_FOUND)
  FetchContent_Declare(boost
    URL https://github.com/boostorg/boost/releases/download/boost-1.84.0/boost-1.84.0.tar.xz)
  FetchContent_MakeAvailable(boost)
endif()
target_link_libraries(sdbcore PUBLIC Boost::headers)
```
(If system boost exists, `Boost::headers` comes from `find_package`; the implementer verifies one of the two paths works on this machine and removes dead weight if system boost is present.)

- [ ] **Step 2: Write failing tests**

`tests/cpp/test_spatial_index.cpp`:
```cpp
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
```

- [ ] **Step 3: Run, verify FAIL**

- [ ] **Step 4: Implement**

`src/sdb/index/spatial_index.h`:
```cpp
#pragma once
#include "sdb/core/design.h"
#include "sdb/geom/object_geometry.h"
namespace sdb {
class SpatialIndex {
public:
    explicit SpatialIndex(Design& d);          // subscribes to d, bulk-loads existing
    std::vector<Handle> query_box(Handle layer, Box b) const;
    std::vector<Handle> query_clearance(Handle layer, Box b, Coord clearance) const;
    std::vector<Handle> net_objects(Handle net) const;
private:
    void on_commit(const CommitEvent&);
    void insert(Handle);  void remove(Handle);
    Design* d_;
    struct Impl;  std::unique_ptr<Impl> impl_;  // hides boost headers from users
};
}
```

Implementation notes:
- `Impl` holds `std::map<LayerKey, bgi::rtree<std::pair<BgBox, Handle>, bgi::rstar<16>>>` where `LayerKey = {Handle layer, Kind kind}`, plus `std::unordered_map<Handle, std::map<Handle,Box>> bbox_cache_` (needed to remove by old bbox) and `std::unordered_map<Handle, std::vector<Handle>> net_to_objects_`.
- `on_commit`: deleted → remove via cache; modified → remove + insert; created → insert. Insert uses `object_bboxes` (Task 8) and the object's net.
- Component handles are indexed too (their pads participate in DRC/connectivity).
- `query_clearance(layer, b, c)` = `query_box(layer, b.inflated(c))` across all kinds on that layer.

- [ ] **Step 5: Run tests, verify PASS**

- [ ] **Step 6: Commit** — `git commit -m "feat: incremental R-tree spatial index with net index"`

---

### Task 10: Constraint system with hierarchy and resolved cache

**Files:**
- Create: `src/sdb/constraints/constraints.h`, `src/sdb/constraints/constraints.cpp`
- Modify: `src/sdb/core/objects.h` (no change needed — `Net::cset`, `NetClass::cset` already exist), `src/sdb/core/design.h` (CSet arena + txn methods)
- Test: `tests/cpp/test_constraints.cpp`

- [ ] **Step 1: Write failing tests**

`tests/cpp/test_constraints.cpp`:
```cpp
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
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement**

`src/sdb/constraints/constraints.h`:
```cpp
#pragma once
#include "sdb/core/design.h"
#include <array>
#include <optional>
namespace sdb {
enum class ObjKind : uint8_t { Line, Pad, Via, Shape, COUNT };
struct SpacingMatrix {
    // upper-triangular storage, symmetric access
    std::array<std::optional<Coord>, size_t(ObjKind::COUNT)*size_t(ObjKind::COUNT)> v{};
    void set(ObjKind a, ObjKind b, Coord c);
    std::optional<Coord> get(ObjKind a, ObjKind b) const;
};
struct CSet {
    std::string name;
    std::optional<Coord> width_min, width_max;
    SpacingMatrix spacing;
};
class ConstraintResolver {
public:
    explicit ConstraintResolver(Design& d);     // subscribes for invalidation
    Coord spacing(Handle net, ObjKind a, ObjKind b) const;   // resolved; 0 if nothing set
    Coord width_min(Handle net) const;
    Coord max_clearance() const;                 // max spacing value anywhere (R-tree inflation)
private:
    const CSet* chain_next(const CSet* level, Handle net, int depth) const;
    Design* d_;
    mutable std::unordered_map<Handle, CSet> resolved_;     // net -> fully resolved cset
    mutable std::optional<Coord> max_clearance_;
    void invalidate();
};
}
```

Implementation notes:
- `Design` additions (this task): `Arena<CSet> csets_{Kind::CSet}`, `Handle system_cset_`, `Transaction::create_cset/update_cset/set_system_cset`, `Design::cset(Handle)`. `set_system_cset` journals as a modify op on a one-field snapshot.
- Resolution: start from empty resolved CSet; overlay system, then net-class cset, then net cset — later levels override only fields that are set (`optional` engaged / matrix cell engaged). Cache per net; `invalidate()` clears everything when a commit touches any `Kind::CSet`, `Kind::Net`, or `Kind::NetClass` handle.
- `spacing()` falls back to 0 when never set (and DRC will skip 0-required pairs).
- `max_clearance()`: max over all live CSets' engaged matrix cells; cached, invalidated together.

- [ ] **Step 4: Run tests, verify PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat: CSet constraint system with hierarchy resolution and cache"`

---

### Task 11: Connectivity engine and ratsnest

**Files:**
- Create: `src/sdb/connectivity/connectivity.h`, `src/sdb/connectivity/connectivity.cpp`
- Test: `tests/cpp/test_connectivity.cpp`

- [ ] **Step 1: Write failing tests**

`tests/cpp/test_connectivity.cpp`:
```cpp
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
TEST_CASE_METHOD(Fix, "incremental update on undo") {
    cline(l1, 0,0, 1000,0);
    cline(l1, 1000,0, 2000,0);
    REQUIRE(conn.net_status(net).cluster_count == 1);
    d.undo();
    REQUIRE(conn.net_status(net).cluster_count == 1);   // one cline left, one cluster
    d.undo();
    REQUIRE(conn.net_status(net).cluster_count == 0);
}
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement**

`src/sdb/connectivity/connectivity.h`:
```cpp
#pragma once
#include "sdb/index/spatial_index.h"
namespace sdb {
struct NetStatus { int cluster_count = 0; int open_pairs = 0; };
struct RatsEdge { Point a, b; };
class Connectivity {
public:
    Connectivity(Design& d, SpatialIndex& idx);   // subscribes; dirty-net lazy recompute
    NetStatus net_status(Handle net);
    std::vector<RatsEdge> ratsnest(Handle net);
    std::vector<std::pair<Handle,int>> clusters(Handle net);  // (object, cluster id)
private:
    void recompute(Handle net);
    Design* d_; SpatialIndex* idx_;
    std::unordered_map<Handle, std::vector<std::pair<Handle,int>>> cache_;
    std::unordered_set<Handle> dirty_;
};
}
```

Implementation notes:
- On commit: mark nets of all touched conductor objects dirty (look up net via the object or, for deletions, via the index's pre-removal net map — simplest correct v1: mark **all** nets in `CommitEvent` handles' nets dirty; if a deleted object's net is unrecoverable, mark all nets dirty. Keep it simple, measure later).
- `recompute(net)`: collect the net's objects from `idx_->net_objects(net)` **plus** components having a pin on this net (scan components via `for_each_object(Kind::Component)`); union-find over pairs whose per-layer prims (Task 8) have `prim_gap == 0` on a shared layer; vias bridge `from_layer..to_layer` (all conductor layers in stackup order between them). Candidate pairs come from R-tree box overlap, not all-pairs.
- Cluster representative point: first prim's `a`/`center`. Ratsnest: complete graph on representatives weighted by Euclidean distance, Prim's MST, edges reported with the two closest representative points. `open_pairs = cluster_count - 1` when `cluster_count > 0` else 0.
- A component with pins on two nets belongs to both nets' object sets (pin-level membership, from `pin_nets`).

- [ ] **Step 4: Run tests, verify PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat: union-find connectivity engine with ratsnest MST"`

---

### Task 12: DRC engine

**Files:**
- Create: `src/sdb/drc/drc.h`, `src/sdb/drc/drc.cpp`
- Test: `tests/cpp/test_drc.cpp`

- [ ] **Step 1: Write failing tests**

`tests/cpp/test_drc.cpp`:
```cpp
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
    cline(n1, 1200); cline(n1, 1400); // same net, gap 100 -> still clean in v1
    REQUIRE(drc.run_full().empty());
}
TEST_CASE_METHOD(Fix, "region mode only checks the window") {
    cline(n1, 0); cline(n2, 450);             // violating pair near y~0
    cline(n1, 100'000); cline(n2, 100'450);   // violating pair near y~100k
    auto v = drc.run_region(Box{{-1000,-1000},{20'000,2000}});
    REQUIRE(v.size() == 1);
}
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement**

`src/sdb/drc/drc.h`:
```cpp
#pragma once
#include "sdb/index/spatial_index.h"
#include "sdb/constraints/constraints.h"
namespace sdb {
struct Violation {
    Handle a, b;            // canonical: (a.kind,a.index) < (b.kind,b.index)
    Handle layer;
    double measured = 0;    // dbu
    Coord required = 0;
    Point location;         // midpoint between closest prims (marker anchor)
};
class Drc {
public:
    Drc(Design& d, SpatialIndex& idx, ConstraintResolver& res);
    std::vector<Violation> run_full();
    std::vector<Violation> run_region(Box window);
private:
    std::vector<Violation> check_layer(Handle layer, std::optional<Box> window);
    Design* d_; SpatialIndex* idx_; ConstraintResolver* res_;
};
}
```

Implementation notes:
- Per conductor layer: for each object (optionally bbox-filtered by window), `query_clearance` with `res_->max_clearance()`; for each candidate pair with different nets (component prims use per-pin nets) and canonical order `a < b`, compute min `prim_gap` over prim pairs on this layer; required = `max(res_->spacing(netA, kindA, kindB), res_->spacing(netB, kindA, kindB))` where ObjKind maps: Cline prim→Line, Via→Via, pin pad→Pad, Shape→Shape. Violation when `measured < required` and `required > 0`.
- Dedup with an `unordered_set<pair<Handle,Handle>>` (hash combine) — pairs seen from both sides count once.
- `location`: midpoint of prim `a`/`b` reference points (sufficient for marker anchoring; exact closest-point is v2 polish).
- Component pin prims with unassigned nets (`net == kNullHandle`) are treated as distinct-from-everything (always checked, never same-net skipped).

- [ ] **Step 4: Run tests, verify PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat: spacing DRC engine with region and full modes"`

---

### Task 13: Engine bindings + Python facade

**Files:**
- Modify: `bindings/module.cpp`
- Modify: `python/substrate/design.py`
- Test: `tests/python/test_engines.py`

- [ ] **Step 1: Write failing Python tests**

`tests/python/test_engines.py`:
```python
import substrate
from substrate.units import um, mm

def make_db():
    db = substrate.Design("t")
    with db.transaction("setup"):
        db.add_layer("L1", "conductor")
        db.set_default_spacing(line_line=um(0.5))     # 500 nm
        db.add_net("A"); db.add_net("B")
    return db

def test_query_box():
    db = make_db()
    with db.transaction("add"):
        a = db.add_cline(layer=db.layer_by_name("L1"), net=db.net_by_name("A"),
                         width=um(0.1), points=[(0, 0), (um(10), 0)])
    hits = db.query_box((0, -um(1), um(10), um(1)), layer="L1")
    assert hits == [a]

def test_drc_finds_violation():
    db = make_db()
    l1 = db.layer_by_name("L1")
    with db.transaction("add"):
        db.add_cline(layer=l1, net=db.net_by_name("A"), width=100, points=[(0,0),(10_000,0)])
        db.add_cline(layer=l1, net=db.net_by_name("B"), width=100, points=[(0,500),(10_000,500)])
    v = db.drc.run()
    assert len(v) == 1
    assert v[0].measured == 400 and v[0].required == 500

def test_connectivity_stats():
    db = make_db()
    l1 = db.layer_by_name("L1")
    n = db.net_by_name("A")
    with db.transaction("add"):
        db.add_cline(layer=l1, net=n, width=100, points=[(0,0),(1000,0)])
        db.add_cline(layer=l1, net=n, width=100, points=[(5000,0),(6000,0)])
    st = db.connectivity.status(n)
    assert st.cluster_count == 2 and st.open_pairs == 1
    rats = db.connectivity.ratsnest(n)
    assert len(rats) == 1
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Bind and wrap**

- Bind `SpatialIndex`, `ConstraintResolver`, `Connectivity`, `Drc`, `Violation`, `NetStatus`, `RatsEdge`, `CSet`, `ObjKind`, `SpacingMatrix` (`set`/`get`).
- `substrate.Design.__init__` constructs the C++ `Design` plus one `SpatialIndex`, `ConstraintResolver`, `Connectivity`, `Drc` (engine objects own subscriptions; construction order matters — index first).
- Facade additions to `python/substrate/design.py`:

```python
class _DrcFacade:
    def __init__(self, drc): self._drc = drc
    def run(self, region=None):
        if region is None: return self._drc.run_full()
        x0, y0, x1, y1 = region
        return self._drc.run_region(_sdb.Box(_sdb.Point(x0,y0), _sdb.Point(x1,y1)))

class _ConnFacade:
    def __init__(self, conn): self._conn = conn
    def status(self, net):    return self._conn.net_status(net)
    def ratsnest(self, net):  return self._conn.ratsnest(net)
```
- `db.query_box((x0,y0,x1,y1), layer="L1"|handle)` resolves layer names, returns handle list.
- `db.set_default_spacing(line_line=..., line_via=..., via_via=..., line_pad=...)`: creates/updates the system CSet inside the open transaction (keyword args map to `SpacingMatrix.set` cells).
- `db.set_net_class(net, class_name, spacing=None, width_min=None)` — optional helper if needed by demo; only add if the demo (Plan 3) uses it.

- [ ] **Step 4: Rebuild, run all tests, verify PASS**

Run: `pip install -e . --no-build-isolation && python -m pytest tests/python -v && ctest --test-dir build --output-on-failure`

- [ ] **Step 5: Commit** — `git commit -m "feat: bind engines to Python with drc/connectivity facades"`

---

## Plan-level acceptance

All suites green. From Python: build a design, query the index, run DRC and connectivity. The foundation is now functionally complete; Plan 3 adds generators, KLayout viewing, and the acceptance demo.
