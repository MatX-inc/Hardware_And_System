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
