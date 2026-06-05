# Plan 1/3: Core Substrate Database (sdb) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the C++ in-memory object database (arenas, generational handles, object model, transactions/undo) with nanobind Python bindings and a working build/test skeleton.

**Architecture:** C++20 static library `sdbcore` (namespace `sdb`) + nanobind module `_sdb` + Python package `substrate`. Integer (int64, 1 nm) coordinates. All mutations go through a transaction journal that emits dirty notifications. Spec: `docs/superpowers/specs/2026-06-03-substrate-db-foundation-design.md`.

**Tech Stack:** C++20, CMake ≥3.26 + Ninja, scikit-build-core, nanobind, Catch2 v3, pytest. Dependencies fetched via CMake `FetchContent` (nanobind, Catch2); boost headers not needed until Plan 2.

**Worker notes (apply to every task):**
- TDD: write the test, see it fail, implement, see it pass, commit.
- C++ test command: `cmake --build build && ctest --test-dir build --output-on-failure`
- Python test command: `python -m pytest tests/python -v`
- Editable install after binding changes: `pip install -e . --no-build-isolation -Ceditable.rebuild=true` (first install: `pip install -e .`)
- Never expose raw pointers through nanobind; Python sees handles and value copies only.

---

### Task 1: Repository and build skeleton

**Files:**
- Create: `CMakeLists.txt`, `pyproject.toml`, `.gitignore`
- Create: `src/sdb/version.h`, `src/sdb/version.cpp`
- Create: `bindings/module.cpp`
- Create: `python/substrate/__init__.py`
- Create: `tests/cpp/CMakeLists.txt`, `tests/cpp/test_version.cpp`
- Test: `tests/python/test_import.py`

- [ ] **Step 1: Write the C++ and Python smoke tests**

`tests/cpp/test_version.cpp`:
```cpp
#include <catch2/catch_test_macros.hpp>
#include "sdb/version.h"
TEST_CASE("version string is set") {
    REQUIRE(sdb::version() == std::string("0.1.0"));
}
```

`tests/python/test_import.py`:
```python
import substrate

def test_version():
    assert substrate.__version__ == "0.1.0"
```

- [ ] **Step 2: Write the build files**

`CMakeLists.txt` (root):
```cmake
cmake_minimum_required(VERSION 3.26)
project(sdb LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

file(GLOB_RECURSE SDB_SOURCES CONFIGURE_DEPENDS src/sdb/*.cpp)
add_library(sdbcore STATIC ${SDB_SOURCES})
target_include_directories(sdbcore PUBLIC src)

include(FetchContent)
if(SDB_BUILD_PYTHON)
  find_package(Python 3.10 COMPONENTS Interpreter Development.Module REQUIRED)
  FetchContent_Declare(nanobind
    GIT_REPOSITORY https://github.com/wjakob/nanobind
    GIT_TAG v2.4.0)
  FetchContent_MakeAvailable(nanobind)
  nanobind_add_module(_sdb bindings/module.cpp)
  target_link_libraries(_sdb PRIVATE sdbcore)
  install(TARGETS _sdb LIBRARY DESTINATION substrate)
endif()

if(SDB_BUILD_TESTS)
  FetchContent_Declare(catch2
    GIT_REPOSITORY https://github.com/catchorg/Catch2
    GIT_TAG v3.7.1)
  FetchContent_MakeAvailable(catch2)
  enable_testing()
  add_subdirectory(tests/cpp)
endif()
```

`tests/cpp/CMakeLists.txt`:
```cmake
file(GLOB TEST_SOURCES CONFIGURE_DEPENDS *.cpp)
add_executable(sdb_tests ${TEST_SOURCES})
target_link_libraries(sdb_tests PRIVATE sdbcore Catch2::Catch2WithMain)
include(Catch)
catch_discover_tests(sdb_tests)
```

