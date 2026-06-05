#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>
#include <nanobind/stl/map.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/variant.h>
#include <nanobind/stl/pair.h>
#include "sdb/version.h"
#include "sdb/core/design.h"
#include "sdb/index/spatial_index.h"
#include "sdb/constraints/constraints.h"
#include "sdb/connectivity/connectivity.h"
#include "sdb/drc/drc.h"
#include "sdb/geom/export_polygons.h"
#include <iterator>

namespace nb = nanobind;
using namespace nb::literals;
using namespace sdb;

namespace {
// Wrap a `const T* Design::accessor(Handle) const` as optional<T> (copy out).
template <const auto Method>
auto opt_accessor() {
    return [](const Design& d, Handle h) {
        using Ptr = decltype((d.*Method)(h));
        using T = std::remove_const_t<std::remove_pointer_t<Ptr>>;
        Ptr p = (d.*Method)(h);
        return p ? std::optional<T>(*p) : std::nullopt;
    };
}
}  // namespace

NB_MODULE(_sdb, m) {
    m.def("version", &sdb::version);

    // ----------------------------------------------------------------- enums
    nb::enum_<Kind>(m, "Kind")
        .value("Layer", Kind::Layer).value("Padstack", Kind::Padstack)
        .value("Symbol", Kind::Symbol).value("Component", Kind::Component)
        .value("Net", Kind::Net).value("NetClass", Kind::NetClass)
        .value("Cline", Kind::Cline).value("Via", Kind::Via)
        .value("Shape", Kind::Shape).value("CSet", Kind::CSet);
    nb::enum_<LayerType>(m, "LayerType")
        .value("Conductor", LayerType::Conductor)
        .value("Dielectric", LayerType::Dielectric)
        .value("Mask", LayerType::Mask);
    nb::enum_<PadShape>(m, "PadShape")
        .value("Circle", PadShape::Circle)
        .value("Rect", PadShape::Rect)
        .value("RoundedRect", PadShape::RoundedRect);
    nb::enum_<ObjKind>(m, "ObjKind")
        .value("Line", ObjKind::Line).value("Pad", ObjKind::Pad)
        .value("Via", ObjKind::Via).value("Shape", ObjKind::Shape);

    // ---------------------------------------------------------------- Handle
    nb::class_<Handle>(m, "Handle")
        .def(nb::init<>())
        .def_ro("kind", &Handle::kind)
        .def_ro("index", &Handle::index)
        .def_ro("generation", &Handle::generation)
        .def("valid", &Handle::valid)
        .def("__eq__", [](const Handle& a, const Handle& b) { return a == b; })
        // Fallback so `handle == non_handle` is False rather than TypeError.
        .def("__eq__", [](const Handle&, nb::object) { return false; })
        .def("__hash__", [](const Handle& h) { return std::hash<Handle>{}(h); })
        .def("__repr__", [](const Handle& h) {
            const char* names[] = {"Layer","Padstack","Symbol","Component","Net",
                                   "NetClass","Cline","Via","Shape","CSet"};
            size_t k = size_t(h.kind);
            return std::string("Handle(") +
                   (k < std::size(names) ? names[k] : "?") +
                   ", index=" + std::to_string(h.index) +
                   ", gen=" + std::to_string(h.generation) + ")";
        });

    // -------------------------------------------------------------- geometry
    nb::class_<Point>(m, "Point")
        .def(nb::init<>())
        .def("__init__", [](Point* p, Coord x, Coord y) { new (p) Point{x, y}; },
             "x"_a, "y"_a)
        .def_rw("x", &Point::x)
        .def_rw("y", &Point::y)
        .def("__eq__", [](const Point& a, const Point& b) { return a == b; })
        .def("__hash__", [](const Point& p) {
            return std::hash<int64_t>{}(p.x) ^ (std::hash<int64_t>{}(p.y) << 1);
        })
        .def("__repr__", [](const Point& p) {
            return "Point(" + std::to_string(p.x) + ", " + std::to_string(p.y) + ")";
        });
    nb::class_<Box>(m, "Box")
        .def(nb::init<>())
        .def("__init__", [](Box* b, Point lo, Point hi) { new (b) Box{lo, hi}; },
             "lo"_a, "hi"_a)
        .def_rw("lo", &Box::lo)
        .def_rw("hi", &Box::hi)
        .def_static("of", &Box::of)
        .def_static("empty", &Box::empty)
        .def("is_empty", &Box::is_empty)
        .def("inflated", &Box::inflated)
        .def("united", &Box::united)
        .def("intersects", &Box::intersects)
        .def("__eq__", [](const Box& a, const Box& b) { return a == b; })
        .def("__repr__", [](const Box& b) {
            return "Box(Point(" + std::to_string(b.lo.x) + ", " + std::to_string(b.lo.y) +
                   "), Point(" + std::to_string(b.hi.x) + ", " + std::to_string(b.hi.y) + "))";
        });

    // --------------------------------------------------------------- objects
    nb::class_<Layer>(m, "Layer")
        .def(nb::init<>())
        .def_rw("name", &Layer::name)
        .def_rw("type", &Layer::type)
        .def_ro("order", &Layer::order)
        .def_rw("thickness_um", &Layer::thickness_um)
        .def_rw("epsilon_r", &Layer::epsilon_r)
        .def_rw("material", &Layer::material);
    nb::class_<PadDef>(m, "PadDef")
        .def(nb::init<>())
        .def_rw("shape", &PadDef::shape)
        .def_rw("w", &PadDef::w)
        .def_rw("h", &PadDef::h);
    nb::class_<Padstack>(m, "Padstack")
        .def(nb::init<>())
        .def_rw("name", &Padstack::name)
        .def_rw("pads", &Padstack::pads)
        .def_rw("drill", &Padstack::drill);
    nb::class_<SymbolPin>(m, "SymbolPin")
        .def(nb::init<>())
        .def("__init__", [](SymbolPin* p, std::string number, Handle padstack,
                            Point offset) {
                 new (p) SymbolPin{std::move(number), padstack, offset};
             }, "number"_a, "padstack"_a, "offset"_a)
        .def_rw("number", &SymbolPin::number)
        .def_rw("padstack", &SymbolPin::padstack)
        .def_rw("offset", &SymbolPin::offset);
    nb::class_<Symbol>(m, "Symbol")
        .def(nb::init<>())
        .def_rw("name", &Symbol::name)
        .def_rw("pins", &Symbol::pins)
        .def_rw("boundary", &Symbol::boundary);
    nb::class_<Component>(m, "Component")
        .def(nb::init<>())
        .def_rw("refdes", &Component::refdes)
        .def_rw("symbol", &Component::symbol)
        .def_rw("origin", &Component::origin)
        .def_rw("rotation_cw_deg", &Component::rotation_cw_deg)
        .def_rw("mirrored", &Component::mirrored)
        .def_rw("pin_nets", &Component::pin_nets);
    nb::class_<NetClass>(m, "NetClass")
        .def(nb::init<>())
        .def_rw("name", &NetClass::name)
        .def_rw("cset", &NetClass::cset);
    nb::class_<Net>(m, "Net")
        .def(nb::init<>())
        .def_rw("name", &Net::name)
        .def_rw("net_class", &Net::net_class)
        .def_rw("cset", &Net::cset);
    nb::class_<Segment>(m, "Segment")
        .def(nb::init<>())
        .def_rw("a", &Segment::a)
        .def_rw("b", &Segment::b)
        .def_rw("is_arc", &Segment::is_arc)
        .def_rw("center", &Segment::center)
        .def_rw("cw", &Segment::cw)
        .def_rw("width", &Segment::width)
        .def_static("line", &Segment::line, "a"_a, "b"_a, "w"_a)
        .def_static("arc", &Segment::arc, "a"_a, "b"_a, "c"_a, "cw"_a, "w"_a);
    nb::class_<Cline>(m, "Cline")
        .def(nb::init<>())
        .def_rw("layer", &Cline::layer)
        .def_rw("net", &Cline::net)
        .def_rw("segs", &Cline::segs);
    nb::class_<Via>(m, "Via")
        .def(nb::init<>())
        .def_rw("padstack", &Via::padstack)
        .def_rw("net", &Via::net)
        .def_rw("pos", &Via::pos)
        .def_rw("from_layer", &Via::from_layer)
        .def_rw("to_layer", &Via::to_layer);
    nb::class_<Shape>(m, "Shape")
        .def(nb::init<>())
        .def_rw("layer", &Shape::layer)
        .def_rw("net", &Shape::net)
        .def_rw("outline", &Shape::outline)
        .def_rw("voids", &Shape::voids);

    // ------------------------------------------------------------ constraints
    nb::class_<SpacingMatrix>(m, "SpacingMatrix")
        .def(nb::init<>())
        .def("set", &SpacingMatrix::set, "a"_a, "b"_a, "c"_a)
        .def("get", &SpacingMatrix::get, "a"_a, "b"_a);
    nb::class_<CSet>(m, "CSet")
        .def(nb::init<>())
        .def_rw("name", &CSet::name)
        .def_rw("width_min", &CSet::width_min)
        .def_rw("width_max", &CSet::width_max)
        .def_rw("spacing", &CSet::spacing);

    // ----------------------------------------------------------- Transaction
    nb::class_<Transaction>(m, "Transaction")
        .def("create_layer", &Transaction::create_layer)
        .def("create_padstack", &Transaction::create_padstack)
        .def("create_symbol", &Transaction::create_symbol)
        .def("create_component", &Transaction::create_component)
        .def("create_net", &Transaction::create_net)
        .def("create_net_class", &Transaction::create_net_class)
        .def("create_cline", &Transaction::create_cline)
        .def("create_via", &Transaction::create_via)
        .def("create_shape", &Transaction::create_shape)
        .def("create_cset", &Transaction::create_cset)
        .def("assign_pin", &Transaction::assign_pin)
        .def("set_property", &Transaction::set_property)
        .def("update_cline", &Transaction::update_cline)
        .def("update_via", &Transaction::update_via)
        .def("update_shape", &Transaction::update_shape)
        .def("update_cset", &Transaction::update_cset)
        .def("set_system_cset", &Transaction::set_system_cset)
        .def("erase", &Transaction::erase)
        .def("commit", &Transaction::commit)
        .def("abort", &Transaction::abort);

    // ---------------------------------------------------------------- Design
    nb::class_<Design>(m, "Design")
        .def(nb::init<std::string>(), "name"_a)
        // keep_alive<0,1>: the returned Transaction holds Design*, so the
        // Design must outlive it.
        .def("begin", &Design::begin, "name"_a, nb::keep_alive<0, 1>())
        .def("layer", opt_accessor<&Design::layer>())
        .def("padstack", opt_accessor<&Design::padstack>())
        .def("symbol", opt_accessor<&Design::symbol>())
        .def("component", opt_accessor<&Design::component>())
        .def("net", opt_accessor<&Design::net>())
        .def("net_class", opt_accessor<&Design::net_class>())
        .def("cline", opt_accessor<&Design::cline>())
        .def("via", opt_accessor<&Design::via>())
        .def("shape", opt_accessor<&Design::shape>())
        .def("cset", opt_accessor<&Design::cset>())
        .def("system_cset", &Design::system_cset)
        .def("layer_by_name", &Design::layer_by_name)
        .def("net_by_name", &Design::net_by_name)
        .def("conductor_layers", &Design::conductor_layers)
        .def("pin_position", &Design::pin_position)
        .def("get_property", &Design::get_property)
        .def("undo", &Design::undo)
        .def("redo", &Design::redo)
        .def("undo_depth", &Design::undo_depth)
        .def("redo_depth", &Design::redo_depth);

    // ---------------------------------------------------------------- engines
    nb::class_<NetStatus>(m, "NetStatus")
        .def_ro("cluster_count", &NetStatus::cluster_count)
        .def_ro("open_pairs", &NetStatus::open_pairs)
        .def("__repr__", [](const NetStatus& s) {
            return "NetStatus(cluster_count=" + std::to_string(s.cluster_count) +
                   ", open_pairs=" + std::to_string(s.open_pairs) + ")";
        });
    nb::class_<RatsEdge>(m, "RatsEdge")
        .def_ro("a", &RatsEdge::a)
        .def_ro("b", &RatsEdge::b);
    nb::class_<Violation>(m, "Violation")
        .def_ro("a", &Violation::a)
        .def_ro("b", &Violation::b)
        .def_ro("layer", &Violation::layer)
        .def_ro("measured", &Violation::measured)
        .def_ro("required", &Violation::required)
        .def_ro("location", &Violation::location);

    // keep_alive<1, N>: the engine (self, arg 1) holds a raw pointer to its
    // constructor argument N, so that argument must outlive the engine.
    nb::class_<SpatialIndex>(m, "SpatialIndex")
        .def(nb::init<Design&>(), "design"_a, nb::keep_alive<1, 2>())
        .def("query_box", &SpatialIndex::query_box, "layer"_a, "box"_a)
        .def("query_clearance", &SpatialIndex::query_clearance,
             "layer"_a, "box"_a, "clearance"_a)
        .def("net_objects", &SpatialIndex::net_objects, "net"_a);
    nb::class_<ConstraintResolver>(m, "ConstraintResolver")
        .def(nb::init<Design&>(), "design"_a, nb::keep_alive<1, 2>())
        .def("spacing", &ConstraintResolver::spacing, "net"_a, "a"_a, "b"_a)
        .def("width_min", &ConstraintResolver::width_min, "net"_a)
        .def("width_max", &ConstraintResolver::width_max, "net"_a)
        .def("max_clearance", &ConstraintResolver::max_clearance);
    nb::class_<Connectivity>(m, "Connectivity")
        .def(nb::init<Design&, SpatialIndex&>(), "design"_a, "index"_a,
             nb::keep_alive<1, 2>(), nb::keep_alive<1, 3>())
        .def("net_status", &Connectivity::net_status, "net"_a)
        .def("ratsnest", &Connectivity::ratsnest, "net"_a)
        .def("clusters", &Connectivity::clusters, "net"_a);
    nb::class_<Drc>(m, "Drc")
        .def(nb::init<Design&, SpatialIndex&, ConstraintResolver&>(),
             "design"_a, "index"_a, "resolver"_a,
             nb::keep_alive<1, 2>(), nb::keep_alive<1, 3>(), nb::keep_alive<1, 4>())
        .def("run_full", &Drc::run_full)
        .def("run_region", &Drc::run_region, "window"_a);

    // ----------------------------------------------------------------- export
    m.def("export_polygons", &export_polygons, "design"_a, "layer"_a, "tol"_a,
          "All conductor geometry on `layer` as polygons (see "
          "export_polygons.h for arc/void conventions).");
}
