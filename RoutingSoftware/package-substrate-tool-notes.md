# Building a Lightweight Auto-Routing Packaging Substrate Tool (APD-like)

*Conversation notes — saved 2026-06-03*

---

## 1. Data Model & Storage

**In-memory object database, not files-as-truth.** The design is a single root object with typed collections:

```
Design
├── Stackup: Layer[] (conductor / dielectric / mask, thickness, material, εr)
├── Padstacks (templates: per-layer regular/thermal/anti pads + drill + plating)
├── Symbols (footprint defs: pins → padstack refs at offsets, boundaries, silkscreen)
├── Components (symbol instance + refdes + pin→net mapping)
├── Nets → Xnets → DiffPairs → NetClasses / MatchGroups
├── Geometry: Cline (trace; segments+arcs, width per segment), Via, Shape (plane/polygon
│   with voids), Text — every conductor object carries {layer_id, net_id}
└── Properties: typed key-value store attachable to any object (this is how Allegro does
    nearly everything — constraints, fab notes, room assignments are all properties)
```

Key decisions:

- **Integer coordinates** on a database-unit grid (e.g., 1 nm like OpenAccess/KLayout). Floating point kills boolean ops and DRC robustness. Snap on input, never accumulate FP error.
- **Arcs as first-class primitives**, not polygonized. Package routing is arc-heavy (river routing, bond finger fan-in). Store `(start, end, center, cw/ccw)`; polygonize only at output/render time.
- **Stable handles, not pointers**: generational IDs (`index + generation`) in arena/slab allocators. Everything references by ID — survives undo, makes Python bindings safe, enables an ECS-flavored layout where geometry lives in contiguous arrays (cache-friendly for renderer and DRC).
- **Persistence**: versioned binary via FlatBuffers/Cap'n Proto (mmap-able, zero-copy load — APD's `.sip`/`.mcm` open instantly for the same reason), or SQLite for queryable design files. Interchange: ODB++, IPC-2581, AIF (die bump exchange), GDS/OASIS for the die side.
- **Connectivity is derived, not stored as truth**: net membership is stored, but *physical* connectivity is computed by a connectivity engine (union-find over touching same-net geometry) and cached. Ratsnest = MST over unconnected pin clusters per net, updated incrementally.
- **Undo**: command journal with inverse operations, grouped into transactions. Every mutation goes through the transaction layer — also the dirty-notification source for indexes, DRC, and the renderer.

## 2. Fast Spatial Query

Routing, DRC, snapping, and rendering are all "give me everything near X."