`pyproject.toml`:
```toml
[build-system]
requires = ["scikit-build-core", "nanobind"]
build-backend = "scikit_build_core.build"

[project]
name = "substrate"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["gdstk>=0.9"]

[project.optional-dependencies]
dev = ["pytest>=7"]

[tool.scikit-build]
cmake.args = ["-DSDB_BUILD_PYTHON=ON", "-DSDB_BUILD_TESTS=OFF"]
wheel.packages = ["python/substrate"]
build-dir = "build/py"
```

`.gitignore`:
```
build/
*.egg-info/
__pycache__/
.venv/
```

- [ ] **Step 3: Write minimal sources**

`src/sdb/version.h`:
```cpp
#pragma once
#include <string>
namespace sdb { std::string version(); }
```

`src/sdb/version.cpp`:
```cpp
#include "sdb/version.h"
namespace sdb { std::string version() { return "0.1.0"; } }
```

`bindings/module.cpp`:
```cpp
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include "sdb/version.h"
namespace nb = nanobind;
NB_MODULE(_sdb, m) { m.def("version", &sdb::version); }
```

`python/substrate/__init__.py`:
```python
from ._sdb import version
__version__ = version()
```

- [ ] **Step 4: Build and verify both test suites pass**

Run:
```bash
cmake -S . -B build -G Ninja -DSDB_BUILD_TESTS=ON -DSDB_BUILD_PYTHON=OFF
cmake --build build && ctest --test-dir build --output-on-failure
pip install -e .[dev]
python -m pytest tests/python -v
```
Expected: 1 C++ test PASS, 1 Python test PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: build skeleton (sdbcore + nanobind module + test harnesses)"
```

---

### Task 2: Integer geometry value types

**Files:**
- Create: `src/sdb/geom/types.h`
- Test: `tests/cpp/test_geom_types.cpp`

- [ ] **Step 1: Write failing tests**

`tests/cpp/test_geom_types.cpp`:
```cpp
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
```

- [ ] **Step 2: Run, verify FAIL (header missing)**

- [ ] **Step 3: Implement**

`src/sdb/geom/types.h`:
```cpp
#pragma once
#include <cstdint>
#include <algorithm>
#include <limits>
namespace sdb {
using Coord = int64_t;            // database units: 1 nm
struct Point {
    Coord x = 0, y = 0;
    bool operator==(const Point&) const = default;
};
struct Box {
    Point lo, hi;
    bool operator==(const Box&) const = default;
    static Box of(Point a, Point b) {
        return {{std::min(a.x,b.x), std::min(a.y,b.y)},
                {std::max(a.x,b.x), std::max(a.y,b.y)}};
    }
    static Box empty() {
        constexpr Coord M = std::numeric_limits<Coord>::max();
        return {{M,M},{-M,-M}};
    }
    bool is_empty() const { return lo.x > hi.x || lo.y > hi.y; }
    Box inflated(Coord d) const { return {{lo.x-d,lo.y-d},{hi.x+d,hi.y+d}}; }
    Box united(const Box& o) const {
        if (is_empty()) return o;
        if (o.is_empty()) return *this;
        return {{std::min(lo.x,o.lo.x), std::min(lo.y,o.lo.y)},
                {std::max(hi.x,o.hi.x), std::max(hi.y,o.hi.y)}};
    }
    bool intersects(const Box& o) const {
        return !is_empty() && !o.is_empty() &&
               lo.x <= o.hi.x && o.lo.x <= hi.x &&
               lo.y <= o.hi.y && o.lo.y <= hi.y;
    }
};
}
```

- [ ] **Step 4: Run tests, verify PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat: integer Point/Box geometry value types"`

---

### Task 3: Generational arena and handles

**Files:**
- Create: `src/sdb/core/handle.h`, `src/sdb/core/arena.h`
- Test: `tests/cpp/test_arena.cpp`

- [ ] **Step 1: Write failing tests**

