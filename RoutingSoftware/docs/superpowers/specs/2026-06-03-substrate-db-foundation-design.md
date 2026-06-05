# Substrate Database Foundation (v1) — Design Spec

Date: 2026-06-03
Status: Approved pending user review

## Purpose

Build the foundation of a lightweight, scriptable packaging-substrate design tool (APD-like): a C++ in-memory object database with Python bindings, spatial indexing, transactions/undo, a constraint system, connectivity and DRC engines, and live viewing through KLayout. Routing algorithms, persistence, and the MCP agent interface are deliberately out of scope for v1 — this release proves the core architecture on synthetic generated designs.

Reference background: `package-substrate-tool-notes.md` (repo root) — the conversation notes this design distills.

## v1 Goal / Acceptance Criterion

A single demo script (`examples/demo_flipchip_bga.py`) that:

1. Generates a small flip-chip die (20×20 bump array) over a BGA substrate (30×30 ball array) using the parametric generators.
2. Adds hand-placed clines and vias, some deliberately violating spacing constraints.
3. Live-pushes the design to a running KLayout window (GDS + `.lyp` layer properties).
4. Runs full-design DRC; violations appear in KLayout's marker browser via `.lyrdb`.
5. Prints connectivity/ratsnest statistics (clusters per net, unconnected pair count).

The demo passing end-to-end, with all unit and integration tests green, defines v1 complete.

## Architecture Decision

**C++ kernel + Python services** (chosen over maximal-C++ and Python-first alternatives):

- **C++ (namespace `sdb`, bound via nanobind):** object database, generational handles, integer geometry kernel with first-class arcs, R-tree spatial indexes, transaction/undo journal, constraint resolution, union-find connectivity, DRC inner loops. Everything where performance, correctness, or architectural lock-in lives.
- **Python (package `substrate`):** BGA/die generators, GDS export (via `gdstk`, polygons supplied by the kernel), KLayout live push, `.lyp` and `.lyrdb` writers, demo scripts. Easily replaceable glue; can migrate into C++ later without API change.

## 1. Repository Layout & Build

```
RoutingSoftware/
├── CMakeLists.txt              # C++20, builds sdb core lib + nanobind module
├── pyproject.toml              # scikit-build-core; `pip install -e .` builds all
├── src/sdb/                    # C++ core (headers + impl)
├── bindings/                   # nanobind module `_sdb`
├── python/substrate/           # Python package (wraps _sdb)
├── tests/cpp/                  # Catch2 unit tests
├── tests/python/               # pytest integration tests
├── examples/                   # demo scripts
└── docs/superpowers/specs/     # this spec; plans live alongside
```

- Toolchain: C++20, CMake + Ninja, nanobind, Catch2 (C++ tests), pytest (Python tests), gdstk (GDS writing).
- The directory becomes a git repository; all work happens on a feature branch per the development workflow.

## 2. Data Model (C++)

**Coordinates:** `int64` database units, 1 nm grid. All input snapped on entry; no floating-point accumulation. Floating point appears only in derived computations (arc evaluation, distance results).

**Object kinds**, each stored in its own arena (slab) with contiguous storage:

| Kind | Contents |
|---|---|
| `Layer` | name, type (conductor / dielectric / mask), stackup order, thickness, material, εr |
| `Padstack` | per-layer pad geometry (shape + size), drill diameter, plating |
| `Symbol` | pin definitions (padstack ref + offset + pin number), boundary outline |
| `Component` | symbol ref, refdes, placement transform, pin→net map |
| `Net` | name; membership of conductor objects (derived index, see §7) |
| `Cline` | ordered path of segments and arcs, width per segment, `{layer_id, net_id}` |
| `Via` | padstack ref, position, `{from_layer, to_layer, net_id}` |
| `Shape` | polygon outline + void list, `{layer_id, net_id}` |

**Handles:** every object is referenced by a generational handle `{kind, index, generation}`. Arena slots are reused with bumped generation; stale handles are detected, never dereferenced. No raw pointers cross the public API. Handles are the currency of the Python bindings, indexes, undo journal, and DRC results.

**Arcs are first-class:** stored as `(start, end, center, cw/ccw)`. Never polygonized inside the database; polygonization (with explicit sag tolerance) happens only at export/render.

**Properties:** a typed key→value store (int / double / string / bool / handle) attachable to any handle. One mechanism serves user data and future constraint extensions.

## 3. Transactions & Undo

