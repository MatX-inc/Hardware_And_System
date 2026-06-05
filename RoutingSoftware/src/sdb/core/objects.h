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