`tests/cpp/test_arena.cpp`:
```cpp
#include <catch2/catch_test_macros.hpp>
#include "sdb/core/arena.h"
#include <string>
using namespace sdb;

TEST_CASE("alloc/get round trip") {
    Arena<std::string> a;
    Handle h = a.alloc("hello", Kind::Net);
    REQUIRE(h.kind == Kind::Net);
    REQUIRE(*a.get(h) == "hello");
    REQUIRE(a.size() == 1);
}
TEST_CASE("stale handle after free returns nullptr") {
    Arena<std::string> a;
    Handle h = a.alloc("x", Kind::Net);
    a.free(h);
    REQUIRE(a.get(h) == nullptr);
    Handle h2 = a.alloc("y", Kind::Net);   // reuses slot 0
    REQUIRE(h2.index == h.index);
    REQUIRE(h2.generation == h.generation + 1);
    REQUIRE(a.get(h) == nullptr);          // old handle still dead
    REQUIRE(*a.get(h2) == "y");
}
TEST_CASE("kind mismatch returns nullptr") {
    Arena<std::string> a;
    Handle h = a.alloc("x", Kind::Net);
    Handle wrong = h; wrong.kind = Kind::Cline;
    REQUIRE(a.get(wrong) == nullptr);
}
TEST_CASE("iteration visits only live slots") {
    Arena<std::string> a;
    Handle h1 = a.alloc("a", Kind::Net);
    Handle h2 = a.alloc("b", Kind::Net);
    a.free(h1);
    int n = 0;
    a.for_each([&](Handle h, const std::string& v){ n++; REQUIRE(v == "b"); REQUIRE(h == h2); });
    REQUIRE(n == 1);
}
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement**

`src/sdb/core/handle.h`:
```cpp
#pragma once
#include <cstdint>
#include <functional>
namespace sdb {
enum class Kind : uint8_t {
    Layer, Padstack, Symbol, Component, Net, NetClass, Cline, Via, Shape, CSet
};
struct Handle {
    Kind kind{};
    uint32_t index = 0;
    uint32_t generation = 0;
    bool operator==(const Handle&) const = default;
    bool valid() const { return generation != 0; }   // generation 0 == null handle
};
inline constexpr Handle kNullHandle{};
}
template<> struct std::hash<sdb::Handle> {
    size_t operator()(const sdb::Handle& h) const {
        return (size_t(h.kind) << 56) ^ (size_t(h.generation) << 32) ^ h.index;
    }
};
```

`src/sdb/core/arena.h`:
```cpp
#pragma once
#include "sdb/core/handle.h"
#include <vector>
#include <optional>
#include <cassert>
namespace sdb {
template <typename T>
class Arena {
    struct Slot { std::optional<T> value; uint32_t generation = 0; };
    std::vector<Slot> slots_;
    std::vector<uint32_t> free_;
    size_t live_ = 0;
public:
    Handle alloc(T value, Kind kind) {
        uint32_t idx;
        if (!free_.empty()) { idx = free_.back(); free_.pop_back(); }
        else { idx = uint32_t(slots_.size()); slots_.emplace_back(); }
        Slot& s = slots_[idx];
        s.value = std::move(value);
        s.generation += 1;                  // first live generation is 1
        live_++;
        return Handle{kind, idx, s.generation};
    }
    T* get(Handle h) {
        if (h.index >= slots_.size()) return nullptr;
        Slot& s = slots_[h.index];
        if (!s.value || s.generation != h.generation) return nullptr;
        return &*s.value;
    }
    const T* get(Handle h) const { return const_cast<Arena*>(this)->get(h); }
    bool free(Handle h) {
        if (!get(h)) return false;
        slots_[h.index].value.reset();
        free_.push_back(h.index);
        live_--;
        return true;
    }
    // Re-create an object in a specific slot with a specific generation (undo support).
    void resurrect(Handle h, T value) {
        assert(h.index < slots_.size() && !slots_[h.index].value);
        Slot& s = slots_[h.index];
        s.value = std::move(value);
        s.generation = h.generation;
        std::erase(free_, h.index);
        live_++;
    }
    size_t size() const { return live_; }
    template <typename F> void for_each(F&& f) const {
        for (uint32_t i = 0; i < slots_.size(); i++)
            if (slots_[i].value) f(Handle{T::kKind, i, slots_[i].generation}, *slots_[i].value);
    }
};
}
```

Note: `for_each` requires `T::kKind`; the test's `Arena<std::string>` can't use it — adjust: store the kind in the arena instead. **Final design:** `Arena` constructor takes `Kind kind_` member, `alloc(T value)` uses it, `for_each` uses it. Update tests accordingly: `Arena<std::string> a{Kind::Net}; Handle h = a.alloc("hello");`. The implementer must apply this constructor-based form consistently (tests in Step 1 are written against the constructor form in the committed version).

- [ ] **Step 4: Run tests, verify PASS**

- [ ] **Step 5: Commit** — `git commit -m "feat: generational arena with stale-handle detection and resurrect"`

---

### Task 4: Object model structs and Design container

**Files:**
- Create: `src/sdb/core/objects.h`, `src/sdb/core/design.h`, `src/sdb/core/design.cpp`
- Test: `tests/cpp/test_design.cpp`

- [ ] **Step 1: Write failing tests**

`tests/cpp/test_design.cpp`:
```cpp
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
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement object structs**