- Every mutation goes through a `Transaction` object obtained from the `Design`. Direct mutation outside a transaction is an API error.
- Each primitive command records its inverse; commit groups commands into a single named undo step (command journal).
- Commit emits **dirty notifications**: the set of touched handles and the union bbox per affected layer (inflated by that layer's max clearance). Subscribers: R-tree maintenance, connectivity engine, DRC region invalidation.
- `undo()` / `redo()` replay inverses/commands through the same notification path, so all indexes stay consistent.

## 4. Spatial Index

- One R-tree per (layer, object kind), storing `(bbox, handle)`. Implementation: `boost::geometry::index::rtree` with R* balancing.
- Bulk population uses STR packing (fast initial load); incremental insert/remove driven by transaction commit notifications.
- Query API: box query, point query, and clearance query (search box inflated by per-layer max clearance from the constraint system, returning candidates for exact testing).
- A net index (`net_id → handle set`) is maintained alongside, so net-based lookups bypass geometry.

## 5. Geometry Kernel

- Exact integer/rational predicates for segment–segment width-aware distance (capsule distance), point containment, bbox computation.
- Arcs: exact representation; distance involving arcs uses conservative bounds tightened by bounded-error numeric evaluation — sufficient for DRC (report gap vs. required with tolerance far below 1 dbu).
- Polygonization: `polygonize(tolerance) → polygon` for clines (width outline including arc sections), pads, and shapes; used by export only.

## 6. Constraint System

- **Physical CSet:** line width min/max. **Spacing CSet:** object-pair matrix (line, pad, via, shape × same) of minimum clearances.
- **Hierarchy:** `system default → net class → net`. Each level may override individual values; resolution walks up the chain.
- **Resolved-constraint cache:** per (net, layer) resolved values cached for O(1) router/DRC queries; invalidated by constraint-edit transactions.
- Per-layer max clearance is cached for R-tree query inflation.
- **Deferred to v2:** constraint regions (region CSets as geometry), electrical/diff-pair constraints. The resolution chain is designed so a region level can be inserted without API breakage.

## 7. Connectivity Engine

- Physical connectivity is **derived, not stored**: union-find over touching same-net conductor geometry (cline↔cline, cline↔via, via↔shape, pin pads included), using the spatial index to find touch candidates.
- Incremental: dirty-net recompute on transaction commit (v1 recomputes the whole net's union-find when any member changes — simple and correct; finer incrementality is a v2 optimization).
- **Ratsnest:** per net, MST over cluster representatives of unconnected clusters; exposed as pin-pair list. Summary statistics (cluster count, open pair count) queryable from Python.

## 8. DRC

- Spacing check: for each conductor object in scope, clearance-inflated R-tree query → exact distance test against resolved spacing constraints → violation `(min_handle, max_handle, layer, measured_gap, required_gap)`; canonical ordering dedups pairs.
- Same-net contacts are skipped in v1 (same-net spacing CSets are v2).
- Modes: **region** (dirty bbox from a transaction) and **full design**.
- Output: violation list in Python, plus a `.lyrdb` writer so violations land in KLayout's marker browser with category, description, and click-to-zoom geometry.

## 9. Python Layer (`substrate` package)

- Wraps the nanobind `_sdb` module with a Pythonic API:

```python
db = substrate.Design("demo")
with db.transaction("fanout"):
    cl = db.add_cline(layer="L1", net="DDR0", width=um(20), points=[...])
hits = db.query_box((x0, y0, x1, y1), layer="L1", kinds=["cline", "via"])
viol = db.drc.run()                  # full design
db.undo()
```

- **Generators** (parameters recorded as properties on the symbol for non-destructive regeneration):
  - `bga_symbol(rows, cols, pitch, padstack, depopulation=...)` — JEDEC naming (A1.., skipping I, O, Q, S, X, Z).
  - `die_symbol(rows, cols, pitch, bump_padstack)` — flip-chip bump array.
- **KLayout integration:**
  - GDS export: layer per conductor/mask layer via a generated `.lyp` file; arcs polygonized at configured tolerance; gdstk writes the file.
  - Live push: write GDS to a temp path, ping a running KLayout over its socket remote interface (klive protocol) to hot-reload while preserving zoom/visibility.
  - `.lyrdb` DRC marker export (§8).

## 10. Testing Strategy

- **TDD throughout** (failing test → implement → green → commit), enforced by the subagent workflow.
- C++ (Catch2): geometry predicates (including arc edge cases and integer-overflow guards), handle/generation semantics, undo/redo round-trips, R-tree consistency under mutation, constraint resolution and cache invalidation, union-find correctness, DRC pair generation.
- Python (pytest): generator correctness (counts, naming, depopulation), end-to-end demo pipeline, GDS golden-file comparison, `.lyrdb` content checks.
- KLayout live-push is exercised by the demo (manual verification); CI-safe tests stub the socket.

## 11. Out of Scope for v1

Persistence/save-load, all routing (escape, river, detail), MCP server, constraint regions, same-net spacing, electrical/diff-pair constraints, netlist import, AIF import, corner stitching, bond-finger generator, custom viewer of any kind.

## Open Questions

None — all v1 decisions above were made during brainstorming on 2026-06-03.
