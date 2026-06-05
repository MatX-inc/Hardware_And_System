#pragma once
#include "sdb/geom/types.h"
#include <array>
#include <cstdint>
#include <optional>
#include <string>
namespace sdb {

// Object-kind axis for the spacing matrix. Distinct from sdb::Kind: these are
// the DRC-relevant geometry categories, not arena kinds.
enum class ObjKind : uint8_t { Line, Pad, Via, Shape, COUNT };

// Pairwise minimum-spacing rules. Full COUNT×COUNT array; only canonical
// (min,max) cells are written, giving symmetric access: set(Line, Via, x)
// makes get(Via, Line) == x. Unset cells (nullopt) mean "not engaged" — they
// inherit from the next constraint set up the hierarchy.
struct SpacingMatrix {
    std::array<std::optional<Coord>,
               size_t(ObjKind::COUNT) * size_t(ObjKind::COUNT)> v{};
    void set(ObjKind a, ObjKind b, Coord c) { v[idx_(a, b)] = c; }
    std::optional<Coord> get(ObjKind a, ObjKind b) const { return v[idx_(a, b)]; }
private:
    static size_t idx_(ObjKind a, ObjKind b) {
        auto lo = std::min(uint8_t(a), uint8_t(b));
        auto hi = std::max(uint8_t(a), uint8_t(b));
        return size_t(lo) * size_t(ObjKind::COUNT) + size_t(hi);
    }
};

// A constraint set. Every field is optional ("engaged" when set); resolution
// overlays system -> net-class -> net, later levels overriding only the
// fields they engage.
struct CSet {
    std::string name;
    std::optional<Coord> width_min, width_max;
    SpacingMatrix spacing;
};
}  // namespace sdb