`src/sdb/core/objects.h`:
```cpp
#pragma once
#include "sdb/core/handle.h"
#include "sdb/geom/types.h"
#include <string>
#include <vector>
#include <map>
namespace sdb {
enum class LayerType : uint8_t { Conductor, Dielectric, Mask };
struct Layer {
    std::string name;
    LayerType type = LayerType::Conductor;
    int order = -1;                // assigned by Design on create
    double thickness_um = 0, epsilon_r = 0;
    std::string material;
};
enum class PadShape : uint8_t { Circle, Rect, RoundedRect };
struct PadDef { PadShape shape = PadShape::Circle; Coord w = 0, h = 0; };
struct Padstack {
    std::string name;
    std::map<Handle, PadDef> pads;     // conductor/mask layer -> pad
    Coord drill = 0;
};
struct SymbolPin { std::string number; Handle padstack; Point offset; };
struct Symbol {
    std::string name;
    std::vector<SymbolPin> pins;
    std::vector<Point> boundary;
};
struct Component {
    std::string refdes;
    Handle symbol;
    Point origin;
    int rotation_cw_deg = 0;           // 0/90/180/270 only
    bool mirrored = false;
    std::map<std::string, Handle> pin_nets;   // pin number -> net
};
struct NetClass { std::string name; Handle cset; };
struct Net { std::string name; Handle net_class; Handle cset; };
struct Segment {
    Point a, b;
    bool is_arc = false;
    Point center;                       // valid when is_arc
    bool cw = false;
    Coord width = 0;
    static Segment line(Point a, Point b, Coord w) { return {a, b, false, {}, false, w}; }
    static Segment arc(Point a, Point b, Point c, bool cw, Coord w) { return {a, b, true, c, cw, w}; }
};
struct Cline { Handle layer, net; std::vector<Segment> segs; };
struct Via { Handle padstack, net; Point pos; Handle from_layer, to_layer; };
struct Shape {
    Handle layer, net;
    std::vector<Point> outline;                  // closed, implicit last->first edge
    std::vector<std::vector<Point>> voids;
};
}
```

- [ ] **Step 4: Implement Design and Transaction (creation subset)**

