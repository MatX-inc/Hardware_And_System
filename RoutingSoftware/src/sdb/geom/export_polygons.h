#pragma once
#include "sdb/core/design.h"
#include <vector>
namespace sdb {
// All conductor geometry on `layer` as polygons (closed, implicit last->first
// edge), suitable for GDS export and rendering. Objects are visited in a
// deterministic kind order (Cline, Via, Shape, Component), each kind in arena
// handle order. Notes:
//   - Arc segments are emitted as a chain of per-edge capsule polygons around
//     the sampled centerline (overlapping polygons union visually / in GDS;
//     no boolean ops in v1).
//   - Shape outlines pass through unchanged; voids are appended after their
//     outline as separate reversed-winding polygons (downstream consumers
//     treat them as independent polygons in v1).
std::vector<std::vector<Point>> export_polygons(const Design&, Handle layer,
                                                double tol);
}
