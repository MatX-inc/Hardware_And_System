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
    // Ordering for use as std::map key (e.g. Padstack::pads); compares
    // kind, then index, then generation. Not a semantic ordering.
    bool operator<(const Handle& o) const {
        if (kind != o.kind) return kind < o.kind;
        if (index != o.index) return index < o.index;
        return generation < o.generation;
    }
    bool valid() const { return generation != 0; }   // generation 0 == null handle
};
inline constexpr Handle kNullHandle{};
}
template<> struct std::hash<sdb::Handle> {
    size_t operator()(const sdb::Handle& h) const {
        return (size_t(h.kind) << 56) ^ (size_t(h.generation) << 32) ^ h.index;
    }
};