`src/sdb/core/design.h` — the contract for this task (undo/notify enriched in Task 5):
```cpp
#pragma once
#include "sdb/core/arena.h"
#include "sdb/core/objects.h"
#include <optional>
#include <variant>
#include <unordered_map>
namespace sdb {
using PropValue = std::variant<int64_t, double, std::string, bool, Handle>;
class Design;

class Transaction {
public:
    // creation — each returns the new handle
    Handle create_layer(Layer);
    Handle create_padstack(Padstack);
    Handle create_symbol(Symbol);
    Handle create_component(Component);
    Handle create_net(Net);
    Handle create_net_class(NetClass);
    Handle create_cline(Cline);
    Handle create_via(Via);
    Handle create_shape(Shape);
    // mutation
    void assign_pin(Handle component, const std::string& pin, Handle net);
    void set_property(Handle target, const std::string& key, PropValue value);
    void update_cline(Handle, Cline);        // whole-value replace (snapshot undo)
    void update_via(Handle, Via);
    void update_shape(Handle, Shape);
    void erase(Handle);                       // any geometry/object kind
    void commit();
    void abort();
    ~Transaction();                            // aborts if not committed
private:
    friend class Design;
    explicit Transaction(Design& d, std::string name);
    Design* d_;
    std::string name_;
    bool open_ = true;
    // journal entries defined in Task 5
};

class Design {
public:
    explicit Design(std::string name);
    Transaction begin(std::string txn_name);
    // typed read accessors (nullptr if stale/wrong kind)
    const Layer* layer(Handle) const;     const Padstack* padstack(Handle) const;
    const Symbol* symbol(Handle) const;   const Component* component(Handle) const;
    const Net* net(Handle) const;         const Cline* cline(Handle) const;
    const Via* via(Handle) const;         const Shape* shape(Handle) const;
    Handle layer_by_name(const std::string&) const;
    Handle net_by_name(const std::string&) const;
    std::vector<Handle> conductor_layers() const;    // in stackup order
    Point pin_position(Handle component, const std::string& pin) const;
    std::optional<double> get_property_double(Handle, const std::string&) const;
    std::optional<std::string> get_property_string(Handle, const std::string&) const;
    std::optional<int64_t> get_property_int(Handle, const std::string&) const;
private:
    friend class Transaction;
    std::string name_;
    Arena<Layer> layers_{Kind::Layer};
    Arena<Padstack> padstacks_{Kind::Padstack};
    Arena<Symbol> symbols_{Kind::Symbol};
    Arena<Component> components_{Kind::Component};
    Arena<Net> nets_{Kind::Net};
    Arena<NetClass> net_classes_{Kind::NetClass};
    Arena<Cline> clines_{Kind::Cline};
    Arena<Via> vias_{Kind::Via};
    Arena<Shape> shapes_{Kind::Shape};
    std::unordered_map<Handle, std::map<std::string, PropValue>> props_;
    int next_layer_order_ = 0;
};
}
```

Implementation notes for `design.cpp`:
- `create_layer` stamps `order = next_layer_order_++` before storing.
- `pin_position`: look up component → symbol → pin offset; apply `mirrored` (negate x), then rotation (0/90/180/270 integer rotation), then add `origin`. Rotation math: 90° cw maps `(x,y) → (y,-x)`.
- Name lookups scan the arena (`for_each`); designs in v1 are small enough. No name-uniqueness enforcement in v1.
- In this task `Transaction` applies operations immediately to the arenas and `commit()` just closes it; journaling arrives in Task 5.

- [ ] **Step 5: Run tests, verify PASS**

- [ ] **Step 6: Commit** — `git commit -m "feat: object model structs and Design container with creation transactions"`

---

### Task 5: Transaction journal, undo/redo, dirty notifications

**Files:**
- Modify: `src/sdb/core/design.h`, `src/sdb/core/design.cpp`
- Test: `tests/cpp/test_undo.cpp`

- [ ] **Step 1: Write failing tests**