- **R-tree per (layer, object-kind)** — `boost::geometry::index::rtree` (R*) or a packed Hilbert R-tree. Stores `(bbox, handle)`. Bulk-load on file open (STR packing), incremental insert/remove driven by transaction commits.
- **Clearance queries**: query with the bbox inflated by `max_clearance_on_this_layer`, then exact-distance test on candidates. Cache the per-layer max clearance from the constraint system.
- **Corner stitching** (Magic's tile planes) as an alternative/hybrid: brilliant for plane/void-dominated layers and for gridless routers asking "what is the empty space around me" — empty space is explicitly represented as tiles. Hybrid recommendation: R-tree for discrete objects, tiles for the router's working view.
- **Online DRC** during interactive edit: only re-check the dirty region (union of touched bboxes, inflated by max clearance). Full DRC = sweep-line or parallel per-tile checks; dedup violation pairs canonically as `(min_id, max_id)`.
- Net-indexed maps (`net_id → handles`) alongside the spatial index, so "highlight net" and same-net checks bypass geometry.

## 3. Constraints Attached to Geometry

Copy Allegro's Constraint Manager model:

- **Constraint Sets (CSets)** as named, reusable bundles:
  - *Physical CSet*: line width min/max, neck width/length, via list, taper rules
  - *Spacing CSet*: an **object-pair matrix** — line-to-line, line-to-pad, via-to-shape, etc. (~10×10 of object kinds)
  - *Same-net spacing CSet* (separate matrix)
  - *Electrical CSet*: max length, prop delay, relative match (±tolerance within group), max via count, diff-pair gap/uncoupled length
- **Hierarchy with override resolution**: `system default → net class → bus → net → region`. Cache the *resolved* constraint per (net, layer, region); invalidate on constraint edits — resolution must be O(1) at router query time.
- **Constraint regions are geometry**: shapes on a constraint "class" of layers carrying `region_cset=NAME`. They live in the same R-tree, so "what width may I use at point P on layer L for net N" = spatial query for regions at P → highest priority → resolve CSet. This is how APD does BGA-area neckdown rules.
- Constraints are just **properties with a schema** (typed, validated, defined inheritance). One property system serves constraints, fab attributes, and user data.

## 4. Component / Symbol Generation

Chain: **padstack → symbol (footprint) → component instance → logical part binding.**

- **Padstack generator**: parametric (shape, per-layer size, drill, plating, soldermask/paste expansions as derived defaults).
- **Symbol generators** — for packages you can generate nearly everything:
  - *BGA wizard*: rows × cols, pitch, ball padstack, depopulation pattern, JEDEC pin naming (A1..; skip I,O,Q,S,X,Z).
  - *Die generator*: import bump/pad coordinates from a die text file or **AIF** → flip-chip bump array or wirebond pad ring symbol.
  - *Bond finger generator*: parametric finger shape, auto-arranged on arcs/rows around the die with pitch rules.
- **Component = symbol + logical pins**: pin number → logical pin → net, from an imported netlist. Keep symbol (physical) and part (logical) separate.
- Store generator *parameters* on the symbol as properties so regeneration is non-destructive (PCell-style, like gdsfactory/KLayout PCells).

## 5. Routing (Package-Specific)

Package substrate routing ≠ PCB routing:

- Mostly **escape/fan-out routing**: die bumps → fan-out vias → BGA balls. Largely **planar per layer**:
  - *Net ↔ ball assignment + layer assignment*: min-cost flow / bipartite matching.
  - *Detail routing*: **river routing** (planar, ordered nets between two contours — classic O(n) algorithms) for bump-to-via and via-to-ball stages; arcs and any-angle, not Manhattan.
- Gridless **shape-based** detail router on the corner-stitched empty-space view, or fine-grid A* with push-and-shove for messy regions. The constraint resolver supplies width/clearance at every probe.
- Package-specific finishers: trace tapering at bumps, teardrops, plane generation with voiding + thermal ties, degassing hole patterns, plating tail/bus generation for non-ECD flows.

## 6. Python Interface

- **C++ (or Rust) core + nanobind/pybind11**, Python as the scripting/automation layer — the KLayout model. Expose handles + thin wrapper classes, never raw pointers.
- Design the API around **transactions and batch ops**:

```python
with db.transaction("fanout"):
    for pin in comp.pins(net_class="DDR"):
        r = db.route.escape(pin, layer="L2", style="arc")
db.drc.check(region=changed_bbox)
nets = db.query.box((x0,y0,x1,y1), layer="L1", kinds=["cline","via"])  # R-tree, fast
```

- Headless mode from day one (CI, batch DRC, agents). GUI is an observer of the database, not its owner.
- Jupyter-friendly: `design._repr_png_()` inline rendering is cheap to add and transformative for debugging.

## 7. MCP

A thin wrapper over the Python API turns the tool into something an LLM agent can drive:

- **Tools**: `open_design`, `query(bbox/net/refdes)`, `place_component`, `route_net`, `run_drc(region)`, `get_constraints(net)`, `set_constraint`, `screenshot(bbox, layers)`, `undo`.
- **Return structured JSON + rendered PNGs** — image feedback lets an agent verify "did my fan-out look right."
- Guard rails: every tool call = one transaction (atomic, undoable); a `dry_run` flag; DRC summary returned with every mutating call.
- Stateful session (design stays open server-side); MCP resources for netlist/stackup so the agent reads context without tool calls.

**Prior art worth reading**: KLayout (DB + Python binding architecture), Magic (corner stitching), KiCad `pcbnew` + its push-and-shove router (`pns/`), OpenAccess docs (object model), Horizon EDA (clean modern C++ pool/padstack/symbol model), OpenROAD (incremental DRC/routing infra). Escape routing literature: Yan & Wong's ordered escape routing papers; river routing from classic VLSI textbooks.

---

## 8. Viewing: Use an Existing Open-Source Viewer, Not PNGs

Don't build a custom viewer — export to a standard format and push to an existing open-source viewer over a live link.

### Option 1 — KLayout as the live viewer (best fit)

- **Live push, no reload clicking**: KLayout's socket-based remote interface — gdsfactory's `klive` plugin writes GDS/OASIS and pings KLayout, which hot-reloads the cell in an already-open window, preserving zoom and layer visibility. Sub-second round trip.
- **DB → GDS mapping is natural**: layer per (conductor, mask class) via a `.lyp` layer-properties file. Arcs polygonize only at export — the DB stays exact.
- **DRC results**: write violations into a KLayout **RDB (`.lyrdb`)** file → marker browser with categorized violation list and click-to-zoom, for free.
- **Cross-sections**: XSection plugin shows stackup cuts.
- **kweb** (gdsfactory ecosystem) serves the same view in a browser for zero-install viewing.
- Limitation: GDS has no nets — "highlight net" needs per-net cells or KLayout's `l2n` connectivity extraction. Keep the headless PNG path for the MCP agent only.

### Option 2 — KiCad as the viewer (net-aware)

- Emit `.kicad_pcb` (documented s-expressions): get net names, ratsnest, net highlighting, 3D view.
- Downsides: no live-push protocol (file-watch reload), PCB-centric data model maps awkwardly to dies/bond wires, lossy round-trip. Fine as a view-only projection.

### Option 3 — Gerber/ODB++ → gerbv or tracespace

- Free if you have fab output anyway; **gerbv** (desktop) or **tracespace** (web/SVG). Per-layer artwork only — no objects, nets, padstacks. Sanity check, not primary viewer.

**Recommendation**: Option 1 now (~2 days: GDS export + klive ping + `.lyp` + RDB writer); add a `.kicad_pcb` projection later if net-interactive viewing becomes a real ask. Do **not** write a custom OpenGL/WebGL viewer early — it's a tarpit that steals time from the router and constraint system.

---

## 9. The Routing Problem, Mathematically

Routing is not one problem but a stack of coupled combinatorial problems, almost every layer NP-hard.

### 9.1 Single-net core: Steiner trees

Routing one net with ≥3 pins on a graph G=(V,E) with edge costs w and terminals T ⊆ V is the **Steiner Minimal Tree** problem:

    min over S ⊆ E of Σ w(e),  s.t. S spans T

- NP-hard (one of Karp's 21), even in the rectilinear plane (Garey–Johnson 1977).
- 2-pin nets degenerate to shortest path — polynomial (Dijkstra/A*) — which is why routers decompose nets into 2-pin subnets, *sacrificing optimality at the decomposition step itself*.
- Best polynomial approximation ≈ 1.39× optimal (Byrka et al.); rectilinear Steiner has a PTAS (Arora) but it's impractical — hence FLUTE-style lookup heuristics.

### 9.2 Multi-net core: disjoint paths

k nets *simultaneously* sharing the substrate is the **vertex/edge-disjoint paths problem**:

- NP-complete for general k (Karp); stays NP-complete in *planar* graphs when k is part of the input. Fixed k is polynomial (Robertson–Seymour), but with astronomical constants.
- The deep statement: **routing is hard not because each net is hard, but because nets couple through a shared resource.** The feasible region is non-convex and the coupling is combinatorial, not smooth.

### 9.3 Continuous/geometric view

Gridless routing in ℝ² × Layers: find non-crossing curves γᵢ with clearance constraints d(γᵢ, γⱼ) ≥ cᵢⱼ, minimizing Σ αᵢ·len(γᵢ) + β·#vias.

- **Homotopy classes**: once you fix which side of each obstacle/other-net a path goes, the optimal representative within the class is easy (rubber-band / funnel algorithm — the math under topological routers and push-and-shove). The hardness is entirely in choosing the classes — a discrete choice growing exponentially with obstacle count.
  **Routing = (hard combinatorial choice of homotopy classes) ∘ (easy continuous optimization within a class).**
- **Planarity**: single-layer routability of boundary-terminated 2-pin nets = non-crossing of chords — a circle-graph emptiness check, O(n log n).

### 9.4 Resource-allocation view (global routing)

Global routing relaxes to **multicommodity flow** on a coarsened grid: commodity per net, edge capacities = wiring tracks.

- Fractional MCF is an LP — polynomial. **Integral** MCF is NP-hard.
- This explains the standard architecture: global routing solves the LP-ish relaxation (negotiated congestion / PathFinder ≈ dual ascent on capacity constraints, Lagrangian pricing of overuse), detailed routing rounds/legalizes. The integrality gap is where global-route "success" becomes detailed-route failure.

### 9.5 Why package routing is mathematically gentler

- **Escape routing** (bumps → boundary) on a grid is **max-flow** — polynomial (escape targets are interchangeable). Ordered escape is harder but has exact ILP formulations that solve fast at package scale.
- **River routing** (two parallel rows, planar, order-preserving) is solvable exactly in **O(n) / O(n log n)** (Leiserson–Pinter). Bump fan-in and finger fan-out are river routing.
- **Ball/layer assignment** is **bipartite matching / min-cost flow** — polynomial.

A package router, properly architected, is a *pipeline of polynomial problems* (matching → flow → river routing) covering ~90% of nets, with the NP-hard general router needed only for the irregular residue. That justifies not dropping a PCB autorouter onto a substrate.

### 9.6 Constraints make it worse, specifically

Length matching, max via count, diff-pair coupling are **non-local, non-monotone** constraints: they break the optimal-substructure property A*/Dijkstra rely on. Formally: **resource-constrained shortest path** — NP-hard even for one path with one resource bound. Real tools do post-hoc detour insertion instead of constrained search.

### 9.7 Summary table

| Layer | Mathematical problem | Complexity |
|---|---|---|
| 1 net, 2 pins | Shortest path | P |
| 1 net, ≥3 pins | Steiner tree | NP-hard |
| Many nets | Disjoint paths / integral MCF | NP-hard (even planar) |
| Topology choice | Homotopy class selection | exponential discrete choice |
| Global routing | Fractional MCF + rounding | LP + NP-hard rounding |
| Escape routing | Max-flow | **P** |
| River routing | Planar ordered routing | **O(n)** |
| Ball assignment | Min-cost bipartite matching | **P** |
| Length matching | Resource-constrained path | NP-hard |

**One-sentence view**: routing is an integral multicommodity packing problem over a non-convex geometric domain, whose hardness lives entirely in the discrete coupling between nets (homotopy/ordering choices); the craft of building a router is recognizing and isolating the polynomially-solvable substructures — which in packaging substrates cover most of the problem.

---

## 10. "Nonlinear Multi-Constraint Optimization" — How That Framing Fits

The framing is *correct but under-specified*; the gap is exactly where the difficulty lives.

### What it gets right

Routing is an optimization problem with many nonlinear, non-convex constraints (clearance d(γᵢ, γⱼ) ≥ cᵢⱼ is nonlinear in path coordinates). As a formal statement, "nonlinear multi-constraint optimization" is not wrong.

### What it misses

    Routing = (choose discrete structure: homotopy class, net ordering, layer assignment)
              ∘ (optimize geometry within it — continuous, and in fact EASY)

- **Inner problem** (topology fixed): rubber-band/funnel — efficiently solvable, often convex-flavored. If routing were only a nonlinear program, this would be the whole problem, and routing would be tractable.
- **Outer problem** (choose topology): discrete, exponentially many options — **all the NP-hardness sits here**.

Refined statement:

> Routing is a nonlinear multi-constraint optimization problem **whose feasible region is a disjoint union of exponentially many "easy" continuous pieces, one per discrete topology choice — and selecting the piece is the NP-hard part.**

In MINLP language: a **mixed-integer nonlinear program where the integer variables dominate**. The continuous relaxation (fractional MCF) is polynomial; the hardness is entirely in the integrality gap, not the nonlinearity. (Opposite of analog circuit sizing, where variables are genuinely continuous and nonlinearity is the enemy.)

### The test of the distinction

If the nonlinear-program model were sufficient, gradient methods would work (penalty function for overlaps, descend). In practice:

- The penalty landscape has a **local minimum for every homotopy class** — exponentially many, separated by barriers corresponding to a trace "jumping over" an obstacle or another net. Gradient flow cannot change homotopy class.
- Continuous methods solve the inner problem (already easy) and are blind to the outer one (the actual problem).

**Non-convexity here is combinatorial, not smooth** — the feasible set isn't a wrinkly landscape, it's confetti.

### Mapping the words

| Phrase | Precise correspondence |
|---|---|
| "nonlinear" | non-convex clearance/length constraints — true, but the *tractable* part |
| "multi-constraint" | the constraint hierarchy; resource-constrained-path hardness lives here |
| *(missing)* | **discreteness**: homotopy/ordering/assignment — where NP-hardness actually lives |
| *(missing)* | **coupling**: nets share one substrate; no net-by-net decomposition |

### Why this matters for the tool

- "Nonlinear optimization" framing → throw at a solver (IPOPT, penalties, annealing on coordinates). Fails as above.
- "Discrete choice + easy continuous inner" framing → the real architecture: **search/flow/matching to fix the discrete structure** (escape flow, river-routing order, A* over homotopy classes, negotiated congestion), then **geometric cleanup as a cheap post-pass** (push-and-shove, rubber-band relaxation, length-tuning detours).

Amended framing: **"a mixed-integer, geometrically-coupled optimization problem — nonlinear in its easy direction, combinatorial in its hard one."**
