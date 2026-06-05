#include "sdb/geom/kernel.h"
#include <algorithm>

// All distance math is done in double on int64 inputs. A double's 53-bit
// mantissa represents integers exactly up to 2^53; v1 designs are far below
// 2^50 dbu in extent, so coordinate differences and projections stay exact
// enough (errors << 1 dbu). Exact sign tests (orientation, point-in-poly)
// use __int128 to avoid any rounding ambiguity.

namespace sdb::geom {

namespace {

constexpr double kPi = 3.14159265358979323846;

double dx(Point a, Point b) { return double(b.x) - double(a.x); }
double dy(Point a, Point b) { return double(b.y) - double(a.y); }

// Exact orientation sign of cross(b-a, c-a) using 128-bit integers.
int orient(Point a, Point b, Point c) {
    __int128 cross = (__int128)(b.x - a.x) * (c.y - a.y) -
                     (__int128)(b.y - a.y) * (c.x - a.x);
    return cross > 0 ? 1 : (cross < 0 ? -1 : 0);
}

// Number of chords needed so the sag of each chord on a circle of radius r
// stays within tol, over a sweep angle (radians).
int chord_count(double r, double tol, double sweep) {
    if (!(tol > 0)) tol = 1.0;  // guard tol<=0/NaN: ceil(sweep/0) is UB
    if (r <= tol) return 1;                      // arc smaller than tolerance
    double max_step = 2.0 * std::acos(1.0 - tol / r);
    int n = (int)std::ceil(sweep / max_step);
    return std::max(n, 1);
}

// Corrupt inputs must not produce silently-inverted geometry or inflated gaps.
Coord clamp_width(Coord w) { return std::max<Coord>(w, 0); }

Point round_pt(double x, double y) {
    return {(Coord)std::llround(x), (Coord)std::llround(y)};
}

// Append interior points of a CCW arc around (cx,cy) from angle a0 sweeping
// `sweep` (positive = CCW, negative = CW); endpoints are NOT emitted.
void append_arc_interior(std::vector<Point>& out, double cx, double cy,
                         double r, double a0, double sweep, double tol) {
    int n = chord_count(r, tol, std::abs(sweep));
    for (int k = 1; k < n; ++k) {
        double t = a0 + sweep * (double(k) / n);
        out.push_back(round_pt(cx + r * std::cos(t), cy + r * std::sin(t)));
    }
}

double min_polyline_segment_dist(const std::vector<Point>& poly,
                                 Point a, Point b) {
    double best = std::numeric_limits<double>::infinity();
    for (size_t i = 0; i + 1 < poly.size(); ++i)
        best = std::min(best, dist_segment_segment(poly[i], poly[i+1], a, b));
    return best;
}

} // namespace

double dist_point_segment(Point p, Point a, Point b) {
    double abx = dx(a, b), aby = dy(a, b);
    double apx = dx(a, p), apy = dy(a, p);
    double len2 = abx * abx + aby * aby;
    double t = len2 > 0.0 ? std::clamp((apx * abx + apy * aby) / len2, 0.0, 1.0)
                          : 0.0;
    double cx = double(a.x) + t * abx, cy = double(a.y) + t * aby;
    return std::hypot(double(p.x) - cx, double(p.y) - cy);
}

double dist_segment_segment(Point a1, Point b1, Point a2, Point b2) {
    int o1 = orient(a1, b1, a2), o2 = orient(a1, b1, b2);
    int o3 = orient(a2, b2, a1), o4 = orient(a2, b2, b1);
    if (o1 * o2 < 0 && o3 * o4 < 0) return 0.0;  // proper crossing
    // Otherwise (incl. collinear/touching) the minimum is attained at an
    // endpoint of one segment against the other segment.
    return std::min(std::min(dist_point_segment(a2, a1, b1),
                             dist_point_segment(b2, a1, b1)),
                    std::min(dist_point_segment(a1, a2, b2),
                             dist_point_segment(b1, a2, b2)));
}

double gap_capsule_capsule(Point a1, Point b1, Coord w1,
                           Point a2, Point b2, Coord w2) {
    w1 = clamp_width(w1); w2 = clamp_width(w2);
    double d = dist_segment_segment(a1, b1, a2, b2);
    return std::max(0.0, d - (double(w1) + double(w2)) / 2.0);
}

std::vector<Point> sample_arc(Point a, Point b, Point center, bool cw,
                              double tol) {
    double cx = double(center.x), cy = double(center.y);
    double r = std::hypot(double(a.x) - cx, double(a.y) - cy);
    double ang_a = std::atan2(double(a.y) - cy, double(a.x) - cx);
    double ang_b = std::atan2(double(b.y) - cy, double(b.x) - cx);
    // Sweep magnitude in direction of travel, normalized into (0, 2*pi].
    double sweep = cw ? ang_a - ang_b : ang_b - ang_a;
    while (sweep <= 0.0) sweep += 2.0 * kPi;
    int n = chord_count(r, tol, sweep);          // n chords -> n+1 points
    std::vector<Point> pts;
    pts.reserve(size_t(n) + 1);
    pts.push_back(a);                            // exact endpoint
    double dir = cw ? -1.0 : 1.0;
    append_arc_interior(pts, cx, cy, r, ang_a, dir * sweep, tol);
    pts.push_back(b);                            // exact endpoint
    return pts;
}

double gap_segment_arc(Point a1, Point b1, Coord w1,
                       Point arc_a, Point arc_b, Point center, bool cw,
                       Coord w2) {
    w1 = clamp_width(w1); w2 = clamp_width(w2);
    auto poly = sample_arc(arc_a, arc_b, center, cw, kArcDrcTol);
    double d = min_polyline_segment_dist(poly, a1, b1);
    // The chord polyline lies inside the true arc, so the sampled distance can
    // overestimate the true gap by up to the sag bound; subtract it to stay
    // conservative (returned gap <= true gap).
    return std::max(0.0, d - (double(w1) + double(w2)) / 2.0 - kArcDrcTol);
}

double gap_arc_arc(Point a1, Point b1, Point c1, bool cw1, Coord w1,
                   Point a2, Point b2, Point c2, bool cw2, Coord w2) {
    w1 = clamp_width(w1); w2 = clamp_width(w2);
    auto p1 = sample_arc(a1, b1, c1, cw1, kArcDrcTol);
    auto p2 = sample_arc(a2, b2, c2, cw2, kArcDrcTol);
    double best = std::numeric_limits<double>::infinity();
    for (size_t i = 0; i + 1 < p2.size(); ++i)
        best = std::min(best, min_polyline_segment_dist(p1, p2[i], p2[i+1]));
    // Two sampled arcs: each polyline sits up to one sag bound inside its true
    // arc, so subtract 2*tol to keep the result conservative (<= true gap).
    return std::max(0.0, best - (double(w1) + double(w2)) / 2.0
                             - 2.0 * kArcDrcTol);
}

std::vector<Point> polygonize_circle(Point center, Coord diameter,
                                     double tol) {
    diameter = clamp_width(diameter);
    double r = double(diameter) / 2.0;
    double full = 2.0 * kPi;
    int n = std::max(8, chord_count(r, tol, full));
    std::vector<Point> poly;
    poly.reserve(size_t(n));
    for (int k = 0; k < n; ++k) {
        double t = full * (double(k) / n);
        poly.push_back(round_pt(double(center.x) + r * std::cos(t),
                                double(center.y) + r * std::sin(t)));
    }
    return poly;
}

std::vector<Point> polygonize_capsule(Point a, Point b, Coord width,
                                      double tol) {
    width = clamp_width(width);
    if (a == b) return polygonize_circle(a, width, tol);
    double r = double(width) / 2.0;
    double ux = dx(a, b), uy = dy(a, b);
    double len = std::hypot(ux, uy);
    ux /= len; uy /= len;                        // unit direction a -> b
    double nx = -uy, ny = ux;                    // unit left normal
    // CCW outline. Cap apexes are emitted exactly (llround of a - dir*r and
    // b + dir*r) so axis-aligned capsules have an exact bounding box.
    Point a_bot = round_pt(double(a.x) - nx * r, double(a.y) - ny * r);
    Point b_bot = round_pt(double(b.x) - nx * r, double(b.y) - ny * r);
    Point a_top = round_pt(double(a.x) + nx * r, double(a.y) + ny * r);
    Point b_top = round_pt(double(b.x) + nx * r, double(b.y) + ny * r);
    Point apex_a = round_pt(double(a.x) - ux * r, double(a.y) - uy * r);
    Point apex_b = round_pt(double(b.x) + ux * r, double(b.y) + uy * r);
    double ang_bot = std::atan2(-ny, -nx);       // angle of -n from a cap center
    double half = kPi / 2.0;
    std::vector<Point> poly;
    poly.push_back(a_bot);
    poly.push_back(b_bot);
    // Cap around b: -n -> apex -> +n, CCW (quarter + quarter).
    append_arc_interior(poly, double(b.x), double(b.y), r, ang_bot, half, tol);
    poly.push_back(apex_b);
    append_arc_interior(poly, double(b.x), double(b.y), r, ang_bot + half, half, tol);
    poly.push_back(b_top);
    poly.push_back(a_top);
    // Cap around a: +n -> apex -> -n, CCW.
    append_arc_interior(poly, double(a.x), double(a.y), r, ang_bot + 2*half, half, tol);
    poly.push_back(apex_a);
    append_arc_interior(poly, double(a.x), double(a.y), r, ang_bot + 3*half, half, tol);
    return poly;                                 // closed implicitly (last -> first)
}

Box poly_bbox(const std::vector<Point>& poly) {
    Box bb = Box::empty();
    for (const Point& p : poly) bb = bb.united(Box{p, p});
    return bb;
}

Box arc_bbox(Point a, Point b, Point center, bool cw) {
    Box bb = Box::of(a, b);
    double cx = double(center.x), cy = double(center.y);
    double r = std::hypot(double(a.x) - cx, double(a.y) - cy);
    double ang_a = std::atan2(double(a.y) - cy, double(a.x) - cx);
    double ang_b = std::atan2(double(b.y) - cy, double(b.x) - cx);
    // Same normalization as sample_arc: sweep magnitude in direction of
    // travel, in (0, 2*pi].
    double sweep = cw ? ang_a - ang_b : ang_b - ang_a;
    while (sweep <= 0.0) sweep += 2.0 * kPi;
    // Axis-extreme points: angles 0, pi/2, pi, 3*pi/2. Unite each one whose
    // angle lies within the sweep starting at ang_a in the travel direction.
    for (int k = 0; k < 4; ++k) {
        double t = k * (kPi / 2.0);
        double delta = cw ? ang_a - t : t - ang_a;
        while (delta < 0.0) delta += 2.0 * kPi;
        while (delta >= 2.0 * kPi) delta -= 2.0 * kPi;
        if (delta <= sweep) {
            Point e = round_pt(cx + r * std::cos(t), cy + r * std::sin(t));
            bb = bb.united(Box{e, e});
        }
    }
    return bb;
}

bool point_in_poly(Point p, const std::vector<Point>& poly) {
    // Even-odd ray cast with exact 128-bit integer arithmetic.
    bool in = false;
    size_t n = poly.size();
    for (size_t i = 0, j = n - 1; i < n; j = i++) {
        const Point& pi = poly[i];
        const Point& pj = poly[j];
        if ((pi.y > p.y) != (pj.y > p.y)) {
            // Edge crosses the horizontal line through p; test whether the
            // crossing is strictly to the right of p:
            //   p.x < pj.x + (p.y - pj.y) * (pi.x - pj.x) / (pi.y - pj.y)
            __int128 lhs = (__int128)(p.x - pj.x) * (pi.y - pj.y);
            __int128 rhs = (__int128)(pi.x - pj.x) * (p.y - pj.y);
            if (pi.y > pj.y ? lhs < rhs : lhs > rhs) in = !in;
        }
    }
    return in;
}

} // namespace sdb::geom
