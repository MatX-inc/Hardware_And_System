#pragma once
#include "sdb/geom/types.h"
#include <vector>
#include <cmath>
namespace sdb::geom {
double dist_point_segment(Point p, Point a, Point b);
double dist_segment_segment(Point a1, Point b1, Point a2, Point b2);
// gap between two width-carrying centerline segments (capsules); clamped >= 0
double gap_capsule_capsule(Point a1, Point b1, Coord w1, Point a2, Point b2, Coord w2);
// arc handled by sampling; sampled gap is reduced by the sag error bound so
// the returned value is conservative (<= true gap); error at most 2*tol
double gap_segment_arc(Point a1, Point b1, Coord w1,
                       Point arc_a, Point arc_b, Point center, bool cw, Coord w2);
double gap_arc_arc(Point a1, Point b1, Point c1, bool cw1, Coord w1,
                   Point a2, Point b2, Point c2, bool cw2, Coord w2);
std::vector<Point> sample_arc(Point a, Point b, Point center, bool cw, double tol);
std::vector<Point> polygonize_capsule(Point a, Point b, Coord width, double tol);
std::vector<Point> polygonize_circle(Point center, Coord diameter, double tol);
// Empty input returns Box::empty().
Box poly_bbox(const std::vector<Point>& poly);
// Exact bbox of a zero-width arc centerline: endpoints united with any of the
// four axis-extreme points (E/N/W/S of center) the sweep passes through.
// Extreme points are rounded with llround (off by <1 dbu); inflate by width/2
// for a width-carrying arc.
Box arc_bbox(Point a, Point b, Point center, bool cw);
// Points exactly on the boundary: result is deterministic but unspecified —
// do not rely on edge/vertex membership.
bool point_in_poly(Point p, const std::vector<Point>& poly);
inline constexpr double kArcDrcTol = 1.0;   // dbu
}