`tests/cpp/test_undo.cpp`:
```cpp
#include <catch2/catch_test_macros.hpp>
#include "sdb/core/design.h"
using namespace sdb;

static std::pair<Handle,Handle> setup(Design& d) {  // returns (layer, net)
    auto t = d.begin("setup");
    Handle l = t.create_layer({.name="L1", .type=LayerType::Conductor});
    Handle n = t.create_net({.name="N1"});
    t.commit();
    return {l, n};
}
static Cline mk_cline(Handle l, Handle n, Coord x0=0) {
    Cline c{.layer=l, .net=n};
    c.segs = {Segment::line({x0,0},{x0+1'000'000,0}, 20'000)};
    return c;
}

TEST_CASE("undo reverses a whole transaction; redo replays it") {
    Design d("t"); auto [l, n] = setup(d);
    auto t = d.begin("add two clines");
    Handle c1 = t.create_cline(mk_cline(l,n,0));
    Handle c2 = t.create_cline(mk_cline(l,n,2'000'000));
    t.commit();
    REQUIRE(d.cline(c1) != nullptr);
    d.undo();
    REQUIRE(d.cline(c1) == nullptr);
    REQUIRE(d.cline(c2) == nullptr);
    d.redo();
    REQUIRE(d.cline(c1) != nullptr);      // same handles after redo
    REQUIRE(d.cline(c2)->segs[0].a.x == 2'000'000);
}
TEST_CASE("undo restores modified and erased objects") {
    Design d("t"); auto [l, n] = setup(d);
    auto t1 = d.begin("add"); Handle c = t1.create_cline(mk_cline(l,n)); t1.commit();
    auto t2 = d.begin("widen");
    Cline mod = *d.cline(c); mod.segs[0].width = 50'000;
    t2.update_cline(c, mod); t2.commit();
    auto t3 = d.begin("del"); t3.erase(c); t3.commit();
    REQUIRE(d.cline(c) == nullptr);
    d.undo();  REQUIRE(d.cline(c)->segs[0].width == 50'000);
    d.undo();  REQUIRE(d.cline(c)->segs[0].width == 20'000);
}
TEST_CASE("abort leaves no trace") {
    Design d("t"); auto [l, n] = setup(d);
    {
        auto t = d.begin("oops");
        t.create_cline(mk_cline(l,n));
    }   // destructor aborts
    bool any = false;
    // undo stack must contain only the setup txn
    REQUIRE(d.undo_depth() == 1);
    (void)any;
}
TEST_CASE("new transaction clears redo stack") {
    Design d("t"); auto [l, n] = setup(d);
    auto t = d.begin("a"); t.create_cline(mk_cline(l,n)); t.commit();
    d.undo();
    auto t2 = d.begin("b"); t2.create_cline(mk_cline(l,n,5'000'000)); t2.commit();
    REQUIRE(d.redo_depth() == 0);
}
TEST_CASE("commit notifies subscribers with touched handles and dirty boxes") {
    Design d("t"); auto [l, n] = setup(d);
    CommitEvent seen;
    d.subscribe([&](const CommitEvent& e){ seen = e; });
    auto t = d.begin("add"); Handle c = t.create_cline(mk_cline(l,n)); t.commit();
    REQUIRE(seen.name == "add");
    REQUIRE(seen.created == std::vector<Handle>{c});
    // dirty box on layer l covers the cline including width/2 inflation
    REQUIRE(seen.dirty_boxes.at(l).intersects(Box{{0,-10'000},{1'000'000,10'000}}));
    d.undo();   // undo/redo notify through the same channel
    REQUIRE(seen.deleted == std::vector<Handle>{c});
}
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement journal**

Add to `design.h`:
```cpp
struct CommitEvent {
    std::string name;
    std::vector<Handle> created, modified, deleted;
    std::map<Handle, Box> dirty_boxes;     // conductor layer -> union bbox of touched geometry
};
using CommitCallback = std::function<void(const CommitEvent&)>;
// on Design:
void subscribe(CommitCallback cb);
void undo();  void redo();
size_t undo_depth() const;  size_t redo_depth() const;
```

Journal design (implement in `design.cpp`):
```cpp
// One journal op = handle + optional before/after snapshots.
// create:  before=nullopt, after=value     undo: free      redo: resurrect
// modify:  before=value,  after=value      undo: write before   redo: write after
// erase:   before=value,  after=nullopt    undo: resurrect      redo: free
using AnyObject = std::variant<Layer, Padstack, Symbol, Component, Net, NetClass,
                               Cline, Via, Shape>;
struct JournalOp {
    Handle h;
    std::optional<AnyObject> before, after;
};
struct TxnRecord { std::string name; std::vector<JournalOp> ops; };
```
- `Transaction` ops apply immediately to arenas AND append a `JournalOp`. Property sets journal as modify ops on a synthetic snapshot of the property map entry (store `{key, old value, new value}` in a parallel op type or fold property maps into the object snapshot — **chosen approach: a separate `PropOp {Handle h; std::string key; std::optional<PropValue> before, after;}` list per TxnRecord**, replayed on undo/redo).
- `commit()`: push TxnRecord on undo stack, clear redo stack, build `CommitEvent` from ops (geometry kinds contribute to `dirty_boxes`, box computation: cline = union of segment bboxes inflated by width/2 — for arc segments use the conservative box `Box::of(a,b).united(Box::of(a,center)).united(Box::of(b,center))` inflated by width/2; via/pad = position inflated by max pad dimension; shape = outline bbox), invoke subscribers.
- `undo()`: pop record, apply inverse of each op in reverse order via `Arena::free`/`Arena::resurrect`/value write, push onto redo stack, emit a CommitEvent describing what changed (created/deleted swap roles).
- `abort()` / destructor of open txn: apply inverses immediately, discard record, no notification.
- `erase(Handle)` dispatches on `h.kind` to the right arena.

- [ ] **Step 4: Run tests, verify PASS** (all prior suites too)

- [ ] **Step 5: Commit** — `git commit -m "feat: transaction journal with undo/redo and commit notifications"`

---

### Task 6: nanobind bindings for the core + Python Design wrapper

**Files:**
- Modify: `bindings/module.cpp`
- Create: `python/substrate/design.py`, `python/substrate/units.py`
- Modify: `python/substrate/__init__.py`
- Test: `tests/python/test_design_api.py`

- [ ] **Step 1: Write failing Python tests**

`tests/python/test_design_api.py`:
```python
import pytest
import substrate
from substrate.units import um, mm

def test_units():
    assert um(1) == 1_000          # 1 um = 1000 nm
    assert mm(1) == 1_000_000

def test_build_and_query():
    db = substrate.Design("demo")
    with db.transaction("setup"):
        l1 = db.add_layer("L1", "conductor", thickness_um=15)
        n1 = db.add_net("VDD")
        c = db.add_cline(layer=l1, net=n1, width=um(20),
                         points=[(0, 0), (mm(1), 0), (mm(1), mm(1))])
    cl = db.cline(c)
    assert cl.width == um(20)
    assert len(cl.points) == 3
    assert db.layer_by_name("L1") == l1
    assert db.net_by_name("VDD") == n1

def test_transaction_rolls_back_on_exception():
    db = substrate.Design("demo")
    with db.transaction("setup"):
        db.add_layer("L1", "conductor")
        n1 = db.add_net("VDD")
    with pytest.raises(RuntimeError):
        with db.transaction("bad"):
            db.add_net("N2")
            raise RuntimeError("boom")
    assert db.net_by_name("N2") is None

def test_undo_redo():
    db = substrate.Design("demo")
    with db.transaction("setup"):
        db.add_net("VDD")
    db.undo()
    assert db.net_by_name("VDD") is None
    db.redo()
    assert db.net_by_name("VDD") is not None

def test_mutation_outside_transaction_raises():
    db = substrate.Design("demo")
    with pytest.raises(RuntimeError):
        db.add_net("VDD")

def test_properties():
    db = substrate.Design("demo")
    with db.transaction("p"):
        n = db.add_net("VDD")
        db.set_property(n, "BUS", "DDR")
    assert db.get_property(n, "BUS") == "DDR"
    assert db.get_property(n, "MISSING") is None

def test_stale_handle_returns_none():
    db = substrate.Design("demo")
    with db.transaction("a"):
        n = db.add_net("VDD")
    db.undo()
    assert db.net(n) is None
```

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Bind C++ types**

`bindings/module.cpp` — bind: `Handle` (with `__eq__`, `__hash__`, `__repr__`), `Kind`, `Point` (accept 2-tuples via implicit conversion), `Box`, enums (`LayerType`, `PadShape`), object structs as read-only value views, `Design` (constructor, `begin`, read accessors returning copies/`std::optional` → None), `Transaction` (all create/update/erase/property methods, `commit`, `abort`). Use `nb::class_<...>`, `nanobind/stl/*.h` casters for string/vector/map/variant/optional. `PropValue` variant maps to Python int/float/str/bool/Handle automatically via `nanobind/stl/variant.h`.

Binding rules:
- `Design::begin` returns `Transaction` by value — bind with `nb::rv_policy::move`.
- Read accessors return `std::optional<T>` copies, not pointers (`d.net(h)` → `std::optional<Net>`); add small inline lambdas in the binding that convert `const T*` to `std::optional<T>`.

- [ ] **Step 4: Python wrapper**

`python/substrate/units.py`:
```python
def nm(v): return int(round(v))
def um(v): return int(round(v * 1_000))
def mm(v): return int(round(v * 1_000_000))
```

`python/substrate/design.py`:
```python
from contextlib import contextmanager
from . import _sdb

_LAYER_TYPES = {"conductor": _sdb.LayerType.Conductor,
                "dielectric": _sdb.LayerType.Dielectric,
                "mask": _sdb.LayerType.Mask}

class ClineView:
    def __init__(self, raw):
        self._raw = raw
        self.width = raw.segs[0].width if raw.segs else 0
        pts = [(raw.segs[0].a.x, raw.segs[0].a.y)] if raw.segs else []
        pts += [(s.b.x, s.b.y) for s in raw.segs]
        self.points = pts
        self.layer, self.net = raw.layer, raw.net

class Design:
    def __init__(self, name):
        self._d = _sdb.Design(name)
        self._txn = None

    @contextmanager
    def transaction(self, name):
        if self._txn is not None:
            raise RuntimeError("nested transactions not supported")
        self._txn = self._d.begin(name)
        try:
            yield self
        except BaseException:
            self._txn.abort(); self._txn = None
            raise
        else:
            self._txn.commit(); self._txn = None

    def _t(self):
        if self._txn is None:
            raise RuntimeError("mutation requires an open transaction")
        return self._txn

    def add_layer(self, name, type, thickness_um=0.0):
        lay = _sdb.Layer(); lay.name = name
        lay.type = _LAYER_TYPES[type]; lay.thickness_um = thickness_um
        return self._t().create_layer(lay)

    def add_net(self, name):
        n = _sdb.Net(); n.name = name
        return self._t().create_net(n)

    def add_cline(self, layer, net, width, points):
        c = _sdb.Cline(); c.layer = layer; c.net = net
        segs = []
        for a, b in zip(points, points[1:]):
            segs.append(_sdb.Segment.line(_sdb.Point(*a), _sdb.Point(*b), width))
        c.segs = segs
        return self._t().create_cline(c)

    def set_property(self, h, key, value):  self._t().set_property(h, key, value)
    def get_property(self, h, key):         return self._d.get_property(h, key)

    def cline(self, h):
        raw = self._d.cline(h)
        return ClineView(raw) if raw is not None else None
    def net(self, h):              return self._d.net(h)
    def layer_by_name(self, name):
        h = self._d.layer_by_name(name); return h if h.valid() else None
    def net_by_name(self, name):
        h = self._d.net_by_name(name); return h if h.valid() else None
    def undo(self): self._d.undo()
    def redo(self): self._d.redo()
```

(`get_property` needs a generic C++ accessor `std::optional<PropValue> Design::get_property(Handle, key)` — add it and bind it; the typed C++ getters from Task 4 remain.)

`python/substrate/__init__.py`:
```python
from ._sdb import version, Handle, Point, Box
from .design import Design
from .units import nm, um, mm
__version__ = version()
```

- [ ] **Step 5: Rebuild, run pytest, verify PASS**

Run: `pip install -e .[dev] && python -m pytest tests/python -v`
Expected: all tests PASS (plus C++ suite still green).

- [ ] **Step 6: Commit** — `git commit -m "feat: nanobind bindings and Pythonic Design wrapper with transactions"`

---

## Plan-level acceptance

All C++ and Python tests green; `python -c "import substrate; db = substrate.Design('x')"` works in a fresh venv. This completes the core database; Plan 2 (engines) builds on the dirty-notification and arena infrastructure created here.
